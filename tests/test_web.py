"""Humo de la web: cada página de la barra de navegación y los hubs responden 200."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _nav_links():
    """URLs del menú. Desde el rediseño el menú se arma con `api.deps.NAV` (base.html ya no
    tiene los enlaces escritos a mano); se suman los href literales que queden en base.html."""
    from api.deps import NAV
    urls = [g["url"] for g in NAV if "url" in g] + [h["url"] for g in NAV for h in g.get("hijos", [])]
    base = (ROOT / "api/templates/base.html").read_text(encoding="utf-8")
    urls += [h for h in re.findall(r'href="(/[^"]*)"', base)
             if not h.startswith("/static") and h != "/docs"]
    return list(dict.fromkeys(urls))


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


# ---------------------------------------------------------------------------------------------
# Menú y filtros de presentación (docs/PLAN_ESTETICA.md F2-F3). No requieren base de datos.
# ---------------------------------------------------------------------------------------------
def _activas(path):
    from api.deps import _nav_activo, _nav_urls
    return [u for u, _ in _nav_urls() if _nav_activo(path, u)]


def test_menu_marca_una_sola_pagina_activa():
    # Cada URL del menú se marca a sí misma, y solo a sí misma.
    from api.deps import _nav_urls
    for url, _ in _nav_urls():
        assert _activas(url) == [url], url


@pytest.mark.parametrize("path, esperada", [
    ("/inversion-institucional/derivados", "/inversion-institucional/derivados"),  # no marca al hub
    ("/inversion-institucional/emisor?rut=1", "/inversion-institucional/emisor"),
    ("/evolucion", "/ranking"),            # prefijo extra declarado en NAV
    ("/producto/ffmm:1:A", "/productos"),
    ("/eeff/corporativos", "/eeff"),
])
def test_menu_activo_en_subpaginas(path, esperada):
    assert _activas(path.split("?")[0]) == [esperada]


def test_menu_renderiza_grupos_y_aria_current(client):
    html = client.get("/derivados").text
    assert html.count('class="nav-pop"') == 4
    assert html.count('aria-current="page"') == 1


@pytest.mark.parametrize("crudo, legible", [
    ("BANCO DE CRÉDITO E INVERSIONES", "Banco de Crédito e Inversiones"),
    ("AFP PLANVITAL S.A.", "AFP PlanVital S.A."),
    ("FM ACCIONES EEUU", "FM Acciones EEUU"),
    ("Banco de Chile", "Banco de Chile"),       # si no viene en mayúsculas, no se toca
    ("", ""),
])
def test_filtro_nombre(crudo, legible):
    from api.deps import _nombre
    assert _nombre(crudo) == legible


def test_filtro_fecha_es():
    from api.deps import _fecha_es
    assert _fecha_es("2026-11-15") == "15 nov 2026"
    assert _fecha_es("202609") == "202609"      # no es fecha completa: se devuelve tal cual


def test_nombre_js_igual_a_python():
    # INF.nombre (api/static/charts.js) repite las listas del filtro `nombre`; deben coincidir.
    import json
    from api.deps import _ESPECIALES, _MENORES, _SIGLAS
    js = (ROOT / "api/static/charts.js").read_text(encoding="utf-8")

    def lista(nombre):
        m = re.search(r"var " + nombre + r" = (\[.*?\]|\{.*?\});", js, re.S)
        assert m, nombre
        return json.loads(m.group(1))
    assert set(lista("NOMBRE_SIGLAS")) == _SIGLAS
    assert lista("NOMBRE_ESPECIALES") == _ESPECIALES
    assert set(lista("NOMBRE_MENORES")) == _MENORES


def test_bancos_en_pesos_en_ambos_planes(real_db_readonly):
    # El plan de cuentas bancario anterior a 2022 viene en millones; la web lo homologa a pesos.
    db = real_db_readonly
    fila = db.conn.execute("""
        SELECT bank_code, account_name FROM cmf_bank_statements
        WHERE report_type = 'balance' AND val_total > 0
        GROUP BY 1, 2 HAVING MIN(period) <= 202112 AND MAX(period) >= 202201
           AND COUNT(*) FILTER (WHERE period IN (202112, 202201)) = 2
        ORDER BY MAX(abs(val_total)) DESC LIMIT 1""").fetchone()
    if not fila:
        pytest.skip("Sin cuentas bancarias presentes en ambos planes")
    s = db.get_bank_account_series(fila[0], fila[1], "balance").set_index("period")["value"]
    razon = s[202201] / s[202112]
    assert 0.5 < razon < 2, f"{fila}: salto ×{razon:,.0f} entre dic-2021 y ene-2022"
    crudo = db.query_bank_statements(bank_code=fila[0], period=202112, report_type="balance", limit=5)
    pesos = db.query_bank_statements(bank_code=fila[0], period=202112, report_type="balance", limit=5, en_pesos=True)
    assert (pesos["val_total"] == crudo["val_total"] * 1e6).all()


def test_cuentas_fecu_legibles(real_db_readonly):
    # La CMF publica los encabezados FECU sin espacios; config/fecu_cuentas.yaml debe cubrirlos todos.
    from db.repos.fecu import nombre_cuenta_fecu
    textos = [r[0] for t, c in [("cmf_insurer_statements", "account_name"), ("cmf_broker_statements", "account_name"),
                                ("cmf_broker_statements", "section")]
              for r in real_db_readonly.conn.execute(f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL").fetchall()]
    pegados = [t for t in textos if re.search(r"[a-záéíóúñ]{19,}", nombre_cuenta_fecu(t))]   # ninguna palabra real llega a 19 letras
    assert not pegados, f"{len(pegados)} cuentas FECU sin glosario, p. ej. {pegados[:3]}"


def test_nombre_siglas_con_guion():
    from api.deps import _nombre
    assert _nombre("CMB-PRIME ADMINISTRADORA GENERAL DE FONDOS S.A.") == "CMB-Prime Administradora General de Fondos S.A."
    assert _nombre("SANTANDER-CHILE") == "Santander-Chile"
    assert _nombre("MBI ADMINISTRADORA GENERAL DE FONDOS S.A.") == "MBI Administradora General de Fondos S.A."


# --- Portada: indicadores de mercado con fecha y fuente (plan 19-nov, F10) ------------------

def test_portada_y_tarjetas_responden(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/kpi/overview/cards").status_code == 200


def test_tarjetas_originales_muestran_fecha_y_fuente(client):
    html = client.get("/api/kpi/overview/cards").text
    bloques = {b.split("</div>")[0].strip(): b for b in html.split('<div class="stat">')[1:]}
    for etiqueta in ("PIB (var. anual)", "IMACEC (var. anual)", "IPC a 12 meses", "USD/CLP"):
        bloque = next(b for k, b in bloques.items() if k.startswith(etiqueta.split(" (")[0]) or etiqueta in k)
        assert 'class="corte"' in bloque and "Banco Central" in bloque, etiqueta


def test_indicadores_de_mercado_nuevos(client, real_db_readonly):
    mk = real_db_readonly.get_overview_kpis()["mercado2"]
    for clave in ("tpm", "pendiente", "inflacion_esperada", "eee_ipc_11m", "spread_depositos", "cobre", "ipsa",
                  "ust_10y", "fed", "desempleo"):
        assert clave in mk                                  # (None si la serie faltara, pero la clave existe)
    ok = [c for c, v in mk.items() if v]
    assert len(ok) >= 8 and all(mk[c]["fecha"] for c in ok)     # cada uno con su propia fecha
    html = client.get("/api/kpi/overview/cards").text
    for texto in ("Tasa de política monetaria", "Pendiente swap pesos", "Inflación esperada a 2 años", "Cobre", "IPSA",
                  "Desempleo", "Depósitos − swap a 90 días", "Tasa de la Reserva Federal"):
        assert texto in html, texto
    # La API JSON conserva las claves anteriores y suma las nuevas (sin NaN: debe ser JSON válido).
    j = client.get("/api/kpi/overview").json()
    assert {"macro", "banca", "afp_returns", "mercado", "curva_soberana", "tasas_colocacion", "mercado2"} <= set(j)
    assert j["macro"]["fechas"]["usd"]
