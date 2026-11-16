"""Resultado no operacional: exposición y separación de la ganancia."""
import numpy as np
import pandas as pd

from models import nonop as N


def _q(n, start=2021):
    out, y, m = [], start, 3
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m += 3
        if m > 12:
            y, m = y + 1, 3
    return out


def test_exposicion_recupera_k():
    per = _q(14)
    x = pd.Series(np.linspace(0.002, 0.03, 14), index=per)
    y = -5e9 * x + np.random.default_rng(0).normal(0, 1e6, 14)
    assert abs(N.exposure(pd.Series(y, index=per), x, per[-1]) / -5e9 - 1) < 0.01
    # Solo usa datos hasta el origen y exige 6 trimestres.
    assert N.exposure(pd.Series(y, index=per), x, per[3]) == 0.0


def test_separa_y_proyecta():
    per, fc = _q(12), _q(4, 2024)
    x = pd.Series(0.01, index=per + fc)
    nonop = {"reajuste": pd.DataFrame({"1": -2e9 * x.reindex(per)})}
    drv = pd.DataFrame({"pi_uf": x, "dlog_tc": 0.0})
    adj_h, adj_f, det = N.split_and_forecast("1", nonop, drv, per, fc)
    assert np.allclose(adj_h.values, (1 - N.TAX) * -2e7)
    assert np.allclose(adj_f, (1 - N.TAX) * -2e7)
    assert abs(det["reajuste"]["k"] + 2e9) < 1


def test_drivers_inflacion_anual_coherente(real_db_readonly):
    from models.macro_path import build_macro_path
    mp = build_macro_path(real_db_readonly, horizon_months=27)
    d = N.drivers(real_db_readonly, mp)
    fut = d["pi_uf"].dropna().iloc[-8:]
    anual = float(np.prod(1 + fut.values[-4:]) - 1)
    assert 0.0 < anual < 0.08
