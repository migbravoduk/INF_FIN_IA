"""
api/routers/dashboard_kpi.py — Agregados multi-fuente para el panel del dashboard.

/overview        → KPIs multi-fuente en JSON (Database.get_overview_kpis()).
/overview/cards  → mismo dato como fragmento HTML para refresco vía HTMX.
"""

from fastapi import APIRouter, Depends, Request

from api.deps import get_db, templates
from db.database import Database

router = APIRouter()


@router.get("/overview")
def kpi_overview(db: Database = Depends(get_db)):
    """KPIs multi-fuente (JSON)."""
    return db.get_overview_kpis()


@router.get("/overview/cards")
def kpi_overview_cards(request: Request, db: Database = Depends(get_db)):
    """Fragmento HTML de tarjetas KPI para HTMX (refresco desde overview.html)."""
    return templates.TemplateResponse(
        request, "partials/kpi_cards.html", {"kpi": db.get_overview_kpis()},
    )
