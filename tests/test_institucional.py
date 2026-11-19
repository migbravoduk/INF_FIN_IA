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


def _b1(db, filas):
    """Inserta posiciones B.1 mínimas: (compañía, rut_emisor, tipo, nemotécnico)."""
    db.conn.executemany("""INSERT INTO cmf_insurer_portfolio_fixed_income
        (insurance_type, period, rut, rut_emisor, tipo_instrumento, nemotecnico, valor_nominal, unidad_monetaria)
        VALUES ('vida', 202609, ?, ?, ?, ?, 1000, 'UF')""", filas)


def _canon(db):
    from db.cross import emisor_canonico_cte
    return dict(db.conn.execute(
        f"WITH {emisor_canonico_cte('SELECT 202609 AS period')} SELECT nemo, rut_canon FROM canon").fetchall())


def test_emisor_canonico_mayoria(mem_db):
    # Caso real (202609): una compañía informó BCMPC-S con el RUT de Empresas CMPC; las demás,
    # con el de Inversiones CMPC. Gana el mayoritario.
    _b1(mem_db, [("1", "96596540", "BE", "BCMPC-S"), ("2", "96596540", "BE", "BCMPC-S"),
                 ("3", "090222000", "BE", "bcmpc-s "), ("1", "97004000", "BB", "BCHI-X")])
    c = _canon(mem_db)
    assert c == {"BCMPC-S": "96596540"}          # BCHI-X tiene un solo RUT: no necesita resolverse


def test_emisor_canonico_empate_y_originadores(mem_db):
    # Empate 1 a 1: decide el RUT que usa la cartera de fondos mutuos para ese nemotécnico.
    # (RUT ficticios: los de bancos fusionados ya se unifican antes de votar.)
    _b1(mem_db, [("1", "11111111", "BB", "BX-1"), ("2", "22222222", "BB", "BX-1"),
                 # Mutuos y leasing: el nemotécnico es del originador; nunca se reasignan.
                 ("1", "111", "MHA", "MHA-1"), ("2", "222", "MHA", "MHA-1"), ("3", "222", "MHA", "MHA-1")])
    mem_db.conn.execute("""INSERT INTO cmf_mutual_fund_portfolio
        (period_year, period_month, portfolio_type, fund_run, fund_name, raw_data)
        VALUES (2026, 9, 'NACI', '9000', 'F', '{"FFM_6010100": "BX-1", "FFM_6010211": "22222222"}')""")
    assert _canon(mem_db) == {"BX-1": "22222222"}


@pytest.fixture(scope="module")
def db(real_db_readonly):
    return real_db_readonly


def test_nombres_curados(db):
    nombres = dict(db.conn.execute("""SELECT rut, nombre_canonico FROM dim_entidad
                                      WHERE rut IN ('96549050', '99514870')""").fetchall())
    assert nombres.get("96549050") == "Seguros de Vida SURA S.A."
    assert nombres.get("99514870") == "MetLife Chile Administradora de Mutuos Hipotecarios S.A."


def test_fusiones_config():
    from db.cross import fusion_sql, fusiones, ruts_equivalentes
    f = fusiones()
    assert f["76645030"] == "97023000" and f["97053000"] == "97080000"   # Itaú–Corpbanca, BICE–Security
    assert f["97032000"] == f["97051000"] == "97018000"                  # ex BBVA y ex Desarrollo → Scotiabank
    assert set(ruts_equivalentes("97023000")) >= {"97023000", "76645030", "97041000"}
    assert "WHEN '76645030' THEN '97023000'" in fusion_sql("x")


def test_fusion_itau(db):
    from db.cross import issuer_holdings
    viejo = issuer_holdings(db, "76645030")
    assert viejo["sucesor"] == "97023000" and not viejo["seguros"] and not viejo["ffmm"]
    itau = issuer_holdings(db, "97023000")
    if not itau["periodos"]:
        pytest.skip("sin carteras")
    assert "76645030" in itau["absorbidos"] and itau["seguros"]


def test_originadores_con_nombre(db):
    """Todo originador de mutuos y leasing del último B.1 tiene nombre (curados el 19-nov-2026)."""
    per = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    if per is None:
        pytest.skip("sin B.1")
    faltan = db.conn.execute("""
        SELECT DISTINCT ltrim(fi.rut_emisor, '0') FROM cmf_insurer_portfolio_fixed_income fi
        LEFT JOIN dim_entidad e ON e.rut = ltrim(fi.rut_emisor, '0')
        WHERE fi.period = ? AND fi.tipo_instrumento IN ('MHA', 'MHB', 'CLEAS') AND e.rut IS NULL""",
        [per]).fetchall()
    assert not faltan, f"originadores sin nombre en {per}: {[r[0] for r in faltan]} (agregarlos a config/entidades.yaml)"


def test_cmpc_renta_fija_va_a_inversiones_cmpc(db):
    """Empresas CMPC no emite bonos: el BCMPC-S que EuroAmerica le atribuyó es de Inversiones CMPC."""
    from db.cross import issuer_holdings
    per = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    if per is None or per < 202609:
        pytest.skip("sin B.1 de 202609 o posterior")
    emp = issuer_holdings(db, "90222000")
    seg = emp["seguros"]
    assert seg is None or not any(f["tenedor"] == "99279000" and (f["ult"] or 0) > 0 for f in seg["tenedores"])
    inv = issuer_holdings(db, "96596540")
    assert inv["seguros"] and any(f["tenedor"] == "99279000" for f in inv["seguros"]["tenedores"])
    if per == 202609:
        assert emp["reasignados"]["salen_nemos"] == ["BCMPC-S"]
        assert "BCMPC-S" in inv["reasignados"]["entran_nemos"]


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


# --- Concentración por emisor de seguros: monto expuesto y detalle (plan 19-nov, F4) ----------

def _ultimo_b1(db, ramo="vida"):
    per = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income "
                          "WHERE insurance_type = ?", [ramo]).fetchone()[0]
    if per is None:
        pytest.skip("sin B.1")
    return int(per)


def test_exposicion_suma_el_monto_par_del_alcance(db):
    """La suma de las filas (todos los emisores) = suma directa del valor par del alcance."""
    from db.cross import _fx_cte, monto_par_sql
    per = _ultimo_b1(db)
    df = db.get_insurer_issuer_exposure(per, "vida", None, limit=500)
    directo = db.conn.execute(f"""
        WITH {_fx_cte(f"SELECT {per} AS period")}
        SELECT SUM({monto_par_sql()}) FROM cmf_insurer_portfolio_fixed_income fi JOIN fx USING (period)
        WHERE fi.period = ? AND fi.insurance_type = 'vida' AND fi.rut_emisor IS NOT NULL""", [per]).fetchone()[0]
    assert len(df) == db.count_insurer_issuers(per, "vida")        # limit=500 cubre a todos
    assert abs(df.mm.sum() - directo) / directo < 0.005
    assert df.mm.is_monotonic_decreasing                           # ordenado por monto, no por conteo


def test_detalle_de_emisor_suma_la_fila_madre(db):
    per = _ultimo_b1(db)
    top = db.get_insurer_issuer_exposure(per, "vida", None, limit=5)
    for r in top.itertuples():
        det = db.get_insurer_issuer_detail(per, "vida", r.rut_emisor)
        assert abs(det.mm.sum() - r.mm) <= max(1.0, r.mm * 1e-6)
        assert int(det.posiciones.sum()) == r.posiciones and len(det) == r.companias
        # y el de una compañía suma lo mismo que su fila en el detalle de mercado
        cia = det.tenedor.iloc[0]
        por_nemo = db.get_insurer_issuer_detail(per, "vida", r.rut_emisor, cia)
        assert abs(por_nemo.mm.sum() - det.mm.iloc[0]) <= max(1.0, det.mm.iloc[0] * 1e-6)


def test_detalle_rechaza_emisor_no_numerico(db):
    assert db.get_insurer_issuer_detail(202609, "vida", "1' OR '1'='1").empty


def test_endpoints_de_concentracion_por_emisor(real_db_readonly):
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    per = _ultimo_b1(real_db_readonly)
    top = real_db_readonly.get_insurer_issuer_exposure(per, "vida", None, limit=1).rut_emisor.iloc[0]
    cia = real_db_readonly.get_insurer_issuer_detail(per, "vida", top).tenedor.iloc[0]
    for url in ("/seguros/cartera/emisores?ramo=vida", f"/seguros/cartera/emisores?ramo=vida&rut={cia}",
                "/seguros/cartera/emisores?ramo=vida&limit=500",
                f"/seguros/cartera/emisores/detalle?ramo=vida&emisor={top}",
                f"/seguros/cartera/emisores/detalle?ramo=vida&rut={cia}&emisor={top}"):
        r = c.get(url)
        assert r.status_code == 200, url
    assert 'class="emisor-det"' in c.get("/seguros/cartera/emisores?ramo=vida").text
    assert c.get("/seguros/cartera/emisores?limit=9999").status_code == 422     # tope de 500


# --- Tipo de instrumento en la exposición por emisor (plan 19-nov, F5) ------------------------

def test_catalogo_de_tipos_de_instrumento():
    from db.cross import etiqueta_tipo, familia_de
    assert familia_de("BE") == familia_de("DEB") == "Bono corporativo"
    assert familia_de("acc") == "Acción"                       # sin distinguir mayúsculas
    assert familia_de("MHA") == "Mutuo hipotecario endosable" and familia_de("CLEAS") == "Leasing inmobiliario"
    assert familia_de("ZZZ") is None and familia_de(None) is None
    assert etiqueta_tipo("ZZZ") == "ZZZ (sin glosa)"           # lo no documentado se muestra crudo
    assert etiqueta_tipo(None) == "sin tipo informado"


def test_tipos_empresas_cmpc_no_emite_bonos(db):
    """Empresas CMPC solo tiene acciones; sus bonos son de Inversiones CMPC (DEB en la SP, BE en B.1)."""
    from db.cross import issuer_holdings
    emp = issuer_holdings(db, "90222000")
    if emp["afp"]:
        assert [t["codigo"] for t in emp["afp"]["por_tipo"]] == ["ACC"]
    if emp["ffmm"]:
        assert [t["codigo"] for t in emp["ffmm"]["por_tipo"]] == ["ACC"]
    inv = issuer_holdings(db, "96596540")
    if inv["seguros"]:
        assert inv["seguros"]["por_tipo"][0]["codigo"] == "BE"
    if inv["afp"]:
        assert inv["afp"]["por_tipo"][0]["codigo"] == "DEB"


def test_por_tipo_suma_el_total_del_bloque(db):
    from db.cross import issuer_holdings
    h = issuer_holdings(db, "97004000")                         # Banco de Chile: las tres fuentes
    for k in ("seguros", "ffmm", "afp"):
        b = h[k]
        if not b:
            continue
        total = [s for s in b["serie"] if s["period"] == b["ult"]][0]["mm"]
        assert abs(sum(t["mm"] for t in b["por_tipo"]) - total) / total < 0.005, k
        assert abs(sum(t["pct"] for t in b["por_tipo"]) - 100) < 1e-6, k
        assert all("tipos" in f for f in b["tenedores"])
