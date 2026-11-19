"""
api/routers/views.py — Agregador de las vistas HTML del dashboard (Jinja2 + HTMX + Plotly).

Las rutas viven por dominio en api/routers/web/ (Fase 5 del plan). Este módulo las reúne en
un solo `router` para que api/main.py y el export estático (api/preview.py) no cambien.
"""

from fastapi import APIRouter

from api.routers.web import panel, eeff, fecu, banca, afp, ffmm, proyecciones, institucional
from api.routers.web.panel import build_overview_context  # noqa: F401  (lo usa api/preview.py)

router = APIRouter()
for _m in (panel, eeff, fecu, banca, afp, ffmm, proyecciones, institucional):
    router.include_router(_m.router)
