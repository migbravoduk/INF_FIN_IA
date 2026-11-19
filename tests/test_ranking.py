"""Ranking de empresas: mejores y peores, flujos de 12 meses y Z'' de Altman (plan 19-nov, F8)."""

import pandas as pd
import pytest

from db.common import (RATIO_META, compute_ratios, first_values, ltm_flows, ordenar_ranking, zona_z)


def _df(cuentas: dict, moneda="CLP") -> pd.DataFrame:
    return pd.DataFrame([{"account_name": k, "value": v, "currency": moneda} for k, v in cuentas.items()])


BALANCE = {"Total de activos": 1000.0, "Total de pasivos": 600.0, "Patrimonio total": 400.0,
           "Activos corrientes totales": 500.0, "Pasivos corrientes totales": 300.0,
           "Ganancias (pérdidas) acumuladas": 200.0}


def test_zonas_del_z():
    assert zona_z(2.61) == "segura" and zona_z(2.6) == "gris" and zona_z(1.1) == "gris"
    assert zona_z(1.09) == "peligro" and zona_z(-3) == "peligro"
    assert zona_z(None) is None and zona_z(float("nan")) is None


def test_z_altman_con_valores_conocidos():
    # X1 = (500−300)/1000 = 0,2 · X2 = 200/1000 = 0,2 · X3 = 100/1000 = 0,1 · X4 = 400/600
    # Z'' = 6,56·0,2 + 3,26·0,2 + 6,72·0,1 + 1,05·(400/600) = 1,312 + 0,652 + 0,672 + 0,7 = 3,336
    r = compute_ratios(_df({**BALANCE, "Ganancias (pérdidas) de actividades operacionales": 100.0}))
    assert r["z_altman"] == pytest.approx(3.336)
    assert zona_z(r["z_altman"]) == "segura"
    # Sin ganancias acumuladas no hay Z'' (no se inventa).
    sin = {k: v for k, v in BALANCE.items() if "acumuladas" not in k}
    assert compute_ratios(_df({**sin, "Ganancias (pérdidas) de actividades operacionales": 100.0}))["z_altman"] is None


def test_ltm_con_tres_periodos_sinteticos():
    nombre = "Ingresos de actividades ordinarias"
    actual, dic, mismo = {nombre: 120.0}, {nombre: 500.0}, {nombre: 100.0}
    assert ltm_flows(actual, dic, mismo, 6)[nombre] == 520.0           # 120 + 500 − 100
    assert ltm_flows(actual, None, None, 12)[nombre] == 120.0          # en diciembre, el año
    assert nombre not in ltm_flows(actual, dic, None, 6)               # falta un período: no se anualiza
    assert first_values(_df({"a": 1.0}).assign(account_name="a"))["a"] == 1.0


def test_ratios_de_flujo_usan_el_ltm():
    cuentas = {**BALANCE, "Ganancia (pérdida)": 10.0, "Ingresos de actividades ordinarias": 100.0,
               "Ganancias (pérdidas) de actividades operacionales": 20.0}
    acum = compute_ratios(_df(cuentas))
    ltm = compute_ratios(_df(cuentas), ltm={"Ganancia (pérdida)": 80.0, "Ingresos de actividades ordinarias": 400.0,
                                            "Ganancias (pérdidas) de actividades operacionales": 100.0})
    assert acum["roe"] == pytest.approx(2.5) and ltm["roe"] == pytest.approx(20.0)      # 10/400 vs 80/400
    assert ltm["margen_neto"] == pytest.approx(20.0)                                   # 80/400
    # Una cuenta de flujo que no se pudo anualizar deja el ratio en None, no en el acumulado.
    assert compute_ratios(_df(cuentas), ltm={})["roe"] is None


def test_deuda_neta_ebitda_exige_depreciacion():
    base = {**BALANCE, "Ganancias (pérdidas) de actividades operacionales": 100.0,
            "Otros pasivos financieros corrientes": 100.0, "Otros pasivos financieros no corrientes": 300.0,
            "Efectivo y equivalentes al efectivo": 100.0}
    sin = compute_ratios(_df(base))
    assert sin["deuda_neta_ebitda"] is None and not sin["deprec_informada"]    # sin depreciación, EBITDA = EBIT: no se usa
    con = compute_ratios(_df({**base, "Gasto por depreciación y amortización": 50.0}))
    assert con["deuda_neta_ebitda"] == pytest.approx((400 - 100) / 150)        # (deuda − caja) / (EBIT + deprec)
    neg = compute_ratios(_df({**base, "Ganancias (pérdidas) de actividades operacionales": -80.0,
                              "Gasto por depreciación y amortización": 50.0}))
    assert neg["deuda_neta_ebitda"] is None and neg["ebitda_negativo"] is True


def _emp(nombre, sector="Comercio y Retail", **kw):
    return {"company_name": nombre, "rut": nombre, "currency": "CLP", "sector": sector, "excl_fin": False,
            "patrimonio_negativo": False, "ebitda_negativo": False, "margen_neto": 5.0, **kw}


def test_orden_mejores_y_peores_segun_el_sentido_del_indicador():
    u = [_emp("A", endeudamiento=0.5, roe=10.0), _emp("B", endeudamiento=2.0, roe=30.0),
         _emp("C", endeudamiento=1.0, roe=-5.0), _emp("D", endeudamiento=9.0, roe=20.0, patrimonio_negativo=True)]
    con, total = ordenar_ranking(u, "endeudamiento")
    assert [r["company_name"] for r in con] == ["A", "C", "B", "D"]            # el menor es el mejor
    assert con[-1]["texto"] == "Patrimonio negativo"                           # y el patrimonio negativo, el peor
    roe, _ = ordenar_ranking(u, "roe")
    assert [r["company_name"] for r in roe] == ["B", "A", "C"]                  # el mayor es el mejor; D (patrimonio negativo) fuera
    assert total == 4
    assert RATIO_META["endeudamiento"]["mejor"] == "bajo" and RATIO_META["roe"]["mejor"] == "alto"
    assert ordenar_ranking(u, "endeudamiento", sector="Otro")[0] == []


def test_ebitda_negativo_va_al_final():
    u = [_emp("A", deuda_neta_ebitda=3.0), _emp("B", deuda_neta_ebitda=None, ebitda_negativo=True),
         _emp("C", deuda_neta_ebitda=-1.0)]
    con, _ = ordenar_ranking(u, "deuda_neta_ebitda")
    assert [r["company_name"] for r in con] == ["C", "A", "B"] and con[-1]["texto"] == "EBITDA negativo"


# --- Vistas ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(real_db_readonly):
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def universo(real_db_readonly):
    per = real_db_readonly.get_cmf_periods()
    if not per:
        pytest.skip("sin EEFF")
    return real_db_readonly.get_ratios_universo(per[0])


def test_universo_marca_los_sectores_sin_z(universo):
    fin = [r for r in universo if r["excl_fin"]]
    assert fin and all(r["z_altman"] is None and r["deuda_neta_ebitda"] is None for r in fin)
    assert any(r["z_altman"] is not None for r in universo if not r["excl_fin"])


def test_ranking_endeudamiento_mejores_menores_que_peores(universo):
    con, _ = ordenar_ranking(universo, "endeudamiento")
    mejores, peores = con[:15], con[15:][::-1][:15]
    fin = lambda r: r["texto"] is not None
    assert max(r["value"] for r in mejores if not fin(r)) <= min(r["value"] for r in peores if not fin(r))
    assert {r["rut"] for r in mejores}.isdisjoint({r["rut"] for r in peores})


def test_ranking_vista_responde_y_z_no_incluye_financieras(client, universo):
    for m in RATIO_META:
        assert client.get(f"/ranking/table?metric={m}").status_code == 200, m
    html = client.get("/ranking/table?metric=z_altman").text
    assert "15 mejores" in html and "15 peores" in html and "zona--" in html
    con, _ = ordenar_ranking(universo, "z_altman")
    assert con and {r["rut"] for r in universo if r["excl_fin"]}.isdisjoint({r["rut"] for r in con})
    assert client.get("/ranking/table?metric=inexistente").status_code == 200      # cae a ROE, no a 500
    assert "de Altman" in client.get("/ranking").text and "salud-financiera" in client.get("/metodologia").text
