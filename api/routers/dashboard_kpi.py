"""
api/routers/dashboard_kpi.py — Agregados multi-fuente para el panel del dashboard.

Endpoint principal del ejemplo inicial (panel multi-fuente). Devuelve JSON para
consumo programático y un fragmento HTML (partials/kpi_cards.html) para HTMX.

Pendiente en la capa DB (stub): Database.get_overview_kpis().
ESQUELETO: usa datos mock; estructura idéntica al contrato real.
"""

from fastapi import APIRouter, Request

from api.deps import templates

router = APIRouter()


# Estructura de KPIs compartida (mock). Cuando se codifique:
#   return db.get_overview_kpis()
_MOCK_OVERVIEW = {
    "macro": {"uf": 39152.10, "usd_clp": 945.30, "ipc_v12": 4.1},
    "banca": {"total_activos_sistema": 3.45e14, "period": 202512},
    "afp": [
        {"afp_name": "HABITAT", "quota_value": 75123.45},
        {"afp_name": "CAPITAL", "quota_value": 74980.10},
        {"afp_name": "PROVIDA", "quota_value": 74010.77},
    ],
    "mercado": {"n_instrumentos": 1287, "date": "2026-06-04"},
}


# NOTA: en el esqueleto estos endpoints devuelven mock SIN tocar la BD, para que el
# dashboard sea demostrable aunque la BD esté bloqueada por el scheduler (ver deps.py).
# Al implementar: añadir `db: Database = Depends(get_db)` y `return db.get_overview_kpis()`.

@router.get("/overview")
def kpi_overview():
    """KPIs multi-fuente (JSON). TODO: añadir Depends(get_db) y return db.get_overview_kpis()."""
    return _MOCK_OVERVIEW


@router.get("/overview/cards")
def kpi_overview_cards(request: Request):
    """
    Fragmento HTML de tarjetas KPI para HTMX (hx-get desde overview.html).
    TODO: alimentar con db.get_overview_kpis() en vez del mock.
    """
    return templates.TemplateResponse(
        request, "partials/kpi_cards.html", {"kpi": _MOCK_OVERVIEW},
    )
