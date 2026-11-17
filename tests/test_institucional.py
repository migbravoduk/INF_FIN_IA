"""Inversión institucional: fecha común de AFP, tenencias por emisor y clasificación sectorial."""

import pytest


def test_sector_curado_manda_sobre_nombre():
    from api.company_profiles import sector_of
    # El nombre no dice el giro: antes quedaban en "Otros / No Clasificado".
    assert sector_of("85741000", "BICECORP S.A.") == "Servicios Financieros"
    assert sector_of("79619200", "CONSORCIO FINANCIERO S.A.") == "Servicios Financieros"
    assert sector_of("90749000", "FALABELLA S.A.") == "Comercio y Retail"
    # La clasificación anterior (la del modelo estructural) sigue disponible e intacta.
    assert sector_of("90749000", "FALABELLA S.A.", "nombre") == "Otros / No Clasificado"
    assert sector_of("79619200", "CONSORCIO FINANCIERO S.A.", "nombre") == "Otros / No Clasificado"


def test_modelo_usa_clasificacion_anterior():
    # La curada perdió el backtest (17-nov-2026); el shrinkage sigue con la de nombre.
    import models.structural as st
    assert st.SECTOR_FUENTE == "nombre"


@pytest.fixture(scope="module")
def db(real_db_readonly):
    return real_db_readonly


def test_fecha_comun_afp(db):
    corte = db.sp_common_date("A")
    if corte is None:
        pytest.skip("sin valores cuota")
    maximos = db.conn.execute("""SELECT MAX(date) FROM sp_quota_values WHERE fund_type = 'A'
                                 AND afp_name <> 'TOTAL' GROUP BY afp_name""").fetchall()
    assert corte == str(min(m[0] for m in maximos))


def test_cartera_afp_suma_100(db):
    periodos = db.get_portfolio_periods()
    if not periodos:
        pytest.skip("sin cartera SP")
    df = db.get_portfolio_composition(periodos[0], "A", "TOTAL", 50)
    # Sin subtotales dentro de las secciones (antes sumaba 100,9%).
    assert abs(df["porcentaje"].sum() - 100) < 0.5


def test_tenencias_emisor(db):
    from db.cross import issuer_holdings
    h = issuer_holdings(db, "97004-000".replace("-", ""))   # Banco de Chile
    if not h["periodos"]:
        pytest.skip("sin carteras")
    assert h["seguros"] and h["ffmm"]
    for b in (h["seguros"], h["ffmm"]):
        ult = [s for s in b["serie"] if s["period"] == b["ult"]][0]
        assert ult["mm"] > 0 and ult["tenedores"] > 0
        assert abs(sum(f["peso"] for f in b["tenedores"]) - 100) < 1e-6
