"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/" → panel multi-fuente (overview.html), renderizado server-side con datos reales:
      tarjetas KPI (get_overview_kpis) + serie UF de los últimos 90 días para el gráfico.
"""

import datetime as dt

from fastapi import APIRouter, Depends, Request

from api.deps import get_db, records, templates
from db.database import Database

router = APIRouter()


def build_overview_context(db: Database) -> dict:
    """Contexto del panel: KPIs + serie UF (90 días) para el gráfico. Reusable por el export estático."""
    kpi = db.get_overview_kpis()
    since = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    uf_series = records(db.get_series(db.KPI_SERIES["uf"], from_date=since))
    return {"kpi": kpi, "uf_series": uf_series}


@router.get("/")
def overview(request: Request, db: Database = Depends(get_db)):
    """Panel multi-fuente (página principal)."""
    ctx = build_overview_context(db)
    return templates.TemplateResponse(request, "overview.html", ctx)
