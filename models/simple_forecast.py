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

Por eso aquí va SARIMAX sobre la propia serie. Bancos y seguros llevan bandas calibradas
fuera de muestra (`BANDAS_SIMPLE`, arnés `models/backtest_simple.py`); intermediarios siguen
con las bandas gaussianas del modelo, y la vista lo declara. El resultado de bancos separa
además la exposición a la UF (`forecast_series_uf`).
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


def cargar_serie(db, clase: str, entity: str, acc: dict, kind: str | None = None) -> pd.Series | None:
    """
    Serie de una partida de `_PROY_SIMPLE` (api/routers/web/_shared.py) para una entidad,
    ya desacumulada si es flujo. Índice YYYYMM (int). La usan la vista y el backtest.
    """
    if clase == "bancos":
        serie = db.get_bank_total_series(entity, acc["key"], acc["report"])
    elif clase == "seguros":
        serie = db.get_insurer_account_series(entity, acc["key"], insurance_type=(kind or "vida"))
    else:
        serie = db.get_broker_account_series(entity, acc["key"])
    if serie is None or serie.empty:
        return None
    return deaccumulate_ytd(serie) if acc["accumulated"] else serie


def escala_mase(s: pd.Series, monthly: bool) -> float:
    """Escala del MASE: error medio del naive estacional dentro de muestra."""
    v = s.dropna().values.astype(float)
    k = 12 if monthly else 4
    if len(v) <= k:
        return float("nan")
    return float(np.mean(np.abs(v[k:] - v[:-k])))


# Bandas empíricas: cuantiles 0,80 / 0,95 del MASE del SARIMAX de producción en el backtest
# fuera de muestra (models/backtest_simple.py). Clave (clase, partida, horizonte medido).
# Un horizonte no medido usa el medido inmediatamente superior (o el mayor).
# Medido nov-2026: bancos 16 × 18 orígenes mensuales (h = 1, 3, 6, 12; 288 pares por celda);
# seguros 73 × 8 orígenes trimestrales (h = 1–4; ~440 pares). Resultado de bancos: spec `uf`.
# Regenerar: python -m models.backtest_simple --bandas scratch/bt_bancos.csv scratch/bt_seguros.csv
BANDAS_SIMPLE: dict[tuple[str, str, int], tuple[float, float]] = {
    ("bancos", "Resultado de los propietarios", 1): (1.057, 1.752),
    ("bancos", "Resultado de los propietarios", 3): (1.219, 2.097),
    ("bancos", "Resultado de los propietarios", 6): (1.358, 2.338),
    ("bancos", "Resultado de los propietarios", 12): (1.872, 3.252),
    ("bancos", "Total activos", 1): (0.384, 0.727),
    ("bancos", "Total activos", 3): (0.618, 1.036),
    ("bancos", "Total activos", 6): (0.829, 1.577),
    ("bancos", "Total activos", 12): (1.421, 2.889),
    ("seguros", "Total activo", 1): (0.783, 1.634),
    ("seguros", "Total activo", 2): (1.124, 2.510),
    ("seguros", "Total activo", 3): (1.348, 3.204),
    ("seguros", "Total activo", 4): (1.593, 3.482),
    ("seguros", "Total resultado del período", 1): (1.759, 3.346),
    ("seguros", "Total resultado del período", 2): (1.818, 3.686),
    ("seguros", "Total resultado del período", 3): (1.870, 3.662),
    ("seguros", "Total resultado del período", 4): (2.105, 4.106),
}


def bandas_empiricas(fc: dict, clase: str, account_label: str, monthly: bool) -> bool:
    """Reemplaza ci80/ci95 de `fc` por bandas calibradas si hay cuantiles. Devuelve si aplicó."""
    hs = sorted(h for (c, a, h) in BANDAS_SIMPLE if c == clase and a == account_label)
    scale = escala_mase(fc["history"], monthly)
    if not hs or not np.isfinite(scale) or scale <= 0:
        return False
    lo80, hi80, lo95, hi95 = [], [], [], []
    for i, m in enumerate(fc["mean"].values, start=1):
        h = next((x for x in hs if x >= i), hs[-1])
        q80, q95 = BANDAS_SIMPLE[(clase, account_label, h)]
        lo80.append(m - q80 * scale); hi80.append(m + q80 * scale)
        lo95.append(m - q95 * scale); hi95.append(m + q95 * scale)
    idx = fc["mean"].index
    fc["ci80"] = pd.DataFrame({"low": lo80, "high": hi80}, index=idx)
    fc["ci95"] = pd.DataFrame({"low": lo95, "high": hi95}, index=idx)
    return True


UF_VENTANA = {True: 36, False: 12}   # meses / trimestres para estimar la exposición


def forecast_series_uf(history: pd.Series, steps: int, monthly: bool, pi: pd.Series) -> dict | None:
    """
    Resultado con exposición a la UF: resultado = a + k·π_UF (MCO con intercepto en la ventana
    `UF_VENTANA` hasta el último dato); el SARIMAX de producción proyecta resultado − k·π y se
    suma k·π de la senda. `pi`: inflación UF por período (decimal, índice YYYYMM).
    Adoptado para el resultado de bancos (backtest nov-2026: 51,7% de pares, Δ MASE −0,021
    en h = 3, 6, 12). Devuelve el dict de `forecast_series` con `k_uf`, o None.
    """
    s = history.dropna().sort_index()
    t = s.tail(UF_VENTANA[monthly])
    x = pi.reindex(t.index)
    ok = x.notna()
    if ok.sum() < max(12 if monthly else 8, len(t) // 2):
        return None
    X = np.column_stack([np.ones(int(ok.sum())), x[ok].values])
    coef, *_ = np.linalg.lstsq(X, t[ok].values.astype(float), rcond=None)
    k = float(coef[1])
    fc = forecast_series(s - k * pi.reindex(s.index).fillna(0.0), steps, monthly=monthly,
                         is_stock=False)
    if fc is None:
        return None
    fut = pi.reindex(fc["mean"].index)
    if fut.isna().any():
        return None
    add = k * fut
    fc["mean"] = fc["mean"] + add
    for ci in ("ci80", "ci95"):
        fc[ci] = fc[ci].add(add, axis=0)
    fc["history"] = s
    fc["spec"] += " + exposición UF"
    fc["k_uf"] = k
    return fc


def inflacion_uf_periodos(db, monthly: bool, meses: int = 30, as_of=None) -> pd.Series:
    """π_UF por período YYYYMM (mensual o trimestral) desde la senda macro vigente (o vintage)."""
    from models.macro_path import build_macro_path
    from models.nonop import inflacion_uf_mensual
    m = inflacion_uf_mensual(db, build_macro_path(db, horizon_months=meses, as_of=as_of)).dropna()
    if monthly:
        m.index = [d.year * 100 + d.month for d in m.index]
        return m
    g = (1 + m).groupby([d.year * 100 + ((d.month - 1) // 3 + 1) * 3 for d in m.index]).prod() - 1
    return g


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
