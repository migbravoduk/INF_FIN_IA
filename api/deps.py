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

# Cache-busting de estáticos: el navegador cachea `app.css` agresivamente y los cambios de
# estilo no se ven hasta un refresco forzado. Se cuelga la mtime del archivo como query param
# (se recalcula al reiniciar el servidor, que es cuando cambian los estilos en la práctica).
_CSS_PATH = Path(__file__).parent / "static" / "app.css"
try:
    ASSET_VERSION = str(int(_CSS_PATH.stat().st_mtime))
except OSError:
    ASSET_VERSION = "0"
templates.env.globals["asset_version"] = ASSET_VERSION

# "Datos al …" con aviso de atraso, calculado con las mismas sondas del catch-up.
from api.freshness_banner import asof_banner  # noqa: E402
templates.env.globals["asof_banner"] = asof_banner


def _pct(x, digits: int = 1, signo: bool = True) -> str:
    """Fracción → porcentaje legible ('—' si falta). 0.0123 → '+1,2%'."""
    if x is None or x != x:
        return "—"
    return _es(x * 100, digits, signo) + "%"


def _num(x, digits: int = 0) -> str:
    return _es(x, digits)


def _es(x, digits: int = 1, signo: bool = False) -> str:
    """Número con separadores chilenos (miles '.', decimal ','). '—' si falta."""
    if x is None or x != x:
        return "—"
    s = f"{x:{'+' if signo else ''},.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _agf_corto(s) -> str:
    """'BICE INVERSIONES ADMINISTRADORA GENERAL DE FONDOS S.A.' → 'BICE Inversiones AGF'.
    En tablas el nombre legal completo ocupaba tres líneas por fila."""
    import re
    t = " ".join(str(s or "").split())
    t = re.sub(r"\s*ADMINISTRADORA GENERAL DE FONDOS\s*", " AGF ", t, flags=re.I)
    t = re.sub(r"\bS\.\s?A\.?(?=\s|$)|\s+SA$", "", t.strip(), flags=re.I)
    t = " ".join(t.split())
    siglas = {"AGF", "BCI", "BICE", "BTG", "SURA", "ITAU", "BBVA", "HSBC", "LV", "UBS"}
    return " ".join(w if w.upper() in siglas or len(w) <= 2 and w.isupper() else w.capitalize()
                    for w in t.split()).replace("Itau", "Itaú").replace("ITAU", "Itaú")


_MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _mes_es(x) -> str:
    """202609 / '2026-09' / '2026-09-30' -> 'sep 2026'. Si no reconoce el formato, lo devuelve tal cual."""
    t = str(x or "").replace("-", "")
    if len(t) < 6 or not t[:6].isdigit() or not 1 <= int(t[4:6]) <= 12:
        return str(x or "—")
    return f"{_MESES_ES[int(t[4:6]) - 1]} {t[:4]}"


def _corte(fecha, fuente: str = ""):
    """Etiqueta "dato a sep 2026 · Banco Central" para títulos de sección, gráficos y tarjetas:
    cada cifra dice a qué mes se refiere y de qué fuente sale (las fuentes tienen rezagos distintos)."""
    from markupsafe import Markup, escape
    if not fecha:
        return Markup("")
    txt = f"dato a {_mes_es(fecha)}" + (f" · {fuente}" if fuente else "")
    return Markup(f'<span class="corte" title="Mes al que se refiere el dato y su fuente">{escape(txt)}</span>')


templates.env.filters["mes_es"] = _mes_es
templates.env.globals["corte"] = _corte
templates.env.filters["agf_corto"] = _agf_corto
templates.env.filters["pct"] = _pct
templates.env.filters["num"] = _num
templates.env.filters["es"] = _es


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
