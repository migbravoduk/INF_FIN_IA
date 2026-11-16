"""Humo de la web: cada página de la barra de navegación y los hubs responden 200."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _nav_links():
    base = (ROOT / "api/templates/base.html").read_text(encoding="utf-8")
    return [h for h in re.findall(r'href="(/[^"]*)"', base)
            if not h.startswith("/static") and h != "/docs"]


@pytest.fixture(scope="module")
def client(real_db_readonly):
    # real_db_readonly solo verifica que la base esté disponible; la app abre la suya.
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("path", _nav_links() + [
    "/eeff/corporativos", "/eeff/agf", "/eeff/seguros", "/eeff/intermediarios", "/banca",
    "/afp", "/fondos", "/seguros/cartera", "/evolucion", "/inversion-institucional/emisores",
    "/productos/tabla", "/productos/tabla?tipo=afp&clase=", "/productos/comparar",
])
def test_pagina_responde(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}"


def test_ficha_y_comparador_de_productos(client, real_db_readonly):
    ids = [r[0] for r in real_db_readonly.conn.execute(
        "SELECT producto_id FROM fact_producto_metricas ORDER BY patrimonio_clp_mm DESC NULLS LAST LIMIT 3").fetchall()]
    if not ids:
        pytest.skip("Sin métricas de productos (correr main.py build-productos)")
    for pid in ids:
        assert client.get(f"/producto/{pid}").status_code == 200
    assert client.get("/productos/comparar?ids=" + ",".join(ids)).status_code == 200
    assert client.get("/producto/no-existe").status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/productos?limit=5", "/api/v1/calidad",
                                  "/api/v1/entidades/97004000"])
def test_api_v1(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"
