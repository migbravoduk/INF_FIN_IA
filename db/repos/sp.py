"""
db/repos/sp.py — Superintendencia de Pensiones: valores cuota, cartera y precios; vistas AFP y fondos.

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


class SpRepo:

    def get_afp_list(self) -> list[str]:
        """Nombres de AFP con valores cuota (excluye el agregado TOTAL)."""
        rows = self.conn.execute("""
            SELECT DISTINCT afp_name FROM sp_quota_values
            WHERE afp_name <> 'TOTAL' ORDER BY afp_name ASC
        """).fetchall()
        return [str(r[0]) for r in rows]

    def sp_common_date(self, fund: Optional[str] = None) -> Optional[str]:
        """
        Último día en que TODAS las AFP tienen valor cuota (el mínimo de los máximos).

        Las AFP no siempre llegan al mismo día: en nov-2026 Capital traía datos al 15 y
        las demás al 12. Comparar cada una con su propio último dato mezclaba ventanas
        distintas (la rentabilidad 12m de Capital incluía un día de caída que las otras no)
        y la participación de mercado se calculaba sobre el patrimonio de una sola AFP
        (sumaba 412%). Toda comparación entre AFP se hace a esta fecha.
        """
        sql = """SELECT MIN(md) FROM (SELECT afp_name, MAX(date) md FROM sp_quota_values
                 WHERE afp_name <> 'TOTAL' {f} GROUP BY afp_name)"""
        row = self.conn.execute(sql.format(f="AND fund_type = ?" if fund else ""),
                                [fund] if fund else []).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    # ----------------------------------------------------------
    # Superintendencia de Pensiones (SP)
    # ----------------------------------------------------------

    def insert_sp_quota_values(self, records: list[dict]) -> int:
        """
        Inserta valores cuota y patrimonio de la SP de forma transaccional.
        Utiliza ON CONFLICT DO UPDATE para evitar duplicados.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['date']),
                str(rec['afp_name']).upper().strip(),
                str(rec['fund_type']).upper().strip(),
                float(rec['quota_value']),
                float(rec['equity_value'])
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            fast_executemany(self.conn, """
                INSERT INTO sp_quota_values (date, afp_name, fund_type, quota_value, equity_value)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (date, afp_name, fund_type) DO UPDATE SET
                    quota_value = excluded.quota_value,
                    equity_value = excluded.equity_value,
                    fetched_at = now()
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error("Error al insertar valores cuota de la SP en DuckDB", exc_info=True)
            raise e

    def get_foreign_portfolio(self, period: str, fund: str, afp: str = "TOTAL", limit: int = 15):
        """
        Detalle de la cartera extranjera: los instrumentos individuales que cuelgan
        de la sección 'TOTAL EXTRANJERO' del informe SP. La sección se resuelve por
        posición en la ingesta (columna `section`), porque los códigos de instrumento
        son ambiguos: el MISMO código (p. ej. CFID(6), CFIV(6)) aparece tanto en
        secciones nacionales como en la extranjera. Devuelve DataFrame
        [instrument_glosa, porcentaje, monto_pesos].
        """
        return self.conn.execute("""
            SELECT instrument_glosa, porcentaje, monto_pesos
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND UPPER(afp_name) = ?
              AND porcentaje IS NOT NULL AND porcentaje > 0
              AND section = 'TOTAL EXTRANJERO'
              AND instrument_glosa NOT LIKE 'TOTAL%'
            ORDER BY porcentaje DESC
            LIMIT ?
        """, [period, fund, afp.upper(), limit]).fetchdf()

    def get_fund_equity_rows(self, fund: str, from_date: str):
        """Patrimonio por AFP y fecha para un fondo (para participación de mercado)."""
        return self.conn.execute("""
            SELECT date, afp_name, equity_value
            FROM sp_quota_values
            WHERE fund_type = ? AND afp_name <> 'TOTAL' AND date >= ?
            ORDER BY date ASC
        """, [fund, from_date]).fetchdf()

    def get_fund_returns_12m(self) -> dict:
        """
        Rentabilidad nominal a 12 meses por multifondo (A–E).
        Por cada fondo devuelve {agg, leader_afp, leader_ret}:
          - agg: rentabilidad agregada del sistema (ponderada por patrimonio).
          - leader_afp / leader_ret: la AFP nº1 del fondo y su rentabilidad.
        return individual = cuota_hoy / cuota_~12m_atrás - 1.
        """
        import datetime as _dt
        import pandas as _pd
        out = {}
        for f in ("A", "B", "C", "D", "E"):
            corte = self.sp_common_date(f)
            rows = self.conn.execute("""
                SELECT q.afp_name, q.date, q.quota_value, q.equity_value
                FROM sp_quota_values q
                JOIN (SELECT afp_name, MAX(date) md FROM sp_quota_values
                      WHERE fund_type = ? AND afp_name <> 'TOTAL' AND date <= ?
                      GROUP BY afp_name) l
                  ON q.afp_name = l.afp_name AND q.date = l.md
                WHERE q.fund_type = ?
            """, [f, corte, f]).fetchdf()

            num = den = 0.0
            per_afp = []  # (afp_name, ret_pct)
            for _, r in rows.iterrows():
                d = _pd.Timestamp(r["date"]).date()
                target = d - _dt.timedelta(days=365)
                old = self.conn.execute("""
                    SELECT quota_value FROM sp_quota_values
                    WHERE afp_name = ? AND fund_type = ? AND date <= ?
                    ORDER BY date DESC LIMIT 1
                """, [r["afp_name"], f, str(target)]).fetchone()
                if old and old[0] and r["quota_value"]:
                    ret = r["quota_value"] / old[0] - 1.0
                    w = float(r["equity_value"] or 0.0)
                    per_afp.append((str(r["afp_name"]), ret * 100.0))
                    num += ret * w
                    den += w

            leader = max(per_afp, key=lambda x: x[1]) if per_afp else (None, None)
            out[f] = {
                "agg": (num / den * 100.0) if den else None,
                "leader_afp": leader[0],
                "leader_ret": leader[1],
            }
        return out

    def get_portfolio_composition(self, period: str, fund: str, afp: str = "TOTAL", limit: int = 12):
        """
        Composición de cartera de un fondo por CATEGORÍA legible (filas "TOTAL ..." que
        agrupan los instrumentos y suman ~100%), excluyendo el gran total "TOTAL ACTIVOS".
        Evita los códigos crípticos de instrumento (CMEV, ETFA, ...).

        Solo las secciones de primer nivel, que la SP escribe en MAYÚSCULAS ("TOTAL
        INSTITUCIONES ESTATALES"). Los subtotales van en minúscula ("Total Tesorería
        General", "Total Banco Central de Chile") y ya están dentro de su sección: con el
        filtro insensible a mayúsculas se contaban dos veces y la cartera sumaba 100,9%.
        Devuelve DataFrame [instrument_glosa, porcentaje, monto_pesos].
        """
        return self.conn.execute("""
            SELECT instrument_glosa, porcentaje, monto_pesos
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND UPPER(afp_name) = ?
              AND porcentaje IS NOT NULL AND porcentaje < 100
              AND instrument_glosa LIKE 'TOTAL %'
              AND instrument_glosa NOT LIKE 'TOTAL ACTIVOS%'
              AND instrument_glosa NOT LIKE '%MM$%' AND instrument_glosa NOT LIKE '%MMU$%'
            ORDER BY porcentaje DESC
            LIMIT ?
        """, [period, fund, afp.upper(), limit]).fetchdf()

    def get_portfolio_periods(self) -> list[str]:
        """Períodos disponibles en la cartera de inversión SP."""
        rows = self.conn.execute(
            "SELECT DISTINCT period FROM sp_portfolio_holdings ORDER BY period DESC"
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_sp_fund_types(self) -> list[str]:
        """Tipos de fondo con cuotas (A-E hoy; los generacionales aparecerán solos)."""
        rows = self.conn.execute(
            "SELECT DISTINCT fund_type FROM sp_quota_values ORDER BY fund_type"
        ).fetchall()
        return [str(r[0]) for r in rows]

    # Horizontes de retorno: (etiqueta, días aprox., anualizar)
    FUND_HORIZONS = [("1M", 30, False), ("3M", 91, False), ("6M", 182, False),
                     ("12M", 365, False), ("3A", 3 * 365, True),
                     ("5A", 5 * 365, True), ("10A", 10 * 365, True)]

    def insert_sp_instrument_prices(self, records: list[dict]) -> int:
        """
        Inserta precios diarios de instrumentos financieros de la SP.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['date']),
                str(rec['instrument_id']).strip(),
                str(rec.get('instrument_type', '')).strip(),
                str(rec.get('currency', '')).strip(),
                float(rec['price'])
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            fast_executemany(self.conn, """
                INSERT INTO sp_instrument_prices (date, instrument_id, instrument_type, currency, price)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (date, instrument_id) DO UPDATE SET
                    instrument_type = excluded.instrument_type,
                    currency = excluded.currency,
                    price = excluded.price,
                    fetched_at = now()
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error("Error al insertar precios de instrumentos SP en DuckDB", exc_info=True)
            raise e

    def insert_sp_portfolio_holdings(self, period: str, records: list[dict]) -> int:
        """
        Inserta la cartera mensual desagregada de la SP usando Delete-then-Insert.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['period']),
                str(rec['afp_name']).upper().strip(),
                str(rec['fund_type']).upper().strip(),
                str(rec['instrument_glosa']).strip(),
                int(rec['row_order']) if rec.get('row_order') is not None else None,
                str(rec['section']).strip() if rec.get('section') is not None else None,
                float(rec['monto_pesos']) if rec.get('monto_pesos') is not None else None,
                float(rec['monto_dolares']) if rec.get('monto_dolares') is not None else None,
                float(rec['porcentaje']) if rec.get('porcentaje') is not None else None
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM sp_portfolio_holdings WHERE period = ?", [period])
            fast_executemany(self.conn, """
                INSERT INTO sp_portfolio_holdings (period, afp_name, fund_type, instrument_glosa, row_order, section, monto_pesos, monto_dolares, porcentaje)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al insertar cartera SP para el período {period} en DuckDB", exc_info=True)
            raise e

    def insert_sp_cartera_detalle(self, period: int, rows: list[tuple], tablas=None) -> int:
        """
        Detalle de la cartera desagregada (collectors/sp_cartera_detalle.py) con delete-then-insert
        por período y tabla. `tablas`: las que se reemplazan (None = las presentes en `rows`).
        """
        import pandas as _pd
        from collectors.sp_cartera_detalle import COLUMNAS
        df = _pd.DataFrame(rows, columns=COLUMNAS)
        tablas = sorted(set(tablas) if tablas is not None else set(df["tabla"]))
        if not tablas:
            return 0
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(f"DELETE FROM sp_cartera_detalle WHERE period = ? "
                              f"AND tabla IN ({','.join('?' * len(tablas))})", [int(period), *tablas])
            if len(df):
                self.conn.register("_sp_det", df)
                self.conn.execute(f"INSERT INTO sp_cartera_detalle ({', '.join(COLUMNAS)}) "
                                  f"SELECT {', '.join(COLUMNAS)} FROM _sp_det")
                self.conn.unregister("_sp_det")
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return len(df)

    def ingest_sp_cartera(self, period: int, collector=None, tablas=None) -> tuple[int, int]:
        """
        Baja e ingesta la cartera desagregada de la SP de un período YYYYMM: composición
        (cuadro 1 → sp_portfolio_holdings) y detalle (→ sp_cartera_detalle) en una sola lectura.
        `tablas`: None = todas las del detalle; set() = solo composición. Devuelve (n_comp, n_det).
        """
        if collector is None:
            from collectors.sp_pensions import SPPensionCollector
            collector = SPPensionCollector()
        recs, det = collector.fetch_portfolio_full(int(period), tablas=tablas)
        if not recs:
            return 0, 0
        n_comp = self.insert_sp_portfolio_holdings(f"{str(period)[:4]}-{str(period)[4:]}", recs)
        n_det = self.insert_sp_cartera_detalle(int(period), det) if det else 0
        return n_comp, n_det

    def get_sp_detalle_periods(self, tabla: Optional[str] = None) -> list[int]:
        sql = "SELECT DISTINCT period FROM sp_cartera_detalle" + (" WHERE tabla = ?" if tabla else "")
        return sorted(r[0] for r in self.conn.execute(sql, [tabla] if tabla else []).fetchall())

    def query_sp_instrument_prices(
        self,
        instrument_id: Optional[str] = None,
        date: Optional[str] = None,
        limit: int = 50
    ):
        """Consulta la cinta de precios diaria de instrumentos con filtros flexibles."""
        query = "SELECT date, instrument_id, instrument_type, currency, price FROM sp_instrument_prices WHERE 1=1"
        params = []

        if instrument_id:
            query += " AND UPPER(instrument_id) LIKE ?"
            params.append(f"%{instrument_id.upper().strip()}%")
        if date:
            query += " AND date = ?"
            params.append(date)

        query += " ORDER BY date DESC, instrument_id ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    def query_sp_portfolio_holdings(
        self,
        period: Optional[str] = None,
        afp: Optional[str] = None,
        fund: Optional[str] = None,
        limit: int = 50,
    ):
        """Consulta la cartera mensual desagregada de la SP con filtros flexibles."""
        query = """
            SELECT period, afp_name, fund_type, instrument_glosa,
                   monto_pesos, monto_dolares, porcentaje
            FROM sp_portfolio_holdings WHERE 1=1
        """
        params = []
        if period:
            query += " AND period = ?"
            params.append(period)
        if afp:
            query += " AND UPPER(afp_name) = ?"
            params.append(afp.upper().strip())
        if fund:
            query += " AND UPPER(fund_type) = ?"
            params.append(fund.upper().strip())

        query += " ORDER BY porcentaje DESC NULLS LAST LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchdf()

    def query_sp_quota_values(
        self,
        afp: Optional[str] = None,
        fund: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 50
    ):
        """Consulta valores cuota y patrimonio registrados con filtros flexibles."""
        query = "SELECT date, afp_name, fund_type, quota_value, equity_value FROM sp_quota_values WHERE 1=1"
        params = []

        if afp:
            query += " AND UPPER(afp_name) = ?"
            params.append(afp.upper().strip())
        if fund:
            query += " AND UPPER(fund_type) = ?"
            params.append(fund.upper().strip())
        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)

        query += " ORDER BY date DESC, afp_name ASC, fund_type ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    def get_fund_horizon_returns(self, fund: str) -> dict:
        """
        Retornos nominales del valor cuota por AFP para un fondo, en varios horizontes.
        <=12M: retorno acumulado; >12M: anualizado (marcado en la etiqueta). La cuota
        base es la última disponible a la fecha objetivo (tolerancia 20 días; si la AFP
        no existía, el horizonte queda en None — ej. UNO antes de 2019).
        Devuelve {"horizons": [labels], "afps": [{afp, values: [...]}], "as_of": date}.
        """
        df = self.conn.execute("""
            SELECT afp_name, date, quota_value FROM sp_quota_values
            WHERE fund_type = ? AND afp_name <> 'TOTAL'
            ORDER BY afp_name, date
        """, [fund]).fetchdf()
        if df.empty:
            return {"horizons": [], "afps": [], "as_of": None}
        import pandas as pd

        df["date"] = pd.to_datetime(df["date"])
        as_of = pd.Timestamp(self.sp_common_date(fund) or df["date"].max())
        df = df[df["date"] <= as_of]
        labels = [f"{lab} (anual)" if ann else lab for lab, _, ann in self.FUND_HORIZONS]
        out = []
        for afp, sub in df.groupby("afp_name"):
            sub = sub.set_index("date")["quota_value"].sort_index()
            q_now = float(sub.iloc[-1])
            vals = []
            for _, days, annualize in self.FUND_HORIZONS:
                target = as_of - pd.Timedelta(days=days)
                base = sub.loc[:target]
                if base.empty or (target - base.index[-1]).days > 20:
                    vals.append(None)
                    continue
                r = q_now / float(base.iloc[-1])
                years = days / 365.0
                vals.append(round(((r ** (1 / years)) - 1) * 100, 2) if annualize
                            else round((r - 1) * 100, 2))
            out.append({"afp": str(afp), "values": vals})
        out.sort(key=lambda a: (a["values"][3] is None, -(a["values"][3] or 0)))  # por 12M
        return {"horizons": labels, "afps": out, "as_of": str(as_of.date())}

    def get_fund_composition_by_afp(self, fund: str) -> dict:
        """
        Composición vigente de la cartera de un fondo, por AFP, agregada a las secciones
        del informe SP (totalizadores 'TOTAL <sección>'; se excluyen las filas de control
        en MM$). Devuelve {"period", "sections": [orden], "afps": [{afp, pcts: {sección: %}}]}.
        """
        period = self.conn.execute(
            "SELECT MAX(period) FROM sp_portfolio_holdings").fetchone()[0]
        if period is None:
            return {"period": None, "sections": [], "afps": []}
        df = self.conn.execute("""
            SELECT afp_name, instrument_glosa, porcentaje, row_order
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND afp_name <> 'TOTAL'
              AND instrument_glosa LIKE 'TOTAL %'
              AND instrument_glosa NOT LIKE '%MM$%' AND instrument_glosa NOT LIKE '%MMU$%'
              AND porcentaje IS NOT NULL
            ORDER BY afp_name, row_order
        """, [str(period), fund]).fetchdf()
        if df.empty:
            return {"period": str(period), "sections": [], "afps": []}

        # Orden de secciones = orden oficial del informe (row_order de la primera AFP)
        sections, seen = [], set()
        for _, r in df.iterrows():
            s = str(r["instrument_glosa"]).replace("TOTAL ", "").title()
            if s not in seen:
                sections.append(s)
                seen.add(s)
        afps = []
        for afp, sub in df.groupby("afp_name"):
            pcts = {str(r["instrument_glosa"]).replace("TOTAL ", "").title():
                    float(r["porcentaje"]) for _, r in sub.iterrows()}
            afps.append({"afp": str(afp), "pcts": pcts})
        return {"period": str(period), "sections": sections, "afps": afps}

    def get_afp_net_simulation(self, fund: str = "C", salary: float = 1_000_000,
                               months: int = 12) -> dict:
        """
        Simulación de rentabilidad NETA de comisiones por AFP para un afiliado con
        sueldo bruto `salary`: cada mes cotiza el 10% al fondo (compra cuotas al
        valor del primer día hábil del mes) y paga además la comisión de su AFP
        (% de la remuneración, desde config/afp_commissions.yaml). Se valoriza el
        saldo a la última cuota disponible y se compara contra el DESEMBOLSO total
        (aportes + comisiones) en los últimos `months` meses corridos completos.

        Devuelve {"rows": [...por AFP...], "window": {desde, hasta, valuacion},
        "vigencia_comisiones": str}. Rentabilidades en % (no anualizadas).
        """
        import yaml
        from pathlib import Path

        cfg = yaml.safe_load(Path("config/afp_commissions.yaml").read_text(encoding="utf-8"))
        commissions = {str(k).upper(): float(v) for k, v in cfg["comisiones"].items()}

        # Cuota del primer día hábil de cada mes + última cuota, por AFP
        df = self.conn.execute("""
            WITH firsts AS (
                SELECT afp_name, STRFTIME(date, '%Y-%m') AS ym, MIN(date) AS d
                FROM sp_quota_values
                WHERE fund_type = ? AND afp_name <> 'TOTAL'
                GROUP BY afp_name, ym
            )
            SELECT f.afp_name, f.ym, f.d AS date, q.quota_value
            FROM firsts f
            JOIN sp_quota_values q
              ON q.afp_name = f.afp_name AND q.date = f.d AND q.fund_type = ?
            ORDER BY f.afp_name, f.ym
        """, [fund, fund]).fetchdf()
        if df.empty:
            return {"rows": [], "window": None, "vigencia_comisiones": cfg.get("vigencia", "")}

        last = self.conn.execute("""
            SELECT afp_name, ARG_MAX(quota_value, date) AS quota, MAX(date) AS date
            FROM sp_quota_values WHERE fund_type = ? AND afp_name <> 'TOTAL' AND date <= ?
            GROUP BY afp_name
        """, [fund, self.sp_common_date(fund)]).fetchdf().set_index("afp_name")

        aporte = salary * 0.10
        # Meses completos: excluir el mes de la última cuota (aún en curso)
        val_month = str(last["date"].max())[:7]
        rows, window = [], None
        for afp, sub in df.groupby("afp_name"):
            sub = sub[sub["ym"] < val_month].tail(months)
            if len(sub) < months or afp not in last.index:
                continue
            rate = commissions.get(str(afp).upper())
            if rate is None:
                continue
            quotas = float((aporte / sub["quota_value"]).sum())
            last_q = float(last.loc[afp, "quota"])
            first_q = float(sub["quota_value"].iloc[0])
            saldo = quotas * last_q
            aportes = aporte * months
            comisiones = salary * rate / 100.0 * months
            desembolso = aportes + comisiones
            
            # Umbral de fondo inicial para tener rentabilidad neta positiva en 12 meses
            r_fondo = (last_q - first_q) / first_q
            ganancia_aportes = saldo - aportes
            if r_fondo > 0:
                umbral = (comisiones - ganancia_aportes) / r_fondo
                umbral = max(0.0, umbral)
            else:
                umbral = None

            rows.append({
                "afp": str(afp), "comision_pct": rate,
                "aportes": aportes, "comisiones": comisiones, "desembolso": desembolso,
                "saldo": saldo,
                "rent_fondo_pct": (saldo - aportes) / aportes * 100.0,
                "rent_neta_pct": (saldo - desembolso) / desembolso * 100.0,
                "ganancia_neta": saldo - desembolso,
                "umbral_inicial": umbral,
            })
            window = {"desde": str(sub["ym"].iloc[0]), "hasta": str(sub["ym"].iloc[-1]),
                      "valuacion": str(last.loc[afp, "date"])[:10]}

        rows.sort(key=lambda r: r["rent_neta_pct"], reverse=True)
        return {"rows": rows, "window": window,
                "vigencia_comisiones": str(cfg.get("vigencia", ""))}

    def get_afp_equity_ranking(self) -> list[dict]:
        """Ranking de AFP por patrimonio total (suma del último patrimonio de cada fondo)."""
        return self.conn.execute("""
            WITH latest AS (
                SELECT afp_name, fund_type, MAX(date) md
                FROM sp_quota_values WHERE afp_name <> 'TOTAL' AND date <= ?
                GROUP BY afp_name, fund_type
            )
            SELECT q.afp_name, SUM(q.equity_value) AS equity
            FROM sp_quota_values q
            JOIN latest l ON q.afp_name = l.afp_name AND q.fund_type = l.fund_type AND q.date = l.md
            GROUP BY q.afp_name
            ORDER BY equity DESC
        """, [self.sp_common_date()]).fetchdf().to_dict(orient="records")

    def get_latest_sp_quota_date(self) -> Optional[str]:
        """Última fecha (YYYY-MM-DD) de valores cuota SP."""
        row = self.conn.execute("SELECT MAX(date) FROM sp_quota_values").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def get_latest_sp_portfolio_period(self) -> Optional[str]:
        """Último período (YYYY-MM) de cartera desagregada SP."""
        row = self.conn.execute("SELECT MAX(period) FROM sp_portfolio_holdings").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def get_latest_sp_price_date(self) -> Optional[str]:
        """Última fecha (YYYY-MM-DD) de la cinta de precios SP."""
        row = self.conn.execute("SELECT MAX(date) FROM sp_instrument_prices").fetchone()
        return str(row[0]) if row and row[0] is not None else None
