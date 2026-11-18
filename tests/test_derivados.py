"""Monitor de derivados institucionales (docs/PLAN_DERIVADOS.md)."""

import pytest

# Registros reales del archivo B.7 (Circular 1.835, 202609 vida), recortados al largo oficial.
FWD = ("3CBE  FWV       02000002013600120251215202612300970110003                    BANCO INTERNACIONAL"
       "                                         CLNR02SI01SIAA             VIDA NO APLICA                     "
       "NO APLICA                     $$                            PROM                          "
       "0001822800000000000000200000000000000018228000000001945200PROM  00000009114000000000019452000000000000"
       "972600000000097378671+0000000004557-0000000123376-0000000123376BLOOMBERG                     0000000000000"
       "              00000000000000000000123376-0000000123376NOOTRCLA                            ")
SWAP = ("51512 S         01000001201100120210924203104270970040005                    BANCO DE CHILE"
        "                                              CLNR02SI01SIAAA            "
        "0000001075554000000097260000000000261964860000001000000000UF    PROM  FIJA 3.38                     "
        "FIJA 3.9                      01010000000972600000000000352900000000040910000000972600000000108560600000"
        "009826290000000972600+0000000102978-0000210405546BLOOMBERG                     VIDA NO APLICA          "
        "           NO APLICA                     0000000000000              00000001029780000000000000+0000000102978SICA    ")


def test_b7_forward():
    from collectors.seguros_cartera import _b7_record
    r = _b7_record(FWD)
    assert r["tipo"] == "forward" and r["objetivo"] == "CBE" and r["tipo_operacion"] == "FWV"
    assert r["contraparte"] == "BANCO INTERNACIONAL" and r["rut_contraparte"] == "97011000"
    assert (r["moneda_larga"], r["moneda_corta"]) == ("$$", "PROM")
    assert r["unidades_corta"] == 2_000_000 and r["precio"] == pytest.approx(911.4)
    # Precio implícito de la pata en dólares = dólar observado del cierre
    assert r["nocional_corta_m"] * 1e3 / r["unidades_corta"] == pytest.approx(972.6)
    assert r["valor_razonable_m"] == -123376


def test_b7_swap():
    from collectors.seguros_cartera import _b7_record
    r = _b7_record(SWAP)
    assert r["tipo"] == "swap" and (r["moneda_larga"], r["moneda_corta"]) == ("UF", "PROM")
    assert r["unidades_corta"] == 1_000_000
    assert r["nocional_corta_m"] * 1e3 / r["unidades_corta"] == pytest.approx(972.6)
    assert r["nocional_larga_m"] * 1e3 / r["unidades_larga"] == pytest.approx(41057.2, rel=1e-4)
    assert r["valor_razonable_m"] == 102978


@pytest.fixture(scope="module")
def db(real_db_readonly):
    n = real_db_readonly.conn.execute(
        "SELECT COUNT(*) FROM series WHERE category = 'derivados_sector'").fetchone()[0]
    if not n:
        pytest.skip("sin series de derivados")
    return real_db_readonly


def test_identidades_bcch(db):
    import db.derivados as D
    for i in D.identidades(db):
        assert i["meses"] > 0, i["regla"]
        assert i["desvio_max"] < 0.005, i      # cuadran al 0,5% (redondeo del BCCh)


def test_monitor_marca_episodio_2026(db):
    # Control de cordura (no validación: el episodio ya se conocía al fijar las reglas).
    import db.derivados as D
    m = D.monitor_afp(db)
    meses = {a["mes"] for a in m["alertas"] if a["serie"].startswith("Swaps sobre tasas")}
    assert {"2026-04", "2026-05", "2026-06"} <= meses


def test_neto_ffmm_mismo_signo_que_bcch(db):
    from db.derivados_ffmm import resumen_ffmm
    s = resumen_ffmm(db)["serie"]
    pares = [(a, b) for a, b in zip(s["neto_usd"], s["bcch_agf"]) if b is not None and abs(b) > 100]
    assert pares and sum((a < 0) == (b < 0) for a, b in pares) / len(pares) >= 0.9


# --- Vista /derivados (db/derivados_global.py) ---------------------------------------------

def test_global_identidades_por_sector(db):
    import db.derivados_global as G
    ids = G.identidades(db)
    if not ids:
        pytest.skip("sin series de bancos por contraparte")
    for i in ids:
        assert abs(i["dif"]) < 1.0, i          # MMUSD: cuadran salvo redondeo


def test_global_espejo_banco_sector(db):
    # La posición del sector = − la del banco frente a él (AFP se reporta a sí misma).
    import db.derivados as D
    w = D._frame(db, {"b": G_P + "40.R.42.NET.Z.MMUSD.CLPUSD.Z.Z.0.M",
                      "s": G_P + "42.R.40.NET.Z.MMUSD.CLPUSD.Z.Z.0.M"}).dropna()
    if w.empty:
        pytest.skip("sin series")
    assert ((w["b"] + w["s"]).abs() < 1.0).all()


def test_global_dv01_suma_cero(db):
    import db.derivados_global as G
    t = G.tasas(db)
    if not t:
        pytest.skip("sin swap cámara por plazo")
    dv = t["dv01"]
    assert abs(dv["residentes"] + dv["no_residentes"] + dv["bancos"]) < 1.0


def test_global_cobertura_afp_razonable(db):
    import db.derivados_global as G
    a = G.coberturas_afp(db)
    if not a:
        pytest.skip("sin cartera SP")
    assert 0 < a["total"]["cobertura"] < 100
    # Más conservador, más cubierto (A < C < E)
    c = {f["fondo"]: f["cobertura"] for f in a["por_fondo"]}
    assert c["A"] < c["C"] < c["E"]


G_P = "F099.DER.STO.Z."


def test_alertas_agrupan_por_afp():
    from db.derivados_global import alertas
    afp = {"filas": [{"afp": "CAPITAL", "fondo": f, "cobertura": cob, "swaps_pct": sw}
                     for f, cob, sw in (("A", 15, -1.2), ("C", 40, -2.5), ("E", 105, -0.5))]}
    out = alertas({}, afp, {}, {})
    swaps = [a for a in out if "swaps" in a["texto"]]
    assert len(swaps) == 1 and swaps[0]["nivel"] == "alto" and "A, C" in swaps[0]["texto"]
    assert any("105%" in a["texto"] for a in out)          # sobrecobertura del Fondo E
