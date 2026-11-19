"""Curvas de tasas: el ajuste recupera una curva conocida y la web/API responden."""
import numpy as np
import pandas as pd
import pytest

from db import curves as C


def _sintetica(params: dict, spec: dict, n_dias: int = 3) -> pd.DataFrame:
    """Tasas 'observadas' generadas desde una curva NSS conocida, en las convenciones de cada punto."""
    filas = {}
    for sid, plazo, conv in spec["puntos"]:
        t = plazo / 365 if conv == "s360" else float(plazo)
        c = float(C.zero_rate(params, [t])[0])
        if conv == "s360":
            r = (np.exp(c * t) - 1) * 360 / plazo
        elif conv == "a360":
            r = np.expm1(c) * 360 / 365
        else:
            r = np.expm1(c)
        filas[sid] = [r * 100] * n_dias
    return pd.DataFrame(filas, index=pd.date_range("2024-01-02", periods=n_dias, freq="B"))


def test_ajuste_recupera_curva_conocida():
    verdad = {"b0": 0.055, "b1": -0.01, "b2": 0.01, "b3": -0.005, "tau1": 1.5, "tau2": 6.0}
    spec = C.CURVAS["swap_clp"]
    df = C._ajustar_curva("swap_clp", spec, _sintetica(verdad, spec))
    assert len(df) == 3 and (df["modelo"] == "nss").all()
    p = df.iloc[0].to_dict()
    assert p["rmse_pb"] < 1.0
    plazos = np.array([0.25, 1, 2, 5, 10])
    assert np.abs(C.zero_rate(p, plazos) - C.zero_rate(verdad, plazos)).max() * 1e4 < 3


def test_conversiones_ida_y_vuelta():
    # Una TIB de 4,5% base 360 a 1 día equivale a una overnight 360 de 4,5%.
    t, c = C._a_continua(4.5, 1, "s360")
    assert abs(float(C.overnight_360(c)) - 4.5) < 1e-6
    # Efectiva anual ↔ continua.
    _, c = C._a_continua(5.0, 2, "efe")
    assert abs(float(C.efectiva(c)) - 5.0) < 1e-9


def test_tres_puntos_usa_tau_fijo():
    verdad = {"b0": 0.03, "b1": -0.01, "b2": 0.005, "b3": None, "tau1": 2.0, "tau2": None}
    spec = C.CURVAS["bono_uf"]
    w = _sintetica(verdad, spec, n_dias=4)
    w.iloc[3, 3:] = np.nan  # último día: sin bonos a 20 y 30 años
    df = C._ajustar_curva("bono_uf", spec, w)
    ult = df.sort_values("fecha").iloc[-1]
    assert ult["modelo"] == "ns_tau_fijo" and pd.isna(ult["rmse_pb"])


@pytest.fixture(scope="module")
def client(real_db_readonly):
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_curvas_web_y_api(client, real_db_readonly):
    if not real_db_readonly.conn.execute("SELECT COUNT(*) FROM curva_ajuste").fetchone()[0]:
        pytest.skip("curva_ajuste vacía")
    assert client.get("/curvas").status_code == 200
    assert client.get("/curvas?fecha=2012-06-15").status_code == 200
    assert client.get("/curvas?fecha=no-es-fecha").status_code == 400
    j = client.get("/api/v1/curvas").json()
    assert {"swap_clp", "swap_uf"} <= set(j["curvas"])
    # La compensación inflacionaria con swaps queda cerca de la que publica el BCCh a 5 años.
    cinco = next(r for r in j["bei"] if r["plazo"] == 5)
    if "bcch" in cinco and "swap" in cinco:
        assert abs(cinco["swap"] - cinco["bcch"]) < 0.25
