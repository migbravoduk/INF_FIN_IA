"""
Proyección macrofundada de partidas de EEFF corporativos (SARIMAX + senda EEE).

Modelo por (empresa, partida ERFG):
  - endógena: partida trimestral DESACUMULADA (los EEFF CMF vienen acumulados en el año),
    ~40 observaciones (2015T1 en adelante).
  - exógenas: variables macro trimestrales (act_yoy, ipc_yoy, tc) — historia efectiva para
    ajustar, senda EEE (models/macro_path.py) para proyectar. Es el enfoque "macrofundado":
    las proyecciones expertas de la Encuesta de Expectativas Económicas anclan el futuro.
  - especificación parsimoniosa: SARIMAX(1,0,0)x(0,1,1,4). La diferencia estacional captura
    el patrón trimestral de los flujos; el AR(1) la persistencia; pocas exógenas para ~40 obs.

La proyección devuelve media + bandas de confianza (80% y 95%) para 4–8 trimestres.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

from models.macro_path import MacroPath, build_macro_path

logger = logging.getLogger(__name__)

# Partidas ERFG proyectables (desacumulables, un valor por período)
FORECASTABLE_ACCOUNTS = [
    "Ingresos de actividades ordinarias",
    "Ganancia (pérdida)",
]

EXOG_VARS = ["act_yoy", "ipc_yoy", "tc"]  # tpm queda fuera: colineal con ipc en ~40 obs
MIN_OBS = 16          # mínimo de trimestres para intentar un modelo
ORDER = (1, 0, 0)
SEASONAL_ORDER = (0, 1, 1, 4)


@dataclass
class ForecastResult:
    rut: str
    account: str
    history: pd.Series          # trimestral desacumulada, índice 'YYYYMM'
    mean: pd.Series             # proyección puntual
    ci80: pd.DataFrame          # columnas low/high
    ci95: pd.DataFrame          # columnas low/high
    macro_assumptions: pd.DataFrame  # exógenas usadas en la proyección (por trimestre)
    survey_month: pd.Timestamp  # encuesta EEE que ancla la senda
    model_info: dict            # orden, aic, n_obs, exógenas


def _next_quarter(period: str) -> str:
    y, m = int(period[:4]), int(period[4:])
    m += 3
    if m > 12:
        y, m = y + 1, m - 12
    return f"{y}{m:02d}"


def get_quarterly_deaccumulated(db, rut: str, account: str) -> pd.Series:
    """Serie trimestral desacumulada de una partida ERFG (valores del trimestre)."""
    clean = str(rut).strip().replace(".", "").replace("-", "")
    df = db.conn.execute("""
        SELECT period, MAX(value) AS value
        FROM cmf_financial_statements
        WHERE rut = ? AND account_name = ? AND statement_group = 'ERFG'
        GROUP BY period ORDER BY period
    """, [clean, account]).fetchdf()
    if df.empty:
        return pd.Series(dtype=float)

    # Desacumular intra-anual: valor del trimestre = acumulado - acumulado previo del año
    out, prev_by_year = {}, {}
    for _, r in df.iterrows():
        period = str(int(r["period"]))
        year = period[:4]
        v = float(r["value"])
        out[period] = v - prev_by_year.get(year, 0.0)
        prev_by_year[year] = v

    s = pd.Series(out, dtype=float)
    # Reindexar a la malla trimestral completa (huecos = NaN; el filtro de Kalman los tolera)
    full_idx, p = [], s.index[0]
    while p <= s.index[-1]:
        full_idx.append(p)
        p = _next_quarter(p)
    return s.reindex(full_idx)


def forecast_account(db, rut: str, account: str, steps: int = 8,
                     macro: MacroPath | None = None,
                     use_exog: bool = True) -> ForecastResult | None:
    """
    Ajusta el SARIMAX y proyecta `steps` trimestres. None si no hay datos.
    `use_exog=False`: SARIMAX puro sin exógenas macro — el backtest (jul-2026)
    mostró que las exógenas dañan la precisión en flujos trimestrales; el modo
    puro es el mejor spec a horizonte de 1 trimestre.
    """
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    endog = get_quarterly_deaccumulated(db, rut, account)
    if endog.dropna().shape[0] < MIN_OBS:
        logger.info("Serie insuficiente para %s / %s (%d obs)", rut, account, endog.dropna().shape[0])
        return None

    if macro is None:
        macro = build_macro_path(db, horizon_months=3 * steps + 6)

    # Exógenas trimestrales sobre historia+senda (cubre también el trimestre "puente"
    # posterior al último EEFF pero anterior al inicio de la senda EEE).
    full_q = macro.full().resample("QE").mean()
    full_q.index = [f"{d.year}{d.month:02d}" for d in full_q.index]
    full_q = full_q[EXOG_VARS].ffill()

    fc_periods = []
    p = _next_quarter(endog.index[-1])
    for _ in range(steps):
        fc_periods.append(p)
        p = _next_quarter(p)

    missing = [p for p in list(endog.index) + fc_periods if p not in full_q.index]
    if missing:
        logger.warning("Sin exógenas para períodos %s; no se proyecta %s/%s", missing, rut, account)
        return None

    exog_hist = full_q.loc[endog.index]
    exog_fc = full_q.loc[fc_periods]

    # Estandarizar exógenas (media/desvío de la historia) y escalar la endógena para
    # estabilidad numérica del optimizador.
    mu, sd = exog_hist.mean(), exog_hist.std().replace(0.0, 1.0)
    exog_hist_z = (exog_hist - mu) / sd
    exog_fc_z = (exog_fc - mu) / sd
    scale = max(float(np.nanstd(endog.values)), 1.0)
    endog_s = endog / scale

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            endog_s.values, exog=exog_hist_z.values if use_exog else None,
            order=ORDER, seasonal_order=SEASONAL_ORDER,
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, maxiter=200)
        fc = fitted.get_forecast(steps=steps, exog=exog_fc_z.values if use_exog else None)

    mean = pd.Series(fc.predicted_mean * scale, index=fc_periods)
    ci80_raw = fc.conf_int(alpha=0.20) * scale
    ci95_raw = fc.conf_int(alpha=0.05) * scale
    ci80 = pd.DataFrame({"low": ci80_raw[:, 0], "high": ci80_raw[:, 1]}, index=fc_periods)
    ci95 = pd.DataFrame({"low": ci95_raw[:, 0], "high": ci95_raw[:, 1]}, index=fc_periods)

    return ForecastResult(
        rut=rut, account=account,
        history=endog, mean=mean, ci80=ci80, ci95=ci95,
        macro_assumptions=exog_fc.round(2),
        survey_month=macro.survey_month,
        model_info={
            "order": ORDER, "seasonal_order": SEASONAL_ORDER,
            "exog": EXOG_VARS if use_exog else [],
            "n_obs": int(endog.dropna().shape[0]),
            "aic": round(float(fitted.aic), 1),
        },
    )


def forecast_company(db, rut: str, steps: int = 8) -> dict[str, ForecastResult]:
    """Proyecta todas las partidas proyectables de una empresa con una sola senda macro."""
    macro = build_macro_path(db, horizon_months=3 * steps + 6)
    results = {}
    for account in FORECASTABLE_ACCOUNTS:
        try:
            r = forecast_account(db, rut, account, steps=steps, macro=macro)
        except Exception:
            logger.exception("Fallo proyectando %s / %s", rut, account)
            r = None
        if r is not None:
            results[account] = r
    return results
