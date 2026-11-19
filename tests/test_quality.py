from db.quality import CHECKS, run_checks


def test_todos_los_controles_corren_sobre_base_vacia(mem_db):
    res = run_checks(mem_db, persist=False)
    rotos = [r for r in res if "no corrió" in (r["detalle"] or "")]
    assert not rotos, rotos
    assert {r["check_id"] for r in res} == {c.check_id for c in CHECKS}


def test_detecta_duplicados_bancarios(mem_db):
    fila = (2023, 8, 202308, "001", "BANCO DE CHILE", "resultado", "415200100", "x", 1.0)
    for _ in range(2):
        mem_db.conn.execute(
            "INSERT INTO cmf_bank_statements (year, month, period, bank_code, bank_name, "
            "report_type, account_code, account_name, val_total) VALUES (?,?,?,?,?,?,?,?,?)", fila)
    res = run_checks(mem_db, persist=False)
    dup = [r for r in res if r["check_id"] == "unicidad_cmf_bank_statements"]
    assert dup[0]["estado"] == "error" and dup[0]["periodo"] == "202308"


def test_persistencia(mem_db):
    run_checks(mem_db, persist=True)
    from db.quality import last_summary
    s = last_summary(mem_db)
    assert s and s["total"] == len(CHECKS)
