"""Factores de curva para la ecuación de activos (models/curve_factors.py)."""

import pandas as pd
import pytest

@pytest.fixture(scope="module")
def db(real_db_readonly):
    d = real_db_readonly
    if not d.conn.execute("SELECT COUNT(*) FROM curva_ajuste").fetchone()[0]:
        pytest.skip("sin curvas ajustadas")
    return d


def test_sin_nulos_en_horizonte_y_respeta_as_of(db):
    from models.curve_factors import CURVE_FACTORS, curve_factors
    f = curve_factors(db, "2023-02-28", 21)
    fut = f[f.index > pd.Timestamp("2023-02-01")]
    assert len(fut) == 21
    assert not fut[list(CURVE_FACTORS)].isna().any().any()
    # La historia termina en el mes del corte: nada posterior viene de datos observados
    assert f.index[-22] == pd.Timestamp("2023-02-01")
    assert fut["pendiente"].nunique() == 1   # último valor constante


def test_variante_conectada_al_modelo(db):
    from models.curve_factors import curve_factors
    from models.macro_path import build_macro_path
    from models.structural import _quarterly_factors
    mp = build_macro_path(db, horizon_months=21, as_of="2025-08-31")
    fq = _quarterly_factors(mp, curve_factors(db, "2025-08-31", 21),
                            ["act_yoy", "ipc_yoy", "tasa_real5", "tc_yoy"])
    assert list(fq.columns) == ["act_yoy", "ipc_yoy", "tasa_real5", "tc_yoy"]
    assert fq.tail(6).notna().all().all()
