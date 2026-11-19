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


def test_exposicion_suma_el_monto_del_alcance(db):
    """La suma de las filas (todos los emisores) = suma directa del monto del alcance, con la medida del período
    (valor final si el período lo trae completo; si no, valor par)."""
    from db.cross import _fx_cte, monto_final_sql, monto_par_sql
    per = _ultimo_b1(db)
    base = db.get_insurer_monto_base(per)
    df = db.get_insurer_issuer_exposure(per, "vida", None, limit=500)
    assert set(df.base) == {base}
    monto = monto_final_sql() if base == "valor_final" else monto_par_sql()
    directo = db.conn.execute(f"""
        WITH {_fx_cte(f"SELECT {per} AS period")}
        SELECT SUM({monto}) FROM cmf_insurer_portfolio_fixed_income fi JOIN fx USING (period)
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


# --- Emisores en común: seguros, fondos mutuos y AFP por RUT (plan 19-nov, F6) -------------

def test_emisores_en_comun_cruza_tres_fuentes(db):
    from db.cross import common_issuers, issuer_holdings
    c = common_issuers(db, limit=500)
    if not c["rows"]:
        pytest.skip("sin carteras")
    por_rut = {r["rut"]: r for r in c["rows"]}
    bch = por_rut["97004000"]                                   # Banco de Chile: las tres fuentes
    assert bch["fuentes"] == 3 and bch["mm_seg"] and bch["mm_ffm"] and bch["mm_afp"]
    assert all(r["fuentes"] >= 2 for r in c["rows"])
    # Tenedores distintos: nunca más compañías que las que informan en el período.
    n_cias = db.conn.execute("SELECT COUNT(DISTINCT rut) FROM cmf_insurer_portfolio_fixed_income "
                             "WHERE period = ?", [c["per_seg"]]).fetchone()[0]
    assert all((r["cias_seg"] or 0) <= n_cias for r in c["rows"])
    # El monto de AFP y de seguros coincide con el bloque de la vista por emisor (mismo RUT y mes).
    h = issuer_holdings(db, "97004000")
    ult = lambda b: [s for s in b["serie"] if s["period"] == b["ult"]][0]["mm"]
    assert abs(bch["mm_afp"] - ult(h["afp"])) < 1e-6 * bch["mm_afp"] + 1e-6
    assert abs(bch["mm_seg"] - ult(h["seguros"])) < 1e-6 * bch["mm_seg"] + 1e-6
    # Seguros excluye mutuos y leasing: los originadores no son «emisores en común».
    assert "99514870" not in por_rut                            # MetLife mutuos hipotecarios
    assert c["cobertura_afp"]["emisores_ok"] <= c["cobertura_afp"]["emisores_total"]


def test_emisores_en_comun_min_fuentes(db):
    from db.cross import common_issuers
    dos, tres = common_issuers(db, limit=500, min_fuentes=2), common_issuers(db, limit=500, min_fuentes=3)
    assert tres["n_emisores"] <= dos["n_emisores"]
    assert all(r["fuentes"] == 3 for r in tres["rows"])


def test_pagina_emisores_en_comun(real_db_readonly):
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    for url in ("/inversion-institucional/emisores", "/inversion-institucional/emisores?min_fuentes=3",
                "/inversion-institucional/emisores/tabla?min_fuentes=3"):
        assert c.get(url).status_code == 200, url
    assert c.get("/inversion-institucional/emisores/tabla?min_fuentes=9").status_code == 422
    assert 'name="min_fuentes"' in c.get("/inversion-institucional/emisores").text


# --- Valor final por posición de B.1 (plan 19-nov, F11) -----------------------------------------

def _linea_b1(valor_final: int, largo: int = 970) -> str:
    """Registro tipo 2 de B.1 mínimo: emisor, tipo, nemotécnico, nominal, unidad y, si es de 970, el valor final."""
    l = [" "] * largo
    l[0] = "2"

    def poner(off, txt):
        l[off:off + len(txt)] = list(txt)
    poner(33, "096596540"); poner(43, "BE"); poner(53, "BCMPC-S"); poner(116, "00000000245000000")
    poner(133, "00000000245000000"); poner(150, "UF")
    if largo == 970:
        poner(893, f"{valor_final:013d}")
    return "".join(l)


def _zip_b1(tmp_path, lineas):
    import zipfile
    ruta = tmp_path / "202609v.zip"
    cab = "1" + "099279000" + "0" + "EUROAMERICA".ljust(60) + "202609"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("i202609v.99279000", "\n".join([cab, *lineas]) + "\n")
    return ruta


def test_parser_b1_lee_el_valor_final_solo_en_registros_de_970(tmp_path):
    from collectors.seguros_cartera import parse_zip_fixed_income
    r970, r930 = parse_zip_fixed_income(_zip_b1(tmp_path, [_linea_b1(3_500_000), _linea_b1(0, 930)]))
    assert r970["valor_final_m"] == 3_500_000.0                      # M$, 13 dígitos, sin decimales
    assert r970["tipo_instrumento"] == "BE" and r970["valor_nominal"] == 24500.0 and r970["pais"] is None
    assert r930["valor_final_m"] is None and r930["tipo_instrumento"] == "BE"   # el formato antiguo otro orden: no se lee


def test_migracion_agrega_valor_final_m(mem_db):
    cols = [r[0] for r in mem_db.conn.execute("DESCRIBE cmf_insurer_portfolio_fixed_income").fetchall()]
    assert "valor_final_m" in cols
    assert "valor_final_m" in [r[0] for r in mem_db.conn.execute("DESCRIBE v_insurer_fixed_income").fetchall()]


def test_base_de_monto_por_periodo(mem_db):
    ins = lambda per, vf: mem_db.conn.execute("""INSERT INTO cmf_insurer_portfolio_fixed_income
        (insurance_type, period, rut, rut_emisor, tipo_instrumento, nemotecnico, valor_nominal, unidad_monetaria, valor_final_m)
        VALUES ('vida', ?, '1', '2', 'BE', 'X', 10, 'UF', ?)""", [per, vf])
    ins(202601, None); ins(202601, None)                  # período sin recargar
    ins(202602, 5.0); ins(202602, 7.0)                    # período completo
    ins(202603, 5.0); ins(202603, None)                   # período a medias: no se mezclan medidas
    assert mem_db.get_insurer_monto_base(202601) == "par"
    assert mem_db.get_insurer_monto_base(202602) == "valor_final"
    assert mem_db.get_insurer_monto_base(202603) == "par"
    assert mem_db.get_insurer_monto_base(209912) == "par"  # sin filas


def test_valor_final_reconcilia_con_el_b8(db):
    """Criterio de F11: la suma del valor final de los tipos de renta fija nacional por compañía coincide (±1%) con el
    valor final de la renta fija nacional del B.8, en ≥ 90% de las compañías (medido en sep-2026: 61 de 63)."""
    from api.insurer_glossary import describe
    per = _ultimo_b1(db)
    if db.get_insurer_monto_base(per) != "valor_final":
        pytest.skip("el último período no está recargado con valor_final_m")
    tipos = ("AFROIND", "BB", "BBP", "BBPNEE", "BCU", "BE", "BEC", "BNEE", "BS", "BTP", "BTU", "BU", "CS", "DPC", "DPL",
             "LH", "MHA", "MHB", "PDBC")
    f = {(r[0], r[1]): r[2] for r in db.conn.execute(
        f"SELECT insurance_type, rut, SUM(valor_final_m) FROM cmf_insurer_portfolio_fixed_income WHERE period = ? "
        f"AND tipo_instrumento IN ({','.join('?' * len(tipos))}) GROUP BY 1, 2", [per, *tipos]).fetchall()}
    rf = {"rf_estatal", "rf_bancaria", "rf_corporativa", "rf_hipotecaria"}
    b8 = {}
    for t, rut, cod, v in db.conn.execute("SELECT insurance_type, rut, investment_code, valor_final "
                                          "FROM cmf_insurer_portfolio_control WHERE period = ?", [per]).fetchall():
        d = describe(cod)
        if d["clase"] in rf and d["geo"] == "nacional":
            b8[(t, rut)] = b8.get((t, rut), 0) + v
    ok = [abs(f.get(k, 0) / v - 1) < 0.01 for k, v in b8.items() if v > 0]
    assert len(ok) >= 20 and sum(ok) / len(ok) >= 0.90
