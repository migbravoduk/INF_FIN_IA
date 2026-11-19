"""
db/repos/eeff.py — EEFF corporativos IFRS (archivo plano CMF), ratios y evolución.

Mixin de `db.database.Database`: los métodos usan `self.conn` (conexión DuckDB) y se
acceden como siempre, `Database().<método>`. Separado de database.py por dominio (Fase 5
del plan) sin cambiar el código de ningún método.
"""

import logging
import re as _re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from db.common import (is_total_account, AGF_SQL_PREDICATE, _agf_where, is_agf_name,
                       compute_ratios, _TOTAL_RE, fast_executemany)

logger = logging.getLogger("db.database")


class EeffRepo:

    # ----------------------------------------------------------
    # Estados Financieros CMF (Fase 3)
    # ----------------------------------------------------------

    def insert_cmf_records(self, period: int, records: list[dict]) -> int:
        """
        Inserta estados financieros corporativos de la CMF usando "Delete-then-Insert".
        Ejecuta la operación completa de forma transaccional y atómica.
        Retorna la cantidad de registros insertados.
        """
        if not records:
            return 0

        # Convertir a tuplas limpias para ejecutemany
        tuples_data = [
            (
                int(rec['period']),
                str(rec['rut']).strip().replace(".", "").replace("-", ""),
                str(rec['company_name']).strip(),
                str(rec['report_type']).strip(),
                str(rec['currency']).strip(),
                str(rec['account_name']).strip(),
                float(rec['value']),
                rec.get('taxonomy_code'),
                rec.get('statement_group')
            )
            for rec in records
        ]

        # Obtener los períodos únicos presentes en el lote de registros
        unique_periods = list(set(int(rec['period']) for rec in records))

        # Iniciar transacción explícita
        self.conn.execute("BEGIN TRANSACTION")
        try:
            # 1. Eliminar datos existentes de forma atómica para cada período presente en el lote
            for p in unique_periods:
                self.conn.execute("DELETE FROM cmf_financial_statements WHERE period = ?", [p])

            # 2. Bulk insert usando la eficiencia nativa de DuckDB executemany
            fast_executemany(self.conn, """
                INSERT INTO cmf_financial_statements 
                    (period, rut, company_name, report_type, currency, account_name, value, taxonomy_code, statement_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)

            self.conn.execute("COMMIT")
            logger.info(f"Ingestados con éxito {len(records)} registros CMF para los períodos {unique_periods}.")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al ingestar registros CMF para el período {period}. Transacción revertida.", exc_info=True)
            raise e

    def get_cmf_companies(self, agf: Optional[bool] = None):
        """Empresas (RUT + Razón Social) de los EEFF corporativos.
        `agf`: None=todas · False=excluye AGF · True=solo AGF (ver AGF_SQL_PREDICATE)."""
        return self.conn.execute(f"""
            SELECT DISTINCT rut, company_name
            FROM cmf_financial_statements
            {_agf_where(agf)}
            ORDER BY company_name ASC
        """).fetchdf()

    def get_cmf_default_company(self, agf: Optional[bool] = None) -> Optional[str]:
        """Empresa con que abre una vista: la de mayores ingresos del último período entre
        las que tienen ≥ 24 trimestres (la primera alfabética solía no tener datos)."""
        row = self.conn.execute(f"""
            WITH n AS (SELECT rut, COUNT(DISTINCT period) AS n FROM cmf_financial_statements
                       GROUP BY rut),
                 u AS (SELECT MAX(period) AS p FROM cmf_financial_statements)
            SELECT f.company_name FROM cmf_financial_statements f JOIN n USING (rut), u
            WHERE f.period = u.p AND n.n >= 24
              AND f.account_name = 'Ingresos de actividades ordinarias'
              {_agf_where(agf, 'AND')}
            ORDER BY f.value DESC LIMIT 1
        """).fetchone()
        return row[0] if row else None

    def search_cmf_companies(self, query: str, limit: int = 15, agf: Optional[bool] = None):
        """
        Busca empresas por nombre para el autocompletar del selector EEFF.
        Rankea prefijo > substring y deduplica por RUT quedándose con el nombre más
        reciente (mayor `period`) de cada empresa. Devuelve DataFrame (rut, company_name).
        `agf`: None=todas · False=excluye AGF · True=solo AGF.
        """
        q = (query or "").strip().lower()
        if len(q) < 2:
            # Devuelve estructura vacía con las columnas esperadas.
            return self.conn.execute(
                "SELECT rut, company_name FROM cmf_financial_statements WHERE 1=0"
            ).fetchdf()
        like_sub = f"%{q}%"
        like_pre = f"{q}%"
        return self.conn.execute(f"""
            WITH matches AS (
                SELECT rut, company_name, period,
                       ROW_NUMBER() OVER (PARTITION BY rut ORDER BY period DESC) AS rn
                FROM cmf_financial_statements
                WHERE LOWER(company_name) LIKE ?{_agf_where(agf, 'AND')}
            )
            SELECT rut, company_name
            FROM matches
            WHERE rn = 1
            ORDER BY CASE WHEN LOWER(company_name) LIKE ? THEN 0 ELSE 1 END ASC,
                     company_name ASC
            LIMIT ?
        """, [like_sub, like_pre, limit]).fetchdf()

    def get_cmf_periods(self) -> list[int]:
        """Lista de períodos (YYYYMM) disponibles en EEFF corporativos, más reciente primero."""
        rows = self.conn.execute("""
            SELECT DISTINCT period FROM cmf_financial_statements ORDER BY period DESC
        """).fetchall()
        return [int(r[0]) for r in rows]

    def query_cmf_statements(
        self,
        rut: Optional[str] = None,
        company: Optional[str] = None,
        period: Optional[int] = None,
        limit: int = 50
    ):
        """Recupera estados financieros de CMF en base a filtros flexibles como DataFrame."""
        query = """
            SELECT period, rut, company_name, report_type, currency, account_name, value, statement_group 
            FROM cmf_financial_statements 
            WHERE 1=1
        """
        params = []

        if period:
            query += " AND period = ?"
            params.append(period)
        if rut:
            # Limpiar RUT de entrada para coincidir con la base de datos
            clean_rut = str(rut).strip().replace(".", "").replace("-", "")
            query += " AND rut = ?"
            params.append(clean_rut)
        if company:
            query += " AND LOWER(company_name) LIKE ?"
            params.append(f"%{company.lower()}%")

        # ORDER BY id preserva el orden de ingesta = orden del archivo plano de la CMF,
        # que es el orden oficial de presentación de la taxonomía IFRS (no alfabético).
        query += " ORDER BY id ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    # ----------------------------------------------------------
    # Series de partidas por empresa (para graficar en /eeff)
    # ----------------------------------------------------------

    def get_company_graphable_accounts(self, rut: str) -> list[str]:
        """
        Cuentas de una empresa que se pueden graficar: presentes en ≥2 períodos y con un
        único valor por período (evita las cuentas repetidas/ambiguas del XBRL plano).
        """
        clean = str(rut).strip().replace(".", "").replace("-", "")
        rows = self.conn.execute("""
            SELECT account_name
            FROM (
                SELECT account_name, period, COUNT(DISTINCT value) dv
                FROM cmf_financial_statements WHERE rut = ?
                GROUP BY account_name, period
            )
            GROUP BY account_name
            HAVING COUNT(DISTINCT period) >= 2 AND MAX(dv) = 1
            ORDER BY account_name
        """, [clean]).fetchall()
        return [str(r[0]) for r in rows]

    def get_company_account_series(self, rut: str, account_name: str):
        """Evolución de una partida por período (un valor por período), como DataFrame."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        return self.conn.execute("""
            SELECT period, MAX(value) AS value
            FROM cmf_financial_statements
            WHERE rut = ? AND account_name = ?
            GROUP BY period
            ORDER BY period ASC
        """, [clean, account_name]).fetchdf()

    def get_company_ratios(self, rut: str, period: int) -> Optional[dict]:
        """Indicadores financieros de una empresa/período (ver compute_ratios)."""
        return compute_ratios(self.query_cmf_statements(rut=rut, period=period, limit=2000))

    def _flujos_primera_ocurrencia(self, period: int) -> dict:
        """{rut: {cuenta de flujo: valor}} de un período (primera ocurrencia = orden IFRS)."""
        from db.common import FLOW_ACCOUNTS
        marcas = ",".join("?" * len(FLOW_ACCOUNTS))
        rows = self.conn.execute(f"""
            SELECT rut, account_name, arg_min(value, id) FROM cmf_financial_statements
            WHERE period = ? AND account_name IN ({marcas}) GROUP BY 1, 2""",
                                 [int(period), *FLOW_ACCOUNTS]).fetchall()
        out: dict = {}
        for rut, name, v in rows:
            out.setdefault(str(rut), {})[name] = v
        return out

    def get_ratios_universo(self, period: int, ltm: bool = True) -> list[dict]:
        """
        Ratios de TODAS las empresas de un período, una fila por empresa (una consulta y cálculo en
        memoria). Con `ltm=True` los ratios de flujo (ROE, ROA, márgenes, cobertura de intereses,
        Z'', deuda neta / EBITDA) son de los últimos 12 meses: acumulado actual + año anterior −
        mismo trimestre del año anterior (en diciembre, el año completo). Una empresa sin los
        períodos que necesita queda sin esos ratios, no sin los de balance.

        Cada fila trae además `sector` y `excl_fin`: sector financiero, holding o AGF, donde el Z'' y
        la deuda neta / EBITDA no tienen sentido (quedan en None). El margen EBITDA y la deuda neta /
        EBITDA exigen que la empresa informe depreciación (`deprec_informada`); si no, el «EBITDA» sería el EBIT.
        """
        from api.company_profiles import sector_of
        from db.common import SECTORES_SIN_Z, first_values, is_agf_name, ltm_flows
        df = self.query_cmf_statements(period=period, limit=10**9)
        if df.empty:
            return []
        mes = int(period) % 100
        dic = mismo = {}
        if ltm and mes != 12:
            dic = self._flujos_primera_ocurrencia((int(period) // 100 - 1) * 100 + 12)
            mismo = self._flujos_primera_ocurrencia(int(period) - 100)
        out = []
        for rut, sub in df.groupby("rut", sort=False):
            cname = str(sub.iloc[0]["company_name"])
            flujos = ltm_flows(first_values(sub), dic.get(str(rut)), mismo.get(str(rut)), mes) if ltm else None
            r = compute_ratios(sub, ltm=flujos)
            if not r:
                continue
            sector = sector_of(str(rut), cname)
            excl = sector in SECTORES_SIN_Z or is_agf_name(cname)
            if excl:                                     # el Z'' y la deuda neta no se leen igual en estos
                r["z_altman"] = r["deuda_neta_ebitda"] = None
                r["ebitda_negativo"] = False
            if not r["deprec_informada"]:
                r["margen_ebitda"] = None
            out.append({**r, "rut": str(rut), "company_name": cname, "sector": sector, "excl_fin": excl,
                        "ltm": bool(ltm)})
        return out

    def get_ratios_ranking(self, period: int, metric: str = "roe", n: Optional[int] = 15,
                           sector: str = None, ltm: bool = True) -> list[dict]:
        """
        Ranking de empresas por un ratio en un período, de MEJOR a peor (`db.common.RATIO_META` dice
        cuál es el sentido de cada indicador), con los ratios de flujo de los últimos 12 meses.
        `n=None` devuelve a todas las que tienen dato.
        """
        from db.common import ordenar_ranking
        con, _ = ordenar_ranking(self.get_ratios_universo(period, ltm), metric, sector)
        return con if n is None else con[:n]

    def get_company_ratios_series(self, rut: str) -> list[dict]:
        """
        Serie de ratios por período de una empresa (para graficar evolución).
        Devuelve [{period, roe, roa, margen_neto, margen_bruto, liquidez, endeudamiento}]
        ordenado por período. Ratios de income son "del período" (acumulado en el año).
        """
        df = self.query_cmf_statements(rut=rut, limit=10**9)
        if df.empty:
            return []
        out = []
        for period, sub in df.groupby("period", sort=False):
            r = compute_ratios(sub)
            if r:
                row = {"period": int(period)}
                row.update({k: v for k, v in r.items() if k != "currency"})
                out.append(row)
        out.sort(key=lambda x: x["period"])
        return out

    def get_company_periods(self, rut: str) -> list[int]:
        """Períodos (YYYYMM) con EEFF de una empresa, más reciente primero."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        rows = self.conn.execute(
            "SELECT DISTINCT period FROM cmf_financial_statements WHERE rut = ? ORDER BY period DESC",
            [clean]
        ).fetchall()
        return [int(r[0]) for r in rows]

    def get_statement_evolution(self, rut: str, periods: list[int], statement_group: str) -> dict:
        """
        Matriz de evolución de UN estado financiero de una empresa a través de varios períodos.
        Devuelve {"periods": [asc], "accounts": [{account_name, values:[por período]}]}.
        Orden de cuentas = posición relativa promedio en el balance para evitar cuentas descolocadas.
        """
        clean = str(rut).strip().replace(".", "").replace("-", "")
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": []}
        ph = ",".join(["?"] * len(periods))
        df = self.conn.execute(f"""
            WITH ranked AS (
                SELECT 
                    period, account_name, value, id,
                    ROW_NUMBER() OVER (PARTITION BY period ORDER BY id ASC) as rn,
                    COUNT(*) OVER (PARTITION BY period) as total
                FROM cmf_financial_statements
                WHERE rut = ? AND statement_group = ? AND period IN ({ph})
            ),
            avg_positions AS (
                SELECT account_name, AVG(rn * 1.0 / total) as avg_pos
                FROM ranked
                GROUP BY account_name
            )
            SELECT r.period, r.account_name, r.value, a.avg_pos
            FROM ranked r
            JOIN avg_positions a ON r.account_name = a.account_name
            ORDER BY a.avg_pos ASC, r.period DESC
        """, [clean, statement_group] + periods).fetchdf()
        if df.empty:
            return {"periods": [], "accounts": []}

        order, seen = [], set()
        for _, r in df.iterrows():
            a = str(r["account_name"])
            if a not in seen:
                order.append(a)
                seen.add(a)

        pivot = {}  # account -> {period: value} (primera ocurrencia por período)
        for _, r in df.iterrows():
            a, p = str(r["account_name"]), int(r["period"])
            pivot.setdefault(a, {})
            if p not in pivot[a]:
                pivot[a][p] = r["value"]

        periods_asc = sorted(set(int(p) for p in df["period"]))
        accounts = [{"account_name": a, "vals": [pivot[a].get(p) for p in periods_asc],
                     "is_total": is_total_account(a)}
                    for a in order]
        return {"periods": periods_asc, "accounts": accounts}

    def get_company_latest_period(self, rut: str) -> Optional[int]:
        """Último período (YYYYMM) con EEFF para una empresa."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        row = self.conn.execute(
            "SELECT MAX(period) FROM cmf_financial_statements WHERE rut = ?", [clean]
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    # ----------------------------------------------------------
    # Frescura (catch-up dirigido por publicación)
    # ----------------------------------------------------------

    def get_latest_cmf_period(self) -> Optional[int]:
        """Último período (YYYYMM) ingresado de estados financieros corporativos CMF."""
        row = self.conn.execute("SELECT MAX(period) FROM cmf_financial_statements").fetchone()
        return int(row[0]) if row and row[0] is not None else None
