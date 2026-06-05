"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX).

"/"      → panel multi-fuente (overview.html), ejemplo inicial.
Futuras: "/macro", "/afp", "/banca" como vistas de detalle con gráficos Plotly.

ESQUELETO: la página renderiza y dispara la carga de tarjetas KPI vía HTMX
(hx-get="/api/kpi/overview/cards"), que hoy devuelve datos mock.
"""

from fastapi import APIRouter, Request

from api.deps import templates

router = APIRouter()


@router.get("/")
def overview(request: Request):
    """Panel multi-fuente (página principal del dashboard)."""
    return templates.TemplateResponse(request, "overview.html")
