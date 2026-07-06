"""
Modelo HÍBRIDO de producción: empalme por horizonte según el backtest (jul-2026).

  h = 1     → SARIMAX puro sin exógenas (mejor spec a 1 trimestre: momentum).
  h >= 2    → modelo estructural con ancla 'last' (mejor mediana desde 2T y,
              sobre todo, medias que no explotan: la cadena activos → rotación
              → margen acota las proyecciones a rangos plausibles).

Bandas de confianza EMPÍRICAS: cuantiles de |error|/escala medidos en el backtest
(40 empresas × 8 orígenes, specs del propio híbrido), reescalados por la escala
de cada empresa (media |y_t − y_{t−4}|). Más honestas que las gaussianas del
modelo porque incorporan el error real fuera de muestra, colas incluidas.
Referencia: docs/wiki/Backtest-Modelos.md.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from models.forecast import (FORECASTABLE_ACCOUNTS, ForecastResult,
                             forecast_account, get_quarterly_deaccumulated)
from models.macro_path import build_macro_path
from models.structural import (estimate_asset_model, forecast_structural,
                               load_panel, _quarterly_factors)

logger = logging.getLogger(__name__)

# Cuantiles de |error|/escala por (partida, h) — backtest jul-2026 (hibrido:
# noexog en h=1, estr_last en h>=2). Para h>4 se usa el valor de h=4.
BAND_QUANTILES = {
    ("Ingresos de actividades ordinarias", 1): (1.258, 3.349),
    ("Ingresos de actividades ordinarias", 2): (1.670, 3.888),
    ("Ingresos de actividades ordinarias", 3): (1.812, 4.244),
    ("Ingresos de actividades ordinarias", 4): (1.877, 5.404),
    ("Ganancia (pérdida)", 1): (1.877, 3.743),
    ("Ganancia (pérdida)", 2): (2.313, 3.949),
    ("Ganancia (pérdida)", 3): (2.507, 4.219),
    ("Ganancia (pérdida)", 4): (2.407, 4.403),
}
_STRUCT_KEY = {"Ingresos de actividades ordinarias": "ingresos",
               "Ganancia (pérdida)": "ganancia"}


def _empirical_bands(mean: pd.Series, account: str, scale: float):
    """Bandas 80/95% desde los cuantiles del backtest, por horizonte."""
    lo80, hi80, lo95, hi95 = {}, {}, {}, {}
    for h, p in enumerate(mean.index, start=1):
        q80, q95 = BAND_QUANTILES[(account, min(h, 4))]
        m = mean[p]
        lo80[p], hi80[p] = m - q80 * scale, m + q80 * scale
        lo95[p], hi95[p] = m - q95 * scale, m + q95 * scale
    return (pd.DataFrame({"low": lo80, "high": hi80}),
            pd.DataFrame({"low": lo95, "high": hi95}))


def forecast_company_hybrid(db, rut: str, steps: int = 8) -> dict:
    """
    Proyección híbrida de una empresa. Devuelve:
      {"results": {partida: ForecastResult}, "structural": dict | None}
    donde "structural" trae la narrativa (activos, rotación, margen proyectados).
    """
    macro = build_macro_path(db, horizon_months=3 * steps + 9)
    panel = load_panel(db)
    amodel = estimate_asset_model(panel, _quarterly_factors(macro))

    est = None
    try:
        est = forecast_structural(db, rut, steps=steps, panel=panel, model=amodel,
                                  macro=macro, ratio_anchor="last")
    except Exception:
        logger.exception("Estructural falló para %s", rut)

    results = {}
    for account in FORECASTABLE_ACCOUNTS:
        try:
            sar = forecast_account(db, rut, account, steps=steps,
                                   macro=macro, use_exog=False)
        except Exception:
            logger.exception("SARIMAX noexog falló para %s/%s", rut, account)
            sar = None

        est_series = est.get(_STRUCT_KEY[account]) if est else None
        if sar is None and est_series is None:
            continue

        # Empalme: h=1 SARIMAX, h>=2 estructural (con fallback al que exista)
        if sar is not None:
            mean = sar.mean.copy()
            if est_series is not None:
                for p in mean.index[1:]:
                    if p in est_series.index and np.isfinite(est_series[p]):
                        mean[p] = est_series[p]
            history = sar.history
        else:
            mean = est_series.copy()
            history = get_quarterly_deaccumulated(db, rut, account)

        # Escala de la empresa para las bandas empíricas
        y = history.dropna().values
        scale = float(np.nanmean(np.abs(y[4:] - y[:-4]))) if len(y) > 8 else float(np.nanstd(y))
        if not np.isfinite(scale) or scale <= 0:
            scale = max(float(np.nanstd(y)), 1.0)
        ci80, ci95 = _empirical_bands(mean, account, scale)

        results[account] = ForecastResult(
            rut=rut, account=account,
            history=history, mean=mean, ci80=ci80, ci95=ci95,
            macro_assumptions=(sar.macro_assumptions if sar is not None
                               else _quarterly_factors(macro).loc[mean.index,
                                                                  ["act_yoy", "ipc_yoy", "tc"]].round(2)),
            survey_month=macro.survey_month,
            model_info={
                "modelo": "híbrido (SARIMAX h1 + estructural h2+)",
                "bandas": "empíricas (backtest jul-2026)",
                "n_obs": int(history.dropna().shape[0]),
                "order": "(1,0,0)", "seasonal_order": "(0,1,1,4)",
            },
        )

    structural_info = None
    if est is not None:
        structural_info = {
            "periods": est["periods"],
            "activos": est["activos"], "rotacion": est["rotacion_path"],
            "margen": est["margen_path"], "beta": est["beta"],
            "activos_last": float(est["activos_hist"].iloc[-1]),
            "rotacion_last": (float(est["rotacion_hist"].dropna().iloc[-1])
                              if not est["rotacion_hist"].dropna().empty else None),
            "margen_last": (float(est["margen_hist"].dropna().iloc[-1])
                            if not est["margen_hist"].dropna().empty else None),
        }
    return {"results": results, "structural": structural_info}
