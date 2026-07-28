"""
models/simple_forecast.py — SARIMAX univariante para las clasificaciones sin modelo
estructural calibrado (bancos, seguros, intermediarios).

Por qué NO se reusa el híbrido corporativo aquí:
  - El modelo estructural (activos operacionales → rotación → margen) se estima sobre el
    panel de empresas CORPORATIVAS; para un banco o una aseguradora esa cadena no aplica
    (la "rotación de activos" de un intermediario financiero no significa lo mismo).
  - Las bandas empíricas de `hybrid.BAND_QUANTILES` son cuantiles del error medidos en un
    backtest de 40 empresas corporativas. Reusarlas fuera de ese universo daría intervalos
    sin fundamento.

Por eso aquí va SARIMAX puro sobre la propia serie, con las bandas del modelo (gaussianas).
Son honestas pero NO están calibradas fuera de muestra: las vistas lo declaran explícitamente.
Para calibrarlas hay que correr el arnés `models/backtest.py` adaptado a cada clasificación.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Mínimo de observaciones para intentar un ajuste con estacionalidad.
MIN_OBS = 16

# Specs por tipo de serie.
#
# STOCK (ej. Total activos): nivel con tendencia → necesita diferenciación regular (d=1);
#   sin ella el modelo diverge y llega a proyectar valores negativos. Y va SIN componente
#   estacional a propósito: un stock de balance no tiene estacionalidad mensual/trimestral
#   real, y la diferenciación estacional hace que un cambio de nivel de una sola vez (una
#   FUSIÓN, por ejemplo) se replique como si fuera un patrón que se repite cada año.
#   Caso comprobado: BICE absorbe a Security en nov-2025 (los activos pasan de 11,9 a
#   21,5 billones); con estacionalidad el modelo proyectaba otro salto a 27,8 billones en
#   nov-2026, que es un artefacto, no una proyección.
#
# FLUJO ya desacumulado (resultado del período): ahí la estacionalidad SÍ es real
#   (efectos de cierre de año, estacionalidad del negocio), así que se conserva.
# El stock lleva además deriva (trend='c' sobre la serie diferenciada = random walk con
# deriva, el baseline estándar para stocks financieros). Sin ella la proyección sale plana
# en el último valor, que subestima el crecimiento. CAVEAT: la deriva se estima sobre toda
# la muestra, así que un evento único (fusión) la infla; no se modelan quiebres estructurales.
ORDER_STOCK = (1, 1, 0)
SEASONAL_STOCK = (0, 0, 0, 0)
TREND_STOCK = "c"
ORDER_FLOW = (1, 0, 0)


def deaccumulate_ytd(s: pd.Series) -> pd.Series:
    """
    Convierte una serie acumulada en el año a valores DEL período.

    Los estados de resultados de bancos, seguros e intermediarios se publican acumulados
    desde enero (verificado en los datos), igual que los flujos corporativos. Sin
    desacumular, la serie tiene un diente de sierra anual que el modelo interpretaría mal.
    El índice es YYYYMM.
    """
    out, prev_year, prev_val = {}, None, None
    for p in sorted(s.index):
        year, v = p // 100, s[p]
        out[p] = v if (year != prev_year or prev_val is None) else v - prev_val
        prev_year, prev_val = year, v
    return pd.Series(out)


def next_periods(last: int, steps: int, monthly: bool) -> list[int]:
    """Períodos futuros en formato YYYYMM (mensuales o trimestrales)."""
    out, y, m = [], last // 100, last % 100
    step = 1 if monthly else 3
    for _ in range(steps):
        m += step
        while m > 12:
            m -= 12
            y += 1
        out.append(y * 100 + m)
    return out


def forecast_series(history: pd.Series, steps: int, monthly: bool,
                    is_stock: bool = False) -> dict | None:
    """
    Ajusta SARIMAX sobre `history` (índice YYYYMM, ya desacumulada si es flujo) y proyecta
    `steps` períodos. `is_stock=True` para niveles con tendencia (activos); False para
    flujos del período. Devuelve {"history", "mean", "ci80", "ci95", "n_obs", "spec"} o
    None si no hay serie suficiente.
    """
    import warnings

    from statsmodels.tsa.statespace.sarimax import SARIMAX

    s = history.dropna().sort_index()
    if len(s) < MIN_OBS:
        return None

    season = 12 if monthly else 4
    y = s.values.astype(float)
    order = ORDER_STOCK if is_stock else ORDER_FLOW
    seasonal_order = SEASONAL_STOCK if is_stock else (0, 1, 1, season)

    trend = TREND_STOCK if is_stock else None

    def _fit(o, so):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return SARIMAX(y, order=o, seasonal_order=so, trend=trend,
                           enforce_stationarity=False,
                           enforce_invertibility=False).fit(disp=False)

    suffix = " con deriva" if is_stock else ""
    try:
        res = _fit(order, seasonal_order)
        spec = f"SARIMAX{order}{seasonal_order}{suffix}"
    except Exception:
        logger.exception("SARIMAX estacional falló; se intenta sin estacionalidad")
        try:
            res = _fit(order, (0, 0, 0, 0))
            spec = f"SARIMAX{order} (sin estacionalidad){suffix}"
        except Exception:
            logger.exception("SARIMAX falló por completo")
            return None

    fc = res.get_forecast(steps=steps)
    periods = next_periods(int(s.index[-1]), steps, monthly)
    mean = pd.Series(np.asarray(fc.predicted_mean, dtype=float), index=periods)
    ci80 = np.asarray(fc.conf_int(alpha=0.20), dtype=float)
    ci95 = np.asarray(fc.conf_int(alpha=0.05), dtype=float)
    return {
        "history": s,
        "mean": mean,
        "ci80": pd.DataFrame({"low": ci80[:, 0], "high": ci80[:, 1]}, index=periods),
        "ci95": pd.DataFrame({"low": ci95[:, 0], "high": ci95[:, 1]}, index=periods),
        "n_obs": len(s),
        "spec": spec,
    }
