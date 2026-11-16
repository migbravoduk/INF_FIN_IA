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
    "/afp", "/fondos", "/seguros/cartera", "/evolucion",
])
def test_pagina_responde(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}"
