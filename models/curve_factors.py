"""
Factores financieros mensuales desde las curvas (db/curves.py) para la ecuación de activos
del modelo estructural (docs/PLAN_SEMANA.md, bloque S2). Historia + futuro, con datos ≤ as_of.

  tasa_real5   cero swap UF a 5 años (% efectivo). Futuro: forward implícita a 5 años que
               empieza en h, [z(h+5)·(h+5) − z(h)·h] / 5, de la curva del día.
  pendiente    cero swap pesos 10a − 2a (pp). Futuro: último valor constante (la curva en
               pesos termina en 10 años; no hay forward).
  spread_banc  depósito a 360 días − cero swap pesos 1 año (pp). Futuro: último valor constante.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from db import curves as C

DEPOSITO_1A = "F022.TDB.TIS.AN01.NO.Z.D"
CURVE_FACTORS = ("tasa_real5", "pendiente", "spread_banc")


def _mensual(s: pd.Series) -> pd.Series:
    return s.dropna().resample("MS").mean()


def curve_factors(db, as_of=None, meses: int = 24) -> pd.DataFrame:
    """DataFrame mensual (índice inicio de mes) con las tres columnas, historia + `meses` futuros."""
    as_of = pd.Timestamp(as_of if as_of is not None else C.ultima_fecha(db))
    uf = C.tasas_cero(db, "swap_uf", (5,), hasta=as_of)
    clp = C.tasas_cero(db, "swap_clp", (1, 2, 10), hasta=as_of)
    dep = db.conn.execute("SELECT date, value FROM observations WHERE series_id = ? AND date <= ? "
                          "ORDER BY date", [DEPOSITO_1A, as_of.date()]).fetchdf()
    dep = pd.Series(dep["value"].astype(float).values, index=pd.to_datetime(dep["date"]))
    _, c = C._a_continua(dep.values, 360, "s360")
    dep_ef = pd.Series(C.efectiva(c), index=dep.index)

    hist = pd.DataFrame({
        "tasa_real5": _mensual(uf[5]),
        "pendiente": _mensual(clp[10] - clp[2]),
        "spread_banc": _mensual(dep_ef - clp[1].reindex(dep_ef.index)),
    })
    mes0 = as_of.to_period("M").to_timestamp()
    hist = hist[hist.index <= mes0]
    fut_idx = pd.date_range(mes0 + pd.DateOffset(months=1), periods=meses, freq="MS")
    fut = pd.DataFrame(index=fut_idx, columns=list(CURVE_FACTORS), dtype=float)

    p = C._params(db, as_of.date()).get("swap_uf")
    if p is not None:
        h = np.arange(1, meses + 1) / 12
        fwd = (C.zero_rate(p, h + 5) * (h + 5) - C.zero_rate(p, h) * h) / 5
        fut["tasa_real5"] = C.efectiva(fwd)
    for col in CURVE_FACTORS:
        ult = hist[col].dropna()
        if fut[col].isna().all() and len(ult):
            fut[col] = float(ult.iloc[-1])
    return pd.concat([hist, fut])
