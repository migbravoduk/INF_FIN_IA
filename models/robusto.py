"""
Robustez de los modelos generales (nov-2026, decisiones del dueño tras PLAN_SEMANA §6):

  limpiar_outliers  aísla datos atípicos de UNA serie antes de calibrar un modelo. Descompone
                    la serie con STL robusto (tendencia + estacionalidad + residuo, Cleveland et
                    al. 1990) y marca como atípico el punto cuyo residuo supera K desvíos robustos
                    (1,4826 × MAD). El punto marcado se reemplaza por tendencia + estacionalidad:
                    un castigo único, una venta de activos o un efecto contable de una vez no se
                    propagan a la proyección. Solo se aísla un punto AISLADO (vecinos dentro de
                    K/2 desvíos): un cambio de nivel (fusión) deja varios residuos grandes
                    seguidos y no se toca. Límite: un quiebre en el último dato es
                    indistinguible de un atípico y se trataría como tal. Es la misma idea que `tsclean` (paquete forecast
                    de R), con un umbral fijado a priori, no ajustado al backtest.

  acotar            tope para el SARIMAX que explota (enforce_stationarity=False permite raíces
                    explosivas: en el backtest de seguros un caso llegó a 10⁸ veces la escala).
                    Si algún punto de la proyección sale del rango histórico ampliado
                    [mín − K·rango, máx + K·rango], la proyección completa se reemplaza por el
                    naive estacional (flujos) o el último valor (stocks).

Los dos se aplican en la calibración de los modelos generales: SARIMAX corporativo
(models/forecast.py), estructural (models/structural.py, ratios y telescopía) y SARIMAX de
bancos, seguros e intermediarios (models/simple_forecast.py).
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

K_OUTLIER = 4.0   # desvíos robustos del residuo STL (≈ 3 × IQR de tsclean)
K_ACOTAR = 1.0    # rangos históricos de holgura antes de declarar la proyección explosiva


def limpiar_outliers(s: pd.Series, period: int, k: float = K_OUTLIER) -> tuple[pd.Series, list]:
    """
    Devuelve (serie limpia, períodos marcados). Los NaN de la serie se conservan como NaN.
    Con menos de 2 ciclos completos + 1 dato no toca nada.
    """
    obs = s.dropna()
    if len(obs) < 2 * period + 1:
        return s, []
    from statsmodels.tsa.seasonal import STL
    x = s.astype(float).interpolate(limit_direction="both")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = STL(x.values, period=period, robust=True).fit()
    resid = pd.Series(res.resid, index=s.index)[obs.index]
    mad = float(np.median(np.abs(resid - np.median(resid))))
    sigma = 1.4826 * mad
    if not np.isfinite(sigma) or sigma <= 0:
        return s, []
    z = np.abs(resid.values - np.median(resid.values)) / sigma
    # Atípico aditivo vs cambio de nivel (Chen y Liu 1993): un atípico es AISLADO — sus vecinos
    # inmediatos están dentro de K/2 desvíos. En un quiebre (fusión) STL reparte el salto en
    # varios residuos grandes seguidos: ninguno queda aislado y la serie no se toca.
    grande = z > k / 2
    marcados = []
    for i in np.where(z > k)[0]:
        vecinos = [j for j in (i - 1, i + 1) if 0 <= j < len(z)]
        if not any(grande[j] for j in vecinos):
            marcados.append(resid.index[i])
    if not marcados:
        return s, []
    ajuste = pd.Series(res.trend + res.seasonal, index=s.index)
    out = s.copy()
    out[marcados] = ajuste[marcados]
    return out, marcados


def detalle(original: pd.Series, limpia: pd.Series, marcados: list) -> list[dict]:
    """[{period, valor, limpio}] de los puntos aislados, para mostrarlos en las vistas."""
    return [{"period": p, "valor": float(original[p]), "limpio": float(limpia[p])}
            for p in marcados]


def naive_estacional(history: pd.Series, steps: int, season: int) -> np.ndarray:
    """Repite el último ciclo observado (y[T+h] = y[T+h−season·⌈h/season⌉])."""
    v = history.dropna().values.astype(float)
    if len(v) < season:
        return np.full(steps, v[-1] if len(v) else np.nan)
    ult = v[-season:]
    return np.array([ult[h % season] for h in range(steps)])


def es_explosiva(mean, history: pd.Series, k: float = K_ACOTAR) -> bool:
    v = history.dropna().values.astype(float)
    m = np.asarray(mean, dtype=float)
    if len(v) == 0:
        return False
    lo, hi = float(v.min()), float(v.max())
    rango = max(hi - lo, abs(hi), abs(lo), 1e-9) if hi == lo else hi - lo
    return bool(np.any(~np.isfinite(m)) or np.any(m < lo - k * rango) or np.any(m > hi + k * rango))


def acotar(mean, history: pd.Series, season: int, stock: bool = False,
           k: float = K_ACOTAR) -> tuple[np.ndarray, bool]:
    """(proyección, se_acotó). Si la proyección es explosiva, naive estacional o último valor."""
    m = np.asarray(mean, dtype=float)
    if not es_explosiva(m, history, k):
        return m, False
    if stock:
        return np.full(len(m), float(history.dropna().iloc[-1])), True
    return naive_estacional(history, len(m), season), True
