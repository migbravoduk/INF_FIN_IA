"""
Senda macro implícita en precios de mercado (curvas swap, db/curves.py).

  tpm(h)      TPM que descuentan los swaps cámara: forward a 1 mes en h, base 360.
  ipc_yoy(h)  Inflación a 12 meses implícita: para h ≥ 12, la compensación inflacionaria
              forward entre h−12 y h (swap pesos − swap UF); para h < 12, interpolación
              lineal entre la última inflación efectiva y la implícita a 12 meses (la curva UF
              empieza en 1 año y el tramo corto lo distorsiona el rezago de la UF).

Los precios de mercado incluyen premios (por plazo, por riesgo inflacionario, liquidez). Se
corrigen restando la diferencia PROMEDIO entre mercado y la Encuesta de Operadores Financieros
en los 3 años previos al corte, a 12 y 24 meses (interpolada por horizonte, 0 en h = 0).
Todo con datos hasta `as_of`: las curvas no se revisan, así que el vintage es exacto.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from db import curves as C

logger = logging.getLogger(__name__)

EOF_TPM = {12: "F089.EOF.TPM.12MS.D", 24: "F089.EOF.TPM.24MS.D"}
EOF_INF = {12: "F089.EOF.VII.12MS.D", 24: "F089.EOF.VII.S12M.D"}  # 0–12 y 13–24 meses
PREMIO_ANOS = 3


def _params_hist(db, curva: str, hasta) -> pd.DataFrame:
    df = db.conn.execute("SELECT * FROM curva_ajuste WHERE curva = ? AND fecha <= ? ORDER BY fecha",
                         [curva, hasta]).fetchdf()
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df.set_index("fecha")


def _row(df: pd.DataFrame, fecha) -> dict | None:
    sub = df[df.index <= pd.Timestamp(fecha)]
    if sub.empty or (pd.Timestamp(fecha) - sub.index[-1]).days > 10:
        return None
    return sub.iloc[-1].to_dict()


def _tpm_fwd(p: dict, meses: np.ndarray) -> np.ndarray:
    a = np.maximum(meses / 12, 1 / 365)
    b = meses / 12 + 1 / 12
    fwd = (C.zero_rate(p, b) * b - C.zero_rate(p, a) * a) / (b - a)
    return C.overnight_360(fwd)


def _infl_fwd(pn: dict, pr: dict, t0: float, t1: float) -> float:
    """Inflación anual implícita promedio entre t0 y t1 años (%)."""
    def acum(t):
        return 0.0 if t <= 0 else float((C.zero_rate(pn, [t])[0] - C.zero_rate(pr, [t])[0]) * t)
    return float((np.exp((acum(t1) - acum(t0)) / (t1 - t0)) - 1) * 100)


def _premios(db, cn: pd.DataFrame, cr: pd.DataFrame, as_of) -> dict:
    """Mercado − EOF promedio en los últimos 3 años, a 12 y 24 meses, para tpm e ipc_yoy."""
    desde = pd.Timestamp(as_of) - pd.DateOffset(years=PREMIO_ANOS)
    out = {"tpm": {}, "ipc_yoy": {}}
    for var, series in (("tpm", EOF_TPM), ("ipc_yoy", EOF_INF)):
        for h, sid in series.items():
            obs = db.conn.execute("SELECT date, value FROM observations WHERE series_id = ? "
                                  "AND date > ? AND date <= ?", [sid, desde, as_of]).fetchall()
            gaps = []
            for d, v in obs:
                pn = _row(cn, d)
                if pn is None:
                    continue
                if var == "tpm":
                    m = float(_tpm_fwd(pn, np.array([h]))[0])
                else:
                    pr = _row(cr, d)
                    if pr is None:
                        continue
                    m = _infl_fwd(pn, pr, (h - 12) / 12, h / 12)
                gaps.append(m - v)
            out[var][h] = float(np.mean(gaps)) if len(gaps) >= 12 else None
    return out


def market_path(db, as_of, meses: int, ipc_actual: float | None) -> tuple[pd.DataFrame, dict]:
    """
    Senda mensual (h = 1..meses) de tpm e ipc_yoy implícita en mercado y corregida por premio.
    Devuelve (DataFrame con columnas tpm/ipc_yoy indexado por h, detalle).
    """
    cn, cr = _params_hist(db, "swap_clp", as_of), _params_hist(db, "swap_uf", as_of)
    pn, pr = _row(cn, as_of), _row(cr, as_of)
    if pn is None:
        return pd.DataFrame(), {}
    h = np.arange(1, meses + 1)
    prem = _premios(db, cn, cr, as_of)

    def corr(var):
        g = prem.get(var, {})
        if g.get(12) is None or g.get(24) is None:
            return np.zeros(len(h))
        return np.interp(h, [0, 12, 24], [0.0, g[12], g[24]])  # constante después de 24

    out = pd.DataFrame(index=h)
    out["tpm"] = _tpm_fwd(pn, h.astype(float)) - corr("tpm")
    if pr is not None:
        # Anclas solo en años enteros (0–1, 1–2, 2–3…): la curva UF empieza en 1 año y un
        # forward entre plazos fraccionarios la extrapolaría bajo ese mínimo (medido: saltos
        # de 80 pb entre h=12 y h=18). Entre anclas, interpolación lineal.
        anos = int(np.ceil(meses / 12))
        xs = [0] + [12 * (a + 1) for a in range(anos)]
        ys = [ipc_actual if ipc_actual is not None else _infl_fwd(pn, pr, 0.0, 1.0)] + \
             [_infl_fwd(pn, pr, float(a), float(a + 1)) for a in range(anos)]
        out["ipc_yoy"] = np.interp(h, xs, ys) - corr("ipc_yoy")
    return out, {"fecha_curva": str(_row(cn, as_of) and cn[cn.index <= pd.Timestamp(as_of)].index[-1].date()),
                 "premios": prem}
