"""
db/repos/bancos.py — Estados financieros bancarios (API SBIFv3) y empalme del plan de cuentas 2022.

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


class BancosRepo:

    def get_bank_list(self):
        """Bancos con datos (código + nombre), ordenados por nombre."""
        return self.conn.execute("""
            SELECT bank_code, MAX(bank_name) AS bank_name
            FROM cmf_bank_statements
            GROUP BY bank_code
            ORDER BY bank_name ASC
        """).fetchdf()

    def get_bank_periods(self, bank_code: Optional[str] = None) -> list[int]:
        """Períodos (YYYYMM) disponibles en estados bancarios, más reciente primero.
        Si se pasa `bank_code`, solo los de ese banco."""
        if bank_code:
            clean = str(bank_code).strip().zfill(3)
            rows = self.conn.execute("""
                SELECT DISTINCT period FROM cmf_bank_statements
                WHERE bank_code = ? ORDER BY period DESC
            """, [clean]).fetchall()
        else:
            rows = self.conn.execute("""
                SELECT DISTINCT period FROM cmf_bank_statements ORDER BY period DESC
            """).fetchall()
        return [int(r[0]) for r in rows]

    # ----------------------------------------------------------
    # Bancos CMF (Fase 4)
    # ----------------------------------------------------------

    def insert_bank_records(self, year: int, month: int, bank_code: str, report_type: str, records: list[dict]) -> int:
        """
        Inserta estados financieros de bancos usando "Delete-then-Insert" atómico.
        Garantiza que la operación completa sea transaccional.
        Retorna la cantidad de registros insertados.
        """
        if not records:
            return 0

        # Estandarizar código de banco a 3 dígitos (ej. '1' -> '001')
        clean_bank_code = str(bank_code).strip().zfill(3)

        tuples_data = [
            (
                int(rec['year']),
                int(rec['month']),
                int(rec['period']),
                clean_bank_code,
                str(rec['bank_name']).strip(),
                str(rec['report_type']).strip(),
                str(rec['account_code']).strip(),
                str(rec['account_name']).strip(),
                float(rec['val_clp_no_reaj']) if rec.get('val_clp_no_reaj') is not None else None,
                float(rec['val_clp_reaj_ipc']) if rec.get('val_clp_reaj_ipc') is not None else None,
                float(rec['val_clp_reaj_tc']) if rec.get('val_clp_reaj_tc') is not None else None,
                float(rec['val_extranjera']) if rec.get('val_extranjera') is not None else None,
                float(rec['val_total'])
            )
            for rec in records
        ]
        # La API SBIFv3 entregó el reporte de resultados de 2023-08 con CADA cuenta dos veces
        # (mismos valores): 19.664 llaves repetidas que duplicaban cualquier suma del mes. Se
        # descarta la repetición exacta por código de cuenta; si una cuenta viniera dos veces
        # con valores DISTINTOS se conservan ambas y el control de calidad lo reporta.
        seen, deduped = set(), []
        for t in tuples_data:
            k = (t[6], t[8:])  # account_code + valores
            if k not in seen:
                seen.add(k)
                deduped.append(t)
        if len(deduped) < len(tuples_data):
            logger.warning(f"Banco {clean_bank_code} {year}-{month:02d} {report_type}: "
                           f"{len(tuples_data) - len(deduped)} filas repetidas descartadas.")
        tuples_data = deduped

        self.conn.execute("BEGIN TRANSACTION")
        try:
            # 1. Eliminar datos existentes del banco, período y tipo de reporte específicos
            self.conn.execute("""
                DELETE FROM cmf_bank_statements 
                WHERE year = ? AND month = ? AND bank_code = ? AND report_type = ?
            """, [year, month, clean_bank_code, report_type])

            # 2. Bulk insert masivo
            fast_executemany(self.conn, """
                INSERT INTO cmf_bank_statements 
                    (year, month, period, bank_code, bank_name, report_type, 
                     account_code, account_name, val_clp_no_reaj, val_clp_reaj_ipc, 
                     val_clp_reaj_tc, val_extranjera, val_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)

            self.conn.execute("COMMIT")
            logger.info(f"Ingestados con éxito {len(tuples_data)} registros bancarios ({report_type}) para el banco {clean_bank_code} en {year}-{month:02d}.")
            return len(tuples_data)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al ingestar registros bancarios para el banco {clean_bank_code} en {year}-{month:02d}. Transacción revertida.", exc_info=True)
            raise e

    # --- Series por cuenta para proyecciones (índice YYYYMM → valor) ---

    def get_bank_total_series(self, bank_code: str, account_name: str, report_type: str):
        """
        Serie mensual del TOTAL consolidado (`val_total`) de una cuenta bancaria — la
        "serie de EEFF total", sin el desglose por moneda.

        EMPALMA los dos planes de cuentas igual que `get_bank_statement_evolution`: la CMF
        cambió el plan en 2022 (los nombres pasaron de 'ACTIVOS' a 'TOTAL ACTIVOS' y las
        UNIDADES de millones de pesos a pesos). Sin el empalme la serie queda partida en
        2022 y con unidades mezcladas, lo que rompe cualquier proyección.
        """
        import unicodedata
        import pandas as _pd

        def norm(name: str) -> str:
            s = unicodedata.normalize("NFKD", str(name))
            s = "".join(c for c in s if not unicodedata.combining(c)).upper().strip()
            if s.startswith("TOTAL "):
                s = s[6:]
            return " ".join(s.split())

        NEW_PLAN, OLD_PLAN_UNIT = 202201, 1e6
        target = norm(account_name)
        rows = self.conn.execute("""
            SELECT period, account_name, MAX(val_total) AS value
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ?
            GROUP BY period, account_name ORDER BY period
        """, [str(bank_code).strip().zfill(3), report_type]).fetchall()

        out: dict[int, float] = {}
        for period, name, value in rows:
            if value is None or norm(name) != target:
                continue
            p = int(period)
            v = float(value) * (OLD_PLAN_UNIT if p < NEW_PLAN else 1.0)
            out[p] = v
        return _pd.Series(dict(sorted(out.items())))

    def query_bank_statements(
        self,
        bank_code: Optional[str] = None,
        period: Optional[int] = None,
        periods: Optional[list[int]] = None,
        account_code: Optional[str] = None,
        report_type: Optional[str] = None,
        limit: int = 50
    ):
        """Recupera estados financieros mensuales de bancos con filtros flexibles como DataFrame."""
        query = """
            SELECT year, month, period, bank_code, bank_name, report_type, 
                   account_code, account_name, val_clp_no_reaj, val_clp_reaj_ipc, 
                   val_clp_reaj_tc, val_extranjera, val_total 
            FROM cmf_bank_statements 
            WHERE 1=1
        """
        params = []

        if period:
            query += " AND period = ?"
            params.append(period)
        elif periods:
            placeholders = ",".join("?" for _ in periods)
            query += f" AND period IN ({placeholders})"
            params.extend(periods)
        if bank_code:
            # Estandarizar a 3 dígitos (ej: '1' -> '001')
            clean_code = str(bank_code).strip().zfill(3)
            query += " AND bank_code = ?"
            params.append(clean_code)
        if account_code:
            query += " AND account_code = ?"
            params.append(str(account_code).strip())
        if report_type:
            query += " AND report_type = ?"
            params.append(str(report_type).strip())

        query += " ORDER BY period ASC, bank_name ASC, account_code ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    def get_bank_statement_evolution(self, bank_code: str, periods: list[int],
                                     report_type: str) -> dict:
        """
        Matriz de evolución del TOTAL (val_total, no por moneda) de un banco a través de
        varios períodos. Devuelve {"periods": [asc], "accounts": [{account_name, vals[por
        período], is_total}], "note"}.

        En 2022 la CMF cambió el plan de cuentas bancario (códigos de 7 → 9 dígitos,
        nombres distintos: 'ACTIVOS' → 'TOTAL ACTIVOS', y UNIDADES: el plan antiguo
        reporta en MILLONES de pesos y el vigente en PESOS — verificado contra cifras
        públicas del Banco de Chile). Para que las series largas no queden partidas,
        las cuentas se EMPALMAN por nombre normalizado (mayúsculas, sin acentos, sin
        prefijo 'TOTAL') y los valores pre-2022 se homologan a pesos (×1.000.000); se
        muestra el nombre moderno. Las cuentas propias de un solo régimen quedan al
        final, sin fusiones forzadas.
        """
        import unicodedata

        def norm(name: str) -> str:
            s = unicodedata.normalize("NFKD", str(name))
            s = "".join(c for c in s if not unicodedata.combining(c)).upper().strip()
            if s.startswith("TOTAL "):
                s = s[6:]
            return " ".join(s.split())

        clean = str(bank_code).strip().zfill(3)
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": [], "note": None}
        ph = ",".join(["?"] * len(periods))
        df = self.conn.execute(f"""
            SELECT period, account_code, account_name, val_total
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ? AND period IN ({ph})
            ORDER BY period ASC, account_code ASC
        """, [clean, report_type] + periods).fetchdf()
        if df.empty:
            return {"periods": [], "accounts": [], "note": None}

        NEW_PLAN = 202201       # primer período del plan de cuentas vigente
        OLD_PLAN_UNIT = 1e6     # plan antiguo en millones de pesos → homologar a pesos
        pivot: dict[str, dict[int, float]] = {}   # key -> {period: val en pesos}
        label: dict[str, tuple[int, str]] = {}    # key -> (period, nombre) más reciente
        code: dict[str, tuple[int, str]] = {}     # key -> (period, código) más reciente
        for _, r in df.iterrows():
            k, p = norm(r["account_name"]), int(r["period"])
            pivot.setdefault(k, {})
            if p not in pivot[k]:
                v = r["val_total"]
                pivot[k][p] = v * OLD_PLAN_UNIT if (v is not None and p < NEW_PLAN) else v
            if k not in label or p >= label[k][0]:
                label[k] = (p, str(r["account_name"]))
                code[k] = (p, str(r["account_code"]))

        periods_asc = sorted(set(int(p) for p in df["period"]))
        has_old = any(p < NEW_PLAN for p in periods_asc)
        has_new = any(p >= NEW_PLAN for p in periods_asc)

        # Orden: estructura del plan vigente primero (por código moderno), luego las
        # cuentas que solo existen en el plan antiguo (por su código), al final.
        def sort_key(k):
            in_new = any(p >= NEW_PLAN for p in pivot[k])
            c = code[k][1]
            return (0 if in_new else 1, len(c), c)

        accounts = []
        for k in sorted(pivot, key=sort_key):
            name = label[k][1]
            accounts.append({"account_name": name,
                             "vals": [pivot[k].get(p) for p in periods_asc],
                             "is_total": is_total_account(name)})
        note = ("La CMF cambió el plan de cuentas bancario en 2022: los totales se "
                "empalman por nombre y los valores pre-2022 se homologan de millones a "
                "pesos; el detalle fino puede no ser comparable entre ambos regímenes."
                ) if (has_old and has_new) else None
        return {"periods": periods_asc, "accounts": accounts, "note": note}

    def get_bank_graphable_accounts(self, bank_code: str, report_type: str) -> list[str]:
        """Cuentas de un banco/reporte presentes en ≥2 períodos (para graficar)."""
        clean = str(bank_code).strip().zfill(3)
        rows = self.conn.execute("""
            SELECT account_name
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ?
            GROUP BY account_name
            HAVING COUNT(DISTINCT period) >= 2
            ORDER BY account_name
        """, [clean, report_type]).fetchall()
        return [str(r[0]) for r in rows]

    def get_bank_account_series(self, bank_code: str, account_name: str, report_type: str):
        """Evolución de una cuenta bancaria (val_total) por período, como DataFrame."""
        clean = str(bank_code).strip().zfill(3)
        return self.conn.execute("""
            SELECT
                period,
                SUM(val_total) AS value,
                SUM(val_clp_no_reaj) AS clp_no_reaj,
                SUM(val_clp_reaj_ipc) AS clp_reaj_ipc,
                SUM(val_clp_reaj_tc) AS clp_reaj_tc,
                SUM(val_extranjera) AS extranjera
            FROM cmf_bank_statements
            WHERE bank_code = ? AND account_name = ? AND report_type = ?
            GROUP BY period
            ORDER BY period ASC
        """, [clean, account_name, report_type]).fetchdf()

    def get_latest_bank_period(self, bank_code: Optional[str] = None) -> Optional[int]:
        """Último período (YYYYMM) de estados bancarios; global o por banco (zfill 3)."""
        if bank_code:
            code = str(bank_code).strip().zfill(3)
            row = self.conn.execute(
                "SELECT MAX(period) FROM cmf_bank_statements WHERE bank_code = ?", [code]
            ).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(period) FROM cmf_bank_statements").fetchone()
        return int(row[0]) if row and row[0] is not None else None
