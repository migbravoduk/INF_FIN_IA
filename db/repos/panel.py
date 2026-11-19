"""
db/repos/panel.py — KPIs del panel multi-fuente.

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


class PanelRepo:


    # IDs de catálogo BCCh usados por el panel multi-fuente.
    KPI_SERIES = {
        "uf": "F073.UFF.PRE.Z.D",
        "usd_clp": "F073.TCO.PRE.Z.D",
        "ipc_v12": "G073.IPC.V12.2023.M",
        "tpm": "F022.TPM.TIN.D001.NO.Z.D",
        "pib": "F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T",
        "imacec": "F032.ICF.IND.Z.Z.EP18.Z.Z.0.M",
        "imacec_minero": "F032.IMC.IND.Z.Z.EP18.03.Z.0.M",
        "imacec_no_minero": "F032.IMC.IND.Z.Z.EP18.N03.Z.0.M",
        "bcp_5y": "F022.BCLP.TIS.AN05.NO.Z.D",
        "bcp_10y": "F022.BCLP.TIS.AN10.NO.Z.D",
        "bcu_5y": "F022.BUF.TIS.AN05.UF.Z.D",
        "bcu_10y": "F022.BUF.TIS.AN10.UF.Z.D",
        "rate_com": "F022.COM.TIP.Z.NO.Z.M",
        "rate_con": "F022.CON.TIP.Z.NO.Z.M",
        "rate_viv": "F022.VIV.TIP.MA03.UF.Z.M",
        # Indicadores de mercado de la portada (plan 19-nov F10). Todos ya están en `observations`.
        "eof_tpm_12m": "F089.EOF.TPM.12MS.D",        # TPM esperada a 12 meses (Encuesta de Operadores Financieros)
        "swap_2y_clp": "F022.SPC.TIN.AN02.NO.Z.D",   # swap cámara pesos 2 y 10 años: pendiente de la curva
        "swap_10y_clp": "F022.SPC.TIN.AN10.NO.Z.D",
        "bei_swap_2y": "F022.SWSP.TAS.AN02.Z.Z.D",   # compensación inflacionaria (swap 2 años, forward 5 en 5)
        "bei_fwd_5x5": "F022.SWFW.TAS.E5X5.Z.Z.D",
        "eee_ipc_11m": "F089.IPC.V12.14.M",          # Encuesta de Expectativas Económicas: inflación a 11 meses
        "cobre": "F019.PPB.PRE.40.M",                # USD por libra
        "ipsa": "F013.IBC.IND.N.7.LAC.CL.CLP.BLO.M",
        "ust_10y": "F019.TBG.TAS.10.M",
        "fed": "F019.TPM.TIN.10.D",
        "desempleo": "F049.DES.TAS.INE.10.M",
        "dep_90d": "F022.TDB.TIS.D090.NO.Z.D",       # depósitos bancarios en bolsa a 90 días (pesos)
        "swap_90d": "F022.SPC.TPR.D090.NO.Z.D",      # swap cámara pesos a 90 días
    }
    META_INFLACION = 3.0     # meta del Banco Central (%)
    # Códigos de cuenta bancaria usados en los rankings.
    BANK_ASSETS_ACCOUNT = "100000000"      # Total activos (balance)
    BANK_RESULT_ACCOUNT = "590000000"      # Utilidad (pérdida) del ejercicio (resultado)

    def _yoy_pct(self, series_id: str) -> Optional[float]:
        """Variación interanual (%) del último dato vs el mismo período del año anterior."""
        latest = self.conn.execute(
            "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 1",
            [series_id]
        ).fetchone()
        if not latest or latest[1] is None:
            return None
        d = latest[0]
        if isinstance(d, datetime):
            d = d.date()
        try:
            prior = d.replace(year=d.year - 1)
        except ValueError:
            prior = d - __import__("datetime").timedelta(days=365)
        old = self.conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            [series_id, str(prior)]
        ).fetchone()
        if not old or not old[0]:
            return None
        return (latest[1] / old[0] - 1) * 100.0

    # --- Helpers de series con fecha (indicadores de mercado de la portada) -----------------------
    def _ultimo(self, series_id: str) -> Optional[dict]:
        """{valor, fecha (ISO)} del último dato de una serie, o None."""
        v = self.get_latest_value(series_id)
        if not v or v["value"] is None:
            return None
        return {"valor": float(v["value"]), "fecha": str(v["date"])[:10]}

    def _valor_en(self, series_id: str, fecha: str) -> Optional[float]:
        """Valor de la serie en `fecha` o el último dato anterior."""
        row = self.conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND date <= ? AND value IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", [series_id, fecha]).fetchone()
        return float(row[0]) if row else None

    @staticmethod
    def _hace_un_anio(fecha: str) -> str:
        d = datetime.fromisoformat(fecha[:10]).date()
        try:
            return str(d.replace(year=d.year - 1))
        except ValueError:                                   # 29 de febrero
            return str(d - __import__("datetime").timedelta(days=365))

    def _ultimo_comun(self, a: str, b: str) -> Optional[dict]:
        """Último día en que existen las dos series: {a, b, fecha}."""
        row = self.conn.execute(
            """SELECT x.date, x.value, y.value FROM observations x JOIN observations y ON y.date = x.date
               WHERE x.series_id = ? AND y.series_id = ? AND x.value IS NOT NULL AND y.value IS NOT NULL
               ORDER BY x.date DESC LIMIT 1""", [a, b]).fetchone()
        return {"fecha": str(row[0])[:10], "a": float(row[1]), "b": float(row[2])} if row else None

    def _diferencia_pb(self, a: str, b: str, fecha: Optional[str] = None) -> Optional[dict]:
        """(a − b) en puntos base el último día en que existen ambas (o en `fecha`), con el valor de hace 12 meses."""
        u = self._ultimo_comun(a, b) if fecha is None else {
            "fecha": fecha, "a": self._valor_en(a, fecha), "b": self._valor_en(b, fecha)}
        if not u or u["a"] is None or u["b"] is None:
            return None
        antes = self._hace_un_anio(u["fecha"])
        va, vb = self._valor_en(a, antes), self._valor_en(b, antes)
        return {"pb": (u["a"] - u["b"]) * 100, "fecha": u["fecha"],
                "pb_12m": None if va is None or vb is None else (va - vb) * 100}

    def get_market_indicators(self) -> dict:
        """
        Indicadores de mercado de la portada, cada uno {valor, fecha, ...} con su propia fecha (las
        series tienen rezagos distintos: diarias, mensuales y trimestrales) y `diaria` para formatear
        el día o solo el mes. Tolerante a series ausentes (None). Todo sale de `observations`; no hay
        fuentes nuevas. Ver docs/PLAN_OBSERVACIONES_19NOV.md F10.
        """
        K = self.KPI_SERIES
        out: dict = {}

        def con_var12(clave):
            u = self._ultimo(K[clave])
            if not u:
                return None
            antes = self._valor_en(K[clave], self._hace_un_anio(u["fecha"]))
            u["var12"] = (u["valor"] / antes - 1) * 100 if antes else None
            u["delta12"] = (u["valor"] - antes) if antes is not None else None     # diferencia absoluta
            return u

        tpm = self._ultimo(K["tpm"])
        out["tpm"] = None if not tpm else {**tpm, "diaria": True, "eof_12m": self._ultimo(K["eof_tpm_12m"])}
        pend = self._diferencia_pb(K["swap_10y_clp"], K["swap_2y_clp"])
        out["pendiente"] = None if not pend else {**pend, "diaria": True}
        bei = self._ultimo(K["bei_swap_2y"])
        out["inflacion_esperada"] = None if not bei else {
            **bei, "diaria": True, "meta": self.META_INFLACION,
            "sobre_meta_pb": (bei["valor"] - self.META_INFLACION) * 100,
            "fwd_5x5": self._ultimo(K["bei_fwd_5x5"])}
        out["eee_ipc_11m"] = self._ultimo(K["eee_ipc_11m"])
        out["spread_depositos"] = (lambda d: None if not d else {**d, "diaria": True})(
            self._diferencia_pb(K["dep_90d"], K["swap_90d"]))
        out["cobre"] = con_var12("cobre")
        out["ipsa"] = con_var12("ipsa")
        ust = self._ultimo(K["ust_10y"])
        if ust:
            # Spread soberano: BCP 10 años (pesos) al cierre del mismo mes que el dato del Tesoro de EE.UU.
            import calendar
            d = datetime.fromisoformat(ust["fecha"]).date()
            fin = str(d.replace(day=calendar.monthrange(d.year, d.month)[1]))
            bcp = self._valor_en(K["bcp_10y"], fin)
            ust["spread_pb"] = None if bcp is None else (bcp - ust["valor"]) * 100
        out["ust_10y"] = ust
        out["fed"] = (lambda d: None if not d else {**d, "diaria": True})(self._ultimo(K["fed"]))
        out["desempleo"] = con_var12("desempleo")
        return out

    def _top_banks(self, account_code: str, report_type: str, n: int = 5) -> list[dict]:
        """Top-n bancos por val_total de una cuenta, en el último período del reporte."""
        return self.conn.execute("""
            SELECT bank_name, val_total
            FROM cmf_bank_statements
            WHERE account_code = ? AND report_type = ?
              AND period = (SELECT MAX(period) FROM cmf_bank_statements
                            WHERE account_code = ? AND report_type = ?)
            ORDER BY val_total DESC
            LIMIT ?
        """, [account_code, report_type, account_code, report_type, n]).fetchdf().to_dict(orient="records")

    def get_overview_kpis(self) -> dict:
        """Indicadores multi-fuente para el panel. Tolerante a tablas vacías."""
        def latest(sid):
            v = self.get_latest_value(sid)
            return v["value"] if v else None

        macro = {
            "pib_yoy": self._yoy_pct(self.KPI_SERIES["pib"]),
            "imacec_yoy": self._yoy_pct(self.KPI_SERIES["imacec"]),
            "imacec_minero_yoy": self._yoy_pct(self.KPI_SERIES["imacec_minero"]),
            "imacec_no_minero_yoy": self._yoy_pct(self.KPI_SERIES["imacec_no_minero"]),
            "ipc_v12": latest(self.KPI_SERIES["ipc_v12"]),
            "usd_clp": latest(self.KPI_SERIES["usd_clp"]),
        }

        def fecha_de(clave):
            v = self.get_latest_value(self.KPI_SERIES[clave])
            return str(v["date"])[:10] if v else None
        pib_f = fecha_de("pib")
        macro["fechas"] = {
            "pib": pib_f, "imacec": fecha_de("imacec"), "ipc": fecha_de("ipc_v12"), "usd": fecha_de("usd_clp"),
            # El PIB es trimestral: la fecha de la serie es el primer día del trimestre.
            "pib_trimestre": (f"T{(int(pib_f[5:7]) - 1) // 3 + 1} {pib_f[:4]}" if pib_f else None)}

        curva_soberana = {
            "bcp_5y": latest(self.KPI_SERIES["bcp_5y"]),
            "bcp_10y": latest(self.KPI_SERIES["bcp_10y"]),
            "bcu_5y": latest(self.KPI_SERIES["bcu_5y"]),
            "bcu_10y": latest(self.KPI_SERIES["bcu_10y"]),
        }

        tasas_colocacion = {
            "comercial": latest(self.KPI_SERIES["rate_com"]),
            "consumo": latest(self.KPI_SERIES["rate_con"]),
            "vivienda": latest(self.KPI_SERIES["rate_viv"]),
        }

        bp = self.conn.execute(
            "SELECT MAX(period) FROM cmf_bank_statements WHERE report_type = 'balance'"
        ).fetchone()
        banca = {
            "period": int(bp[0]) if bp and bp[0] is not None else None,
            "top_assets": self._top_banks(self.BANK_ASSETS_ACCOUNT, "balance", 5),
            "top_results": self._top_banks(self.BANK_RESULT_ACCOUNT, "resultado", 5),
        }

        afp_returns = self.get_fund_returns_12m()
        afp_ranking = self.get_afp_equity_ranking()

        mercado = {"n_instrumentos": 0, "date": None}
        mrow = self.conn.execute("SELECT MAX(date) FROM sp_instrument_prices").fetchone()
        if mrow and mrow[0] is not None:
            last_d = str(mrow[0])
            cnt = self.conn.execute(
                "SELECT COUNT(*) FROM sp_instrument_prices WHERE date = ?", [last_d]
            ).fetchone()[0]
            mercado = {"n_instrumentos": int(cnt), "date": last_d}

        return {"macro": macro, "banca": banca, "afp_returns": afp_returns,
                "afp_ranking": afp_ranking, "mercado": mercado,
                "curva_soberana": curva_soberana, "tasas_colocacion": tasas_colocacion,
                "mercado2": self.get_market_indicators()}
