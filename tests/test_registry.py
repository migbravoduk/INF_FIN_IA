"""
Cableado de fuentes: el catch-up estuvo caído (nov-2026) porque la sonda, el dispatch y el
menú se escribían en tres lugares y un nombre no calzaba. Estos tests impiden que vuelva.
"""
import datetime as dt

from scheduler.freshness import probe_all
from scheduler.sources import (BY_KIND, SOURCES, _months_between, completeness)


def test_kinds_unicos():
    kinds = [s.kind for s in SOURCES]
    assert len(kinds) == len(set(kinds))


def test_toda_fuente_tiene_modo_manual_en_el_menu():
    import actualizar
    faltan = [s.kind for s in SOURCES if s.kind not in actualizar.MANUAL]
    assert not faltan, f"Fuentes sin modo manual en actualizar.MANUAL: {faltan}"
    assert {v[0] for v in actualizar.MENU_FUENTES.values()} == set(BY_KIND)


def test_probe_all_sobre_base_vacia_no_falla(mem_db):
    statuses = probe_all(mem_db, today=dt.date(2026, 11, 16))
    assert statuses, "probe_all no devolvió nada"
    assert not [s for s in statuses if s.note.startswith("error de sonda")]
    # Cada status emitido apunta a una fuente registrada (si no, el dispatch lo ignoraría).
    assert {s.kind for s in statuses} <= set(BY_KIND)
    # En una base vacía todo (salvo BCCh sin credenciales) está pendiente.
    assert all(s.due for s in statuses if s.kind != "bcentral")


def test_probe_all_sobre_la_base_real(real_db_readonly):
    statuses = probe_all(real_db_readonly)
    assert not [s for s in statuses if s.note.startswith("error de sonda")]


def test_months_between():
    assert _months_between(202611, 202702, 10) == [202612, 202701, 202702]
    assert _months_between(202611, 202611, 10) == []
    assert _months_between(None, 202611, 10) == [202611]
    assert _months_between(202601, 202612, 3) == [202602, 202603, 202604]


def test_completeness_detecta_periodo_a_medias():
    # Caso real: EEFF 202606 quedó con 200 empresas cuando los junios anteriores tenían ~380.
    rows = [(202206, 385), (202303, 370), (202306, 385), (202406, 384), (202506, 378), (202606, 200)]
    period, n, ratio = completeness(rows, same_month=True)
    assert period == 202606 and n == 200 and ratio < 0.6
    ok = rows[:-1] + [(202606, 381)]
    assert completeness(ok, same_month=True)[2] >= 0.9
