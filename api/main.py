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

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routers import macro, cmf, banks, sp, dashboard_kpi, views
from config.settings import settings

logger = logging.getLogger(__name__)
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranca el scheduler embebido si RUN_SCHEDULER_IN_APP=true (proceso único)."""
    global _scheduler
    if settings.RUN_SCHEDULER_IN_APP:
        from scheduler.jobs import create_scheduler
        _scheduler = create_scheduler(blocking=False)
        _scheduler.start()
        logger.info("Scheduler embebido iniciado dentro de la app FastAPI.")
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler embebido detenido.")


app = FastAPI(
    title="INF_FIN_IA — API Financiera Chile",
    description="API REST + dashboard sobre la BD DuckDB de datos financieros chilenos.",
    version="0.1.0",
    lifespan=lifespan,
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
