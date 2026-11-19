"""
scheduler/freshness.py — Sondas de frescura para la ingesta "al publicarse" (catch-up).

Cada fuente expone su "último período que tengo" (vía la capa DB) y se calcula el
"siguiente esperado" según la frecuencia y un rezago de publicación. Si el dato falta y
ya estamos dentro de su ventana de publicación, queda marcado como `due` (pendiente).

Las sondas de cada fuente están en `scheduler/sources.py` (registro único); este módulo
aporta el tipo `FreshnessStatus`, las utilidades de fechas y `probe_all()`, que recorre
el registro. El orquestador `run_catchup` (scheduler/jobs.py) descarga solo lo `due`.

No descarga nada: este módulo es de SOLO LECTURA sobre la BD + cálculo de fechas.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml


logger = logging.getLogger(__name__)

# Rezago de publicación por defecto, en días, según frecuencia.
LAG_DEFAULTS = {"daily": 1, "monthly": 8, "quarterly": 85}

# Los rezagos propios de cada fuente viven junto a su sonda, en scheduler/sources.py.

# Tope de días hábiles a recuperar de una vez para fuentes diarias (evita loops largos).
MAX_DAILY_BACKFILL = 30  # días hábiles por corrida (antes 10: un atraso de 3 meses tomaba 6 corridas)


@dataclass
class FreshnessStatus:
    """Estado de frescura de un objetivo de ingesta."""
    source: str                       # etiqueta legible de la fuente
    kind: str                         # clave de la fuente en scheduler/sources.SOURCES
    frequency: str                    # 'daily'|'monthly'|'quarterly'
    latest_have: Optional[str]        # último período/fecha que hay en BD (str)
    expected: Optional[str]           # siguiente período/fecha esperado (str)
    due: bool                         # True si falta y ya está en ventana de publicación
    params: dict = field(default_factory=dict)  # datos para que run_catchup despache el fetch
    note: str = ""                    # p. ej. "incompleto: 200 entidades (53% de lo habitual)"


# ============================================================
# Utilidades de fechas
# ============================================================

def _last_business_day_before(ref: dt.date) -> dt.date:
    """Último día hábil estrictamente anterior a `ref`."""
    d = ref - dt.timedelta(days=1)
    while d.weekday() >= 5:  # 5=sáb, 6=dom
        d -= dt.timedelta(days=1)
    return d


def _month_end(year: int, month: int) -> dt.date:
    if month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _expected_month(today: dt.date, lag_days: int) -> tuple[int, int]:
    """Mes más reciente cuyo dato ya debería estar publicado (fin de mes + rezago <= hoy)."""
    y, m = today.year, today.month
    for _ in range(18):
        if today >= _month_end(y, m) + dt.timedelta(days=lag_days):
            return y, m
        y, m = _add_months(y, m, -1)
    return y, m


def _quarter_end(year: int, q: int) -> dt.date:
    return _month_end(year, q * 3)


def _expected_quarter(today: dt.date, lag_days: int) -> tuple[int, int]:
    """Trimestre más reciente cuyo dato ya debería estar publicado."""
    q = (today.month - 1) // 3 + 1
    y = today.year
    for _ in range(8):
        if today >= _quarter_end(y, q) + dt.timedelta(days=lag_days):
            return y, q
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return y, q


def _to_date(value) -> Optional[dt.date]:
    """Normaliza date/datetime/str(YYYY-MM-DD) a date."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


# ============================================================
# Sondeo de todas las fuentes
# ============================================================

def probe_all(db, today: Optional[dt.date] = None, kinds: Optional[list[str]] = None) -> list[FreshnessStatus]:
    """Estado de frescura de todas las fuentes registradas (solo lectura, sin red).

    Una sonda que falla no tumba el resto: se registra como pendiente con la nota del error,
    para que el problema se vea en el menú en vez de esconderse.
    """
    from scheduler.sources import SOURCES  # import diferido: sources importa este módulo
    today = today or dt.date.today()
    statuses: list[FreshnessStatus] = []
    for src in SOURCES:
        if kinds and src.kind not in kinds:
            continue
        try:
            statuses.extend(src.probe(db, today))
        except Exception as e:
            logger.error(f"Sonda {src.kind} falló: {e}")
            statuses.append(FreshnessStatus(
                source=src.titulo, kind=src.kind, frequency="?", latest_have=None,
                expected=None, due=False, note=f"error de sonda: {e}"))
    return statuses
