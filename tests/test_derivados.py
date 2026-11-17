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
