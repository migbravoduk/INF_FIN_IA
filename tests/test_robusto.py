"""Aislamiento de atípicos y tope a proyecciones explosivas (models/robusto.py)."""

import numpy as np
import pandas as pd

from models.robusto import acotar, es_explosiva, limpiar_outliers, naive_estacional


def _serie(n=40, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    estac = np.tile([10.0, -5.0, 0.0, 20.0], n // 4 + 1)[:n]
    y = 100 + 0.8 * t + estac + rng.normal(0, 1.5, n)
    return pd.Series(y, index=[f"{2015 + i // 4}{3 * (i % 4 + 1):02d}" for i in range(n)])


def test_aisla_un_castigo_unico():
    s = _serie()
    p = s.index[22]
    s_mal = s.copy()
    s_mal[p] -= 150.0                       # castigo de una vez
    limpia, marcados = limpiar_outliers(s_mal, 4)
    assert marcados == [p]
    assert abs(limpia[p] - s[p]) < 10       # vuelve a la vecindad del dato sano
    assert (limpia.drop(p) == s_mal.drop(p)).all()


def test_no_toca_una_serie_sana_ni_un_cambio_de_nivel():
    s = _serie()
    assert limpiar_outliers(s, 4)[1] == []
    salto = s.copy()
    salto.iloc[20:] += 80.0                 # fusión: cambio de nivel persistente
    _, marcados = limpiar_outliers(salto, 4)
    assert marcados == []                   # varios residuos grandes seguidos: quiebre, no atípico


def test_conserva_huecos():
    s = _serie()
    s.iloc[10] = np.nan
    limpia, _ = limpiar_outliers(s, 4)
    assert np.isnan(limpia.iloc[10])


def test_acotar_reemplaza_solo_lo_explosivo():
    s = _serie()
    sana = np.array([135.0, 120.0, 128.0, 150.0])
    m, aco = acotar(sana, s, 4)
    assert not aco and np.allclose(m, sana)
    loca = np.array([135.0, 1e6, 1e8, 1e9])
    assert es_explosiva(loca, s)
    m, aco = acotar(loca, s, 4)
    assert aco and np.allclose(m, naive_estacional(s, 4, 4))
    m, aco = acotar(loca, s, 4, stock=True)
    assert aco and np.allclose(m, s.iloc[-1])
