"""
Modelo HÍBRIDO de producción: empalme por horizonte según el backtest (jul-2026, ronda 6
de nov-2026).

  h = 1     → SARIMAX puro sin exógenas (mejor spec a 1 trimestre: momentum), con tope si
              la proyección explota (models/robusto.py).
  h = 2–3   → promedio del SARIMAX y del estructural (combinación de pronósticos: gana a
              cada uno por separado en 54–60% de los pares).
  h >= 4    → modelo estructural (medias que no explotan: la cadena activos → rotación
              → margen acota las proyecciones a rangos plausibles). Rotación con ancla
              'last', margen con mediana 4T; activos ← tasa real swap UF 5a (no la TPM).

Bandas de confianza EMPÍRICAS: cuantiles de |error|/escala medidos en el backtest
(80 empresas × 8 orígenes, el propio híbrido), reescalados por la escala
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

# Cuantiles 0,80 / 0,95 de |error|/escala por (partida, h) — ronda 6, nov-2026
# (scratch/bt_r6_hibridos.csv, spec hib_nuevo: 80 empresas con ERFG o ERNG × 8 orígenes × h1–4,
# 640 pares por celda), compuesto igual que PRODUCCION. Para h>4 se usa h=4. Regenerar si
# cambian specs, senda o muestra (scratch/decisiones_r6.py final).
BAND_QUANTILES = {
    ("Ingresos de actividades ordinarias", 1): (1.293, 2.937),
    ("Ingresos de actividades ordinarias", 2): (1.410, 3.005),
    ("Ingresos de actividades ordinarias", 3): (1.513, 3.600),
    ("Ingresos de actividades ordinarias", 4): (1.701, 3.841),
    ("Ganancia (pérdida)", 1): (1.516, 3.399),
    ("Ganancia (pérdida)", 2): (1.773, 3.471),
    ("Ganancia (pérdida)", 3): (1.753, 3.637),
    ("Ganancia (pérdida)", 4): (1.804, 3.851),
}
_STRUCT_KEY = {"Ingresos de actividades ordinarias": "ingresos",
               "Ganancia (pérdida)": "ganancia"}

# Configuración de producción (cada valor salió de un backtest; ver docs/wiki/Backtest-Modelos.md).
# Ronda 6 (nov-2026, 80 empresas × 8 orígenes): el conjunto gana al híbrido anterior en 56% de
# los pares en ingresos (Δ MASE −0,11) y 54% en ganancia (Δ −0,06), en las 40 empresas ya
# vistas y en 40 nuevas.
PRODUCCION = {
    # Decisión 1: tasa real swap UF 5 años en lugar de la TPM (ingresos h2–4: 53% de pares)
    "factores": ["act_yoy", "ipc_yoy", "tasa_real5", "tc_yoy"],
    "ancla": "last",            # rotación: último valor (med4 se contradice entre muestras)
    "ancla_margen": "median4",  # decisión 2: margen con mediana 4T (robusta a un trimestre atípico)
    "limpiar": False,           # limpieza de atípicos medida: no aporta sobre la mediana
    "acotar": True,             # decisión 4: tope al SARIMAX explosivo
    "promedio_h": (2, 3),       # decisión 3: h2–3 = promedio SARIMAX + estructural
}


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
    cfg = PRODUCCION
    macro = build_macro_path(db, horizon_months=3 * steps + 9)
    panel = load_panel(db)
    extra = None
    if cfg["factores"]:
        from models.curve_factors import CURVE_FACTORS, curve_factors
        if any(f in CURVE_FACTORS for f in cfg["factores"]):
            extra = curve_factors(db, None, 3 * steps + 9)
    factors_q = _quarterly_factors(macro, extra, cfg["factores"])
    amodel = estimate_asset_model(panel, factors_q)

    est = None
    try:
        # nonop=True: la ganancia separa reajuste UF y diferencias de cambio y los proyecta
        # con la exposición de la empresa × senda de inflación y TC (backtest nov-2026:
        # gana al estructural simple en 53% de los casos, error medio −0,07 MASE).
        est = forecast_structural(db, rut, steps=steps, panel=panel, model=amodel,
                                  macro=macro, ratio_anchor=cfg["ancla"], nonop=True,
                                  factors_q=factors_q, limpiar=cfg["limpiar"],
                                  ancla_margen=cfg["ancla_margen"])
    except Exception:
        logger.exception("Estructural falló para %s", rut)

    results = {}
    for account in FORECASTABLE_ACCOUNTS:
        try:
            sar = forecast_account(db, rut, account, steps=steps, macro=macro, use_exog=False,
                                   limpiar=cfg["limpiar"], acotar=cfg["acotar"])
        except Exception:
            logger.exception("SARIMAX noexog falló para %s/%s", rut, account)
            sar = None

        est_series = est.get(_STRUCT_KEY[account]) if est else None
        if sar is None and est_series is None:
            continue

        # Empalme: h=1 SARIMAX, h en promedio_h = promedio de ambos, resto estructural;
        # si falta uno, el que exista
        if sar is not None:
            mean = sar.mean.copy()
            if est_series is not None:
                for h, p in enumerate(mean.index, start=1):
                    if h == 1 or p not in est_series.index or not np.isfinite(est_series[p]):
                        continue
                    if h in cfg["promedio_h"]:
                        mean[p] = (sar.mean[p] + est_series[p]) / 2
                    else:
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

        # Atípicos de la serie publicada (para la vista). Con limpiar=False solo se informan:
        # el modelo no los elimina (la mediana del margen ya es robusta a ellos).
        atipicos = (sar.model_info.get("atipicos") if sar is not None else None) or []
        if not cfg["limpiar"]:
            from models.robusto import detalle, limpiar_outliers
            limpia, marcados = limpiar_outliers(history, 4)
            atipicos = detalle(history, limpia, marcados)

        results[account] = ForecastResult(
            rut=rut, account=account,
            history=history, mean=mean, ci80=ci80, ci95=ci95,
            macro_assumptions=(sar.macro_assumptions if sar is not None
                               else _quarterly_factors(macro).loc[mean.index,
                                                                  ["act_yoy", "ipc_yoy", "tc"]].round(2)),
            survey_month=macro.survey_month,
            model_info={
                "modelo": "híbrido (SARIMAX h1 · promedio h2–3 · estructural h4+)",
                "bandas": "empíricas (backtest ronda 6, 80 empresas, nov-2026)",
                "atipicos_usados": cfg["limpiar"],
                "n_obs": int(history.dropna().shape[0]),
                "order": "(1,0,0)", "seasonal_order": "(0,1,1,4)",
                "atipicos": atipicos,
                "acotado": bool(sar is not None and sar.model_info.get("acotado")),
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
            "no_operacional": est.get("no_operacional") or {},
        }
    return {"results": results, "structural": structural_info}
