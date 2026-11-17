"""Senda macro implícita en swaps (models/market_path.py)."""

import pandas as pd
import pytest

from db.database import Database


@pytest.fixture(scope="module")
def db():
    d = Database(read_only=True)
    n = d.conn.execute("SELECT COUNT(*) FROM curva_ajuste WHERE curva = 'swap_clp'").fetchone()[0]
    if not n:
        pytest.skip("sin curvas ajustadas")
    return d


def test_senda_actual(db):
    from db.curves import ultima_fecha
    from models.market_path import market_path
    mp, det = market_path(db, ultima_fecha(db), 24, 4.0)
    assert len(mp) == 24
    assert mp["tpm"].between(0, 20).all()
    assert mp["ipc_yoy"].between(-2, 15).all()


def test_vintage(db):
    from models.market_path import market_path
    mp, det = market_path(db, "2023-02-28", 24, 11.9)
    assert len(mp) == 24
    assert pd.Timestamp(det["fecha_curva"]) <= pd.Timestamp("2023-02-28")
