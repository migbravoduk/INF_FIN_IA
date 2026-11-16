"""
api/freshness_banner.py — "Datos al …" en cada vista, con aviso si la fuente está atrasada.

Una vista de inversiones que muestra un dato viejo sin decirlo engaña (pasó con la cartera
de AFP: la web mostraba enero cuando ya debía estar septiembre). Este módulo reusa las
MISMAS sondas del catch-up (`scheduler/sources.py`), así que la web y el actualizador
nunca discrepan sobre qué está al día. Se cachea unos minutos: las sondas son baratas pero
corren en cada página.

`base.html` llama a `asof_banner(request.url.path)`; la ruta decide qué fuentes mira.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from markupsafe import Markup, escape

logger = logging.getLogger(__name__)

# Prefijo de ruta → fuentes que alimentan esa vista (el primero que calza, en orden).
ROUTE_SOURCES: list[tuple[str, list[str]]] = [
    ("/banco-central", ["bcentral"]),
    ("/eeff/seguros", ["cmf_insurer"]),
    ("/eeff/intermediarios", ["cmf_broker"]),
    ("/proyecciones/bancos", ["cmf_bank"]),
    ("/proyecciones/seguros", ["cmf_insurer"]),
    ("/proyecciones/intermediarios", ["cmf_broker"]),
    ("/proyecciones", ["cmf_emp"]),
    ("/eeff", ["cmf_emp"]),
    ("/evolucion", ["cmf_emp"]),
    ("/comparar", ["cmf_emp"]),
    ("/ranking", ["cmf_emp"]),
    ("/salud", ["cmf_emp"]),
    ("/banca", ["cmf_bank"]),
    ("/fondos-mutuos", ["cmf_fm_cuotas", "cmf_fm_cartera", "cmf_fm_comisiones", "cmf_fm_comparador"]),
    ("/seguros/cartera", ["cmf_cartera"]),
    ("/afp", ["sp_cuotas", "sp_cartera"]),
    ("/fondos", ["sp_cuotas", "sp_cartera"]),
    ("/inversion-institucional", ["cmf_cartera", "sp_cuotas", "sp_cartera", "cmf_fm_cuotas"]),
]

_TTL = 300
_cache: dict = {"at": 0.0, "by_kind": None}


def _statuses_by_kind() -> dict:
    now = time.monotonic()
    if _cache["by_kind"] is not None and now - _cache["at"] < _TTL:
        return _cache["by_kind"]
    from config.settings import settings
    from db.database import Database
    from scheduler.freshness import probe_all
    by_kind: dict = {}
    try:
        with Database(read_only=not settings.RUN_SCHEDULER_IN_APP) as db:
            for st in probe_all(db):
                by_kind.setdefault(st.kind, []).append(st)
    except Exception as e:
        logger.info(f"Aviso de frescura no disponible: {e}")
    _cache.update(at=now, by_kind=by_kind)
    return by_kind


def _fmt(s: str | None) -> str:
    if not s:
        return "—"
    s = str(s)
    if len(s) == 6 and s.isdigit():          # AAAAMM
        return f"{s[4:]}/{s[:4]}"
    if len(s) == 8 and s.isdigit():          # AAAAMMDD
        return f"{s[6:]}/{s[4:6]}/{s[:4]}"
    if len(s) == 10 and s[4] == "-":         # AAAA-MM-DD
        return f"{s[8:]}/{s[5:7]}/{s[:4]}"
    if len(s) == 7 and s[4] == "-":          # AAAA-MM
        return f"{s[5:]}/{s[:4]}"
    return s


def _item(kind: str, sts: list) -> tuple[str, bool]:
    """Texto de una fuente y si está atrasada."""
    from scheduler.sources import BY_KIND
    titulo = BY_KIND[kind].titulo if kind in BY_KIND else kind
    if kind == "bcentral":
        due = [s for s in sts if s.due]
        txt = f"{titulo}: {len(sts) - len(due)} de {len(sts)} series al día"
        return txt, bool(due)
    # Una fuente puede tener varias sondas (ramos, reingesta de un período incompleto).
    principales = [s for s in sts if not s.note.startswith("incompleto")] or sts
    have = min((s.latest_have for s in principales if s.latest_have), default=None)
    due = [s for s in sts if s.due]
    txt = f"{titulo}: datos al {_fmt(have)}"
    if due:
        exp = max((s.expected for s in due if s.expected), default=None)
        inc = [s for s in due if s.note.startswith("incompleto")]
        txt += f" — ⚠ período {_fmt(inc[0].expected)} incompleto" if inc else \
               f" — ⚠ desactualizado, ya debería estar {_fmt(exp)}"
    return txt, bool(due)


def asof_banner(path: str) -> Markup:
    kinds = next((k for prefix, k in ROUTE_SOURCES if path == prefix or path.startswith(prefix + "/")), None)
    if not kinds:
        return Markup("")
    by_kind = _statuses_by_kind()
    if not by_kind:
        return Markup("")
    items = [_item(k, by_kind[k]) for k in kinds if k in by_kind]
    if not items:
        return Markup("")
    stale = any(s for _, s in items)
    li = "".join(f'<span class="asof-item{" asof-stale" if s else ""}">{escape(t)}</span>'
                 for t, s in items)
    return Markup(f'<div class="asof{" asof-warn" if stale else ""}" role="status">{li}</div>')


def reset_cache() -> None:
    _cache.update(at=0.0, by_kind=None)
