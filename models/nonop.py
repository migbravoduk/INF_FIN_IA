"""
Resultado NO operacional ligado a variables macro: reajuste por UF y diferencias de cambio.

En Chile buena parte de la volatilidad de la ganancia neta es mecánica:
  - "Resultados por unidades de reajuste": activos y pasivos en UF se reajustan con la
    inflación → reajuste_t ≈ k_UF · π_UF,t   (k_UF < 0 si la empresa es deudora neta en UF)
  - Diferencias de cambio: posición neta en moneda extranjera → dif_t ≈ k_TC · Δlog(TC)_t
Ambos son antes de impuestos. El modelo estructural separa la ganancia en
    ganancia = operacional + (1 − τ)·(reajuste + dif. cambio)
proyecta la parte operacional con su cadena activos → rotación → margen y esta parte con
la exposición estimada de cada empresa × la senda macro (vintage-consistente).

Exposición: MCO sin intercepto sobre los últimos 12 trimestres hasta el origen (mínimo 6).
Inflación trimestral futura: se recupera de la senda de inflación a 12 meses por la
recursión exacta  m_t = (1+y_t)/(1+y_{t−12}) · (1+m_{t−12}) − 1  (con efecto base), y la UF
sigue al IPC con ~1 mes de rezago.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TAX = 0.27                  # impuesto de primera categoría (régimen general)
USE_MACRO = True            # False = ablación: se limpia el margen pero se proyecta 0 no operacional
EXPO_WINDOW_Q = 12
EXPO_MIN_Q = 6

REAJUSTE = ["Resultados por unidades de reajuste"]
# La CMF cambió el nombre de la cuenta en 2020: se suman (nunca coexisten en un período).
DIF_CAMBIO = ["Diferencias de cambio", "Ganancias (pérdidas) de cambio en moneda extranjera"]
UF = "F073.UFF.PRE.Z.D"
IPC_VAR = "G073.IPC.VAR.2023.M"


def _deacc(w: pd.DataFrame) -> pd.DataFrame:
    years = pd.Series([p[:4] for p in w.index], index=w.index)
    same = years.eq(years.shift(1)).values
    vals = np.where(same[:, None], w.values - w.shift(1).values, w.values)
    return pd.DataFrame(vals, index=w.index, columns=w.columns)


def load_nonop(db, periods: list[str]) -> dict[str, pd.DataFrame]:
    """Reajuste y diferencias de cambio trimestrales (desacumulados), period × rut."""
    names = REAJUSTE + DIF_CAMBIO
    df = db.conn.execute(f"""
        SELECT rut, account_name, period, MAX(value) AS value
        FROM cmf_financial_statements
        WHERE statement_group = 'ERFG' AND account_name IN ({', '.join('?' * len(names))})
        GROUP BY rut, account_name, period
    """, names).fetchdf()
    out = {}
    for key, accts in (("reajuste", REAJUSTE), ("dif_cambio", DIF_CAMBIO)):
        sub = df[df["account_name"].isin(accts)]
        w = sub.pivot_table(index="period", columns="rut", values="value", aggfunc="sum")
        w.index = w.index.astype(int).astype(str)
        out[key] = _deacc(w.reindex(periods))
    return out


def _quarter_key(ts: pd.Timestamp) -> str:
    return f"{ts.year}{ts.month:02d}"


def drivers(db, macro) -> pd.DataFrame:
    """
    Impulsores trimestrales (index 'YYYYMM'): pi_uf (variación UF del trimestre, decimal) y
    dlog_tc (Δlog del tipo de cambio de fin de trimestre). Historia efectiva hasta el corte de
    la senda; después, la senda macro (encuestas) — así el backtest no ve el futuro.
    """
    corte = macro.future.index.min()

    # --- Inflación mensual: historia efectiva + futura por recursión desde y (12m) ---
    ipc = db.conn.execute("SELECT date, value FROM observations WHERE series_id = ? ORDER BY date",
                          [IPC_VAR]).fetchdf()
    m = pd.Series(ipc["value"].values / 100, index=pd.to_datetime(ipc["date"])).resample("MS").last()
    m = m[m.index < corte].dropna()
    y = (macro.full()["ipc_yoy"] / 100)
    # Meses sin IPC efectivo (desde el último publicado hasta el fin de la senda):
    # tendencia (1+y)^(1/12) de la senda + estacionalidad promedio de ese mes en 5 años.
    # (La recursión exacta con efecto base repetía para siempre los shocks puntuales del
    # último año: un 2T-2026 de 2,5% reaparecía cada 2T.)
    tend_hist = (1 + y.reindex(m.index).ffill()) ** (1 / 12) - 1
    desv = (m - tend_hist).dropna().tail(60)
    estac = desv.groupby(desv.index.month).mean() if len(desv) >= 24 else pd.Series(dtype=float)
    fin = macro.future.index.max()
    for t in pd.date_range(m.index.max() + pd.DateOffset(months=1), fin, freq="MS"):
        yt = y.get(t)
        if yt is None or not np.isfinite(yt):
            yt = y[y.index <= t].dropna().iloc[-1] if y[y.index <= t].notna().any() else 0.03
        m.loc[t] = (1 + yt) ** (1 / 12) - 1 + float(estac.get(t.month, 0.0))
    m = m.sort_index()

    # UF del trimestre ≈ IPC de los 3 meses que terminan un mes antes del cierre.
    uf = db.conn.execute("SELECT date, value FROM observations WHERE series_id = ? ORDER BY date",
                         [UF]).fetchdf()
    ufm = pd.Series(uf["value"].values, index=pd.to_datetime(uf["date"])).resample("ME").last()
    ufm = ufm[ufm.index < corte]
    pi_hist = (ufm / ufm.shift(3) - 1)[ufm.index.month.isin([3, 6, 9, 12])]
    pi = {_quarter_key(d): float(v) for d, v in pi_hist.dropna().items()}
    lag = m.shift(1)  # rezago de la UF
    for d in pd.date_range(corte, periods=len(macro.future) + 3, freq="ME"):
        if d.month in (3, 6, 9, 12) and _quarter_key(d) not in pi:
            win = lag[(lag.index > d - pd.DateOffset(months=3)) & (lag.index <= d)]
            if len(win) == 3 and win.notna().all():
                pi[_quarter_key(d)] = float(np.prod(1 + win.values) - 1)

    # --- Tipo de cambio: promedio del último mes del trimestre ---
    tc = macro.full()["tc"]
    tcq = tc[tc.index.month.isin([3, 6, 9, 12])]
    dlog = np.log(tcq).diff()
    fx = {_quarter_key(d): float(v) for d, v in dlog.dropna().items()}
    return pd.DataFrame({"pi_uf": pd.Series(pi), "dlog_tc": pd.Series(fx)}).sort_index()


def exposure(y: pd.Series, x: pd.Series, as_of_period: str) -> float:
    """k de y ≈ k·x (sin intercepto) en los últimos 12 trimestres ≤ origen; 0 si no alcanza."""
    d = pd.concat([y.rename("y"), x.rename("x")], axis=1)
    d = d[d.index <= as_of_period].dropna().tail(EXPO_WINDOW_Q)
    if len(d) < EXPO_MIN_Q or (d["x"] ** 2).sum() <= 0:
        return 0.0
    return float((d["y"] * d["x"]).sum() / (d["x"] ** 2).sum())


def split_and_forecast(rut: str, nonop: dict, drv: pd.DataFrame, hist_periods: list[str],
                       fc_periods: list[str]) -> tuple[pd.Series, np.ndarray, dict]:
    """
    Devuelve (ajuste_historico, ajuste_futuro, detalle):
      ajuste = (1 − τ)·(reajuste + dif. cambio), en la escala de la ganancia (después de impuestos).
    La ganancia operacional histórica es ganancia − ajuste_historico.
    """
    origin = hist_periods[-1]
    adj_hist = pd.Series(0.0, index=hist_periods)
    adj_fc = np.zeros(len(fc_periods))
    det = {}
    for key, col in (("reajuste", "pi_uf"), ("dif_cambio", "dlog_tc")):
        w = nonop.get(key)
        if w is None or rut not in w.columns or col not in drv:
            continue
        s = w[rut].reindex(hist_periods)
        if s.notna().sum() < EXPO_MIN_Q:
            continue
        k = exposure(s, drv[col], origin)
        adj_hist = adj_hist + (1 - TAX) * s.fillna(0.0)
        x_fc = drv[col].reindex(fc_periods).fillna(0.0).values
        if USE_MACRO:
            adj_fc = adj_fc + (1 - TAX) * k * x_fc
        det[key] = {"k": k, "x_fc": x_fc.round(5).tolist()}
    return adj_hist, adj_fc, det
