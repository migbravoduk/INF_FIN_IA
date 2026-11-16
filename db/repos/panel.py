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
    }
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
                "curva_soberana": curva_soberana, "tasas_colocacion": tasas_colocacion}
