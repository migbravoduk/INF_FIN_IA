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

    def palabra(w):
        # Marcas con mayúscula interior (LarrainVial, BancoEstado…): la tabla de _nombre.
        if w.upper() in _ESPECIALES and w.upper().isalpha():
            return _ESPECIALES[w.upper()]
        return w if w.upper() in siglas or len(w) <= 2 and w.isupper() else w.capitalize()
    return " ".join(palabra(w) for w in t.split()).replace("Itau", "Itaú").replace("ITAU", "Itaú")


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


_SIGLAS = {"AFP", "AGF", "BCI", "BICE", "BTG", "CMF", "SP", "CLP", "USD", "UF", "ETF", "ESG", "EEUU",
           "EE.UU.", "FM", "FI", "FIP", "APV", "LP", "CP", "USA", "II", "III", "IV", "VI", "BCH", "CCS",
           "HSBC", "BBVA", "BNP", "JP", "IFRS", "ECC", "NDF", "OIS", "CGE", "ENAP", "LATAM", "SQM", "CMPC",
           "CAP", "SMU", "CCU", "AES", "IPC", "TPM", "PIB", "ADR", "REIT", "RV", "RF", "MM", "MMM", "MBI", "CMB"}
_ESPECIALES = {"PLANVITAL": "PlanVital", "ITAU": "Itaú", "ITAÚ": "Itaú", "LARRAINVIAL": "LarrainVial",
               "BANCOESTADO": "BancoEstado", "METLIFE": "MetLife", "4LIFE": "4Life", "SCOTIABANK": "Scotiabank",
               "S.A.": "S.A.", "S.A": "S.A.", "SA": "S.A.", "SPA": "SpA", "LTDA": "Ltda.", "LTDA.": "Ltda.",
               "CIA": "Cía.", "CIA.": "Cía.", "CÍA": "Cía.", "CÍA.": "Cía."}
_MENORES = {"de", "del", "la", "las", "los", "el", "y", "e", "en", "para", "por", "con", "a", "al", "o", "u"}


def _nombre(s) -> str:
    """Nombre propio legible: 'BANCO DE CRÉDITO E INVERSIONES' → 'Banco de Crédito e Inversiones'.
    Solo actúa si el texto viene entero en mayúsculas; respeta siglas, números y casos especiales."""
    t = " ".join(str(s or "").split())
    if not t or t != t.upper() or not any(c.isalpha() for c in t):
        return t
    out = []
    for i, w in enumerate(t.split(" ")):
        uw, lw = w.upper(), w.lower()
        if uw in _ESPECIALES:
            out.append(_ESPECIALES[uw])
        elif uw.strip(".,()") in _SIGLAS or any(c.isdigit() for c in w):
            out.append(w)
        elif i and lw in _MENORES:
            out.append(lw)
        else:
            out.append("-".join(p if p.upper() in _SIGLAS else p[:1].upper() + p[1:].lower() for p in w.split("-")))
    return " ".join(out)


_MESES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_es(x) -> str:
    """'2026-11-15' → '15 nov 2026'. Si no es una fecha, la devuelve tal cual."""
    t = str(x or "")[:10]
    if len(t) == 10 and t[4] == "-" and t[7] == "-" and t[:4].isdigit():
        return f"{int(t[8:10])} {_MESES_ES[int(t[5:7]) - 1]} {t[:4]}"
    return str(x or "—")


templates.env.filters["nombre"] = _nombre
templates.env.filters["fecha_es"] = _fecha_es


# Menú principal (docs/PLAN_ESTETICA.md F2). "hijos": (texto, url, descripción, prefijos extra).
NAV = [
    {"t": "Panel", "url": "/"},
    {"t": "Mercados", "hijos": [
        {"t": "Curvas de tasas", "url": "/curvas", "d": "Nominal y real: swaps y bonos del Estado"},
        {"t": "Banco Central", "url": "/banco-central", "d": "Series macro y expectativas"},
        {"t": "Derivados", "url": "/derivados", "d": "Exposición y riesgo de toda la economía"},
    ]},
    {"t": "Empresas", "hijos": [
        {"t": "Estados financieros", "url": "/eeff", "d": "Corporativos, seguros, intermediarios y AGF"},
        {"t": "Bancos", "url": "/banca", "d": "Balance y resultados por moneda"},
        {"t": "Comparar empresas", "url": "/comparar", "d": "Hasta tres empresas lado a lado"},
        {"t": "Ranking", "url": "/ranking", "d": "Las 15 mejores por indicador", "extra": ["/evolucion"]},
        {"t": "Salud financiera", "url": "/salud", "d": "Rentabilidad contra endeudamiento"},
        {"t": "Proyecciones", "url": "/proyecciones", "d": "Ingresos y resultados proyectados"},
    ]},
    {"t": "Inversionistas", "hijos": [
        {"t": "Inversión institucional", "url": "/inversion-institucional", "d": "AFP, seguros y fondos en un vistazo"},
        {"t": "AFP", "url": "/afp", "d": "Rentabilidad, comisiones y cartera"},
        {"t": "Fondos de pensiones", "url": "/fondos", "d": "Retornos y composición por fondo"},
        {"t": "Cartera de seguros", "url": "/seguros/cartera", "d": "Asset allocation de las aseguradoras"},
        {"t": "Derivados institucionales", "url": "/inversion-institucional/derivados", "d": "Monitor de swaps de las AFP"},
        {"t": "Inversión por emisor", "url": "/inversion-institucional/emisor", "d": "Quién invierte en cada emisor"},
        {"t": "Emisores en común", "url": "/inversion-institucional/emisores", "d": "Aseguradoras y fondos mutuos"},
    ]},
    {"t": "Fondos y productos", "hijos": [
        {"t": "Productos de inversión", "url": "/productos", "d": "Rentabilidad, riesgo y costo comparables", "extra": ["/producto"]},
        {"t": "Fondos mutuos", "url": "/fondos-mutuos", "d": "Rendimiento y cartera por fondo"},
    ]},
    {"t": "Metodología", "url": "/metodologia"},
]


def _nav_urls():
    for g in NAV:
        if "url" in g:
            yield g["url"], [g["url"]]
        for h in g.get("hijos", []):
            yield h["url"], [h["url"], *h.get("extra", [])]


def _nav_activo(path: str, url: str) -> bool:
    """Activo = el ítem de prefijo más largo que calza con la ruta (así
    /inversion-institucional/derivados no marca también /inversion-institucional)."""
    def calza(p):
        return path == p or (p != "/" and path.startswith(p.rstrip("/") + "/"))
    mejor, largo = None, -1
    for u, prefijos in _nav_urls():
        for p in prefijos:
            if calza(p) and len(p) > largo:
                mejor, largo = u, len(p)
    return mejor == url


def _nav_grupo_activo(path: str, grupo: dict) -> bool:
    return any(_nav_activo(path, h["url"]) for h in grupo.get("hijos", []))


templates.env.globals.update(NAV=NAV, nav_activo=_nav_activo, nav_grupo_activo=_nav_grupo_activo)




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
