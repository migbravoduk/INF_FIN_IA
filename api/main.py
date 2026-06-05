"""
api/main.py — Punto de entrada de la app FastAPI.

Arranque (con el .venv del proyecto):
    ./.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000

Monta:
  - Routers REST bajo /api/*  (JSON)
  - Router de vistas HTML (Jinja2 + HTMX) en /
  - Archivos estáticos en /static

ESQUELETO: los endpoints son stubs. Ver cada router para el contrato de datos.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routers import macro, cmf, banks, sp, dashboard_kpi, views

app = FastAPI(
    title="INF_FIN_IA — API Financiera Chile",
    description="API REST + dashboard sobre la BD DuckDB de datos financieros chilenos.",
    version="0.1.0",
)

# Routers REST (JSON)
app.include_router(macro.router, prefix="/api", tags=["macro"])
app.include_router(cmf.router, prefix="/api/cmf", tags=["cmf"])
app.include_router(banks.router, prefix="/api/banks", tags=["banks"])
app.include_router(sp.router, prefix="/api/sp", tags=["sp"])
app.include_router(dashboard_kpi.router, prefix="/api/kpi", tags=["kpi"])

# Vistas HTML (dashboard)
app.include_router(views.router, tags=["views"])

# Estáticos
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", tags=["meta"])
def health():
    """Liveness check simple."""
    return {"status": "ok"}
