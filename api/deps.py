"""
api/deps.py — Dependencias compartidas de FastAPI.

get_db() entrega una conexión DuckDB en modo SOLO LECTURA por request.

⚠️ MODELO DE CONCURRENCIA DUCKDB (importante):
DuckDB NO permite que un proceso lea mientras OTRO proceso tiene el archivo abierto en
lectura/escritura. Solo admite, a la vez, *o bien* un único proceso escritor, *o bien*
varios procesos lectores. Por tanto la idea de "API read_only conviviendo con un
scheduler escritor en otro proceso" NO funciona: el read_only fallará con IOException
mientras el scheduler tenga la BD abierta.

Opciones reales (a decidir al implementar):
  A) RECOMENDADA — Proceso único: montar el scheduler como BackgroundScheduler DENTRO
     de la app FastAPI (create_scheduler(blocking=False) ya existe) y compartir UNA sola
     conexión. Cero conflictos de archivo.
  B) Lectores múltiples sin escritor: correr la API read_only solo cuando el scheduler NO
     esté corriendo (p.ej. ingestas manuales/cron que abren-escriben-cierran rápido).
  C) Snapshot: el scheduler exporta una copia/parquet que la API lee aparte.

Hoy get_db() usa read_only=True (opción B/validación); devuelve 503 si la BD está
bloqueada por otro proceso.
"""

import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from db.database import Database
from config.settings import settings

# Plantillas Jinja2 (compartidas por los routers de vistas)
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def records(df) -> list[dict]:
    """
    Convierte un DataFrame a lista de dicts JSON-safe (fechas en ISO, NaN→null).
    Pasa por to_json para evitar problemas de serialización de Timestamps/NaN.
    """
    return json.loads(df.to_json(orient="records", date_format="iso"))


def get_db():
    """
    Dependencia FastAPI: abre la BD en read_only y la cierra al terminar el request.

    Uso en un endpoint:
        @router.get(...)
        def handler(db: Database = Depends(get_db)):
            ...
    """
    # En modo embebido (scheduler dentro de la app) se abre R/W para compartir el mismo
    # proceso con el escritor; si no, lectura para coexistir/diagnosticar sin riesgo.
    try:
        db = Database(read_only=not settings.RUN_SCHEDULER_IN_APP)
    except Exception as e:
        # Típicamente: la BD aún no existe (no se ha corrido ningún fetch).
        raise HTTPException(
            status_code=503,
            detail=f"Base de datos no disponible en modo lectura: {e}",
        )
    try:
        yield db
    finally:
        db.close()
