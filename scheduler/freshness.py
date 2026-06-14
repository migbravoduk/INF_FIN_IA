"""
scheduler/freshness.py — Sondas de frescura para la ingesta "al publicarse" (catch-up).

Cada fuente expone su "último período que tengo" (vía la capa DB) y se calcula el
"siguiente esperado" según la frecuencia y un rezago de publicación. Si el dato falta y
ya estamos dentro de su ventana de publicación, queda marcado como `due` (pendiente).

El orquestador `run_catchup` (scheduler/jobs.py) consume `probe_all()` y descarga solo
lo que esté `due`, reusando los collectors y los inserts idempotentes existentes.

No descarga nada: este módulo es de SOLO LECTURA sobre la BD + cálculo de fechas.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml

from config.settings import settings
from collectors.cmf_banks import CMFBankCollector

logger = logging.getLogger(__name__)

# Rezago de publicación por defecto, en días, según frecuencia.
LAG_DEFAULTS = {"daily": 1, "monthly": 8, "quarterly": 85}

# Overrides conocidos para fuentes no-BCCh (días tras el cierre del período).
LAG_CMF_EMPRESAS = 60   # EEFF trimestrales XBRL (plazo regulatorio ~60 días)
LAG_CMF_BANCOS = 35     # Estados bancarios mensuales SBIF (~30-40 días)
LAG_SP_CARTERA = 40     # Cartera desagregada mensual SP

# Tope de días hábiles a recuperar de una vez para fuentes diarias (evita loops largos).
MAX_DAILY_BACKFILL = 10


@dataclass
class FreshnessStatus:
    """Estado de frescura de un objetivo de ingesta."""
    source: str                       # etiqueta legible de la fuente
    kind: str                         # 'bcentral'|'cmf_emp'|'cmf_bank'|'sp_cuotas'|'sp_precios'|'sp_cartera'
    frequency: str                    # 'daily'|'monthly'|'quarterly'
    latest_have: Optional[str]        # último período/fecha que hay en BD (str)
    expected: Optional[str]           # siguiente período/fecha esperado (str)
    due: bool                         # True si falta y ya está en ventana de publicación
    params: dict = field(default_factory=dict)  # datos para que run_catchup despache el fetch


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
# Sondas por fuente
# ============================================================

def _bcentral_lag(meta: dict) -> int:
    """Rezago de una serie BCCh: override `publish_lag_days` o default por frecuencia."""
    if meta.get("publish_lag_days") is not None:
        return int(meta["publish_lag_days"])
    return LAG_DEFAULTS.get(meta.get("frequency", "monthly"), 8)


def _probe_bcentral(db, today: dt.date) -> list[FreshnessStatus]:
    out: list[FreshnessStatus] = []
    if not settings.has_bcentral_credentials:
        return out  # sin credenciales no se puede descargar BCCh

    with open(settings.CATALOG_PATH, encoding="utf-8") as f:
        catalog = yaml.safe_load(f) or {}
    for meta in catalog.get("bcentral", []):
        sid = meta["id"]
        freq = meta.get("frequency", "monthly")
        lag = _bcentral_lag(meta)
        latest = db.get_latest_value(sid)
        latest_d = _to_date(latest["date"]) if latest else None

        if freq == "daily":
            expected_d = _last_business_day_before(today)
            due = latest_d is None or latest_d < expected_d
            expected_s = expected_d.isoformat()
        elif freq == "quarterly":
            ey, eq = _expected_quarter(today, lag)
            expected_period = ey * 100 + eq * 3
            have_period = (latest_d.year * 100 + ((latest_d.month - 1) // 3 + 1) * 3) if latest_d else 0
            due = have_period < expected_period
            expected_s = f"{ey}Q{eq}"
        else:  # monthly
            ey, em = _expected_month(today, lag)
            expected_period = ey * 100 + em
            have_period = (latest_d.year * 100 + latest_d.month) if latest_d else 0
            due = have_period < expected_period
            expected_s = f"{ey}-{em:02d}"

        out.append(FreshnessStatus(
            source=f"BCCh · {meta.get('name', sid)}",
            kind="bcentral", frequency=freq,
            latest_have=latest_d.isoformat() if latest_d else None,
            expected=expected_s, due=due,
            params={"series_id": sid, "from_date": latest_d.isoformat() if latest_d else None},
        ))
    return out


def _probe_cmf_empresas(db, today: dt.date) -> FreshnessStatus:
    ey, eq = _expected_quarter(today, LAG_CMF_EMPRESAS)
    expected_period = ey * 100 + eq * 3
    have = db.get_latest_cmf_period()
    due = have is None or have < expected_period
    return FreshnessStatus(
        source="CMF · EEFF empresas", kind="cmf_emp", frequency="quarterly",
        latest_have=str(have) if have else None, expected=str(expected_period), due=due,
        params={"period": expected_period},
    )


def _probe_cmf_bancos(db, today: dt.date) -> FreshnessStatus:
    ey, em = _expected_month(today, LAG_CMF_BANCOS)
    expected_period = ey * 100 + em
    have = db.get_latest_bank_period()
    due = have is None or have < expected_period
    return FreshnessStatus(
        source="CMF · EEFF bancos", kind="cmf_bank", frequency="monthly",
        latest_have=str(have) if have else None, expected=str(expected_period), due=due,
        params={"year": ey, "month": em, "expected_period": expected_period},
    )


def _probe_sp_cuotas(db, today: dt.date) -> FreshnessStatus:
    expected_d = _last_business_day_before(today)
    have = db.get_latest_sp_quota_date()
    have_d = _to_date(have)
    due = have_d is None or have_d < expected_d
    return FreshnessStatus(
        source="SP · valores cuota", kind="sp_cuotas", frequency="daily",
        latest_have=have_d.isoformat() if have_d else None,
        expected=expected_d.isoformat(), due=due,
        params={"year": today.year},
    )


def _probe_sp_precios(db, today: dt.date) -> FreshnessStatus:
    expected_d = _last_business_day_before(today)
    have = db.get_latest_sp_price_date()
    have_d = _to_date(have)
    due = have_d is None or have_d < expected_d
    return FreshnessStatus(
        source="SP · cinta de precios", kind="sp_precios", frequency="daily",
        latest_have=have_d.isoformat() if have_d else None,
        expected=expected_d.isoformat(), due=due,
        params={"from_date": have_d.isoformat() if have_d else None,
                "to_date": expected_d.isoformat()},
    )


def _probe_sp_cartera(db, today: dt.date) -> FreshnessStatus:
    ey, em = _expected_month(today, LAG_SP_CARTERA)
    expected_period = ey * 100 + em
    db_period = f"{ey}-{em:02d}"
    have = db.get_latest_sp_portfolio_period()  # 'YYYY-MM'
    have_cmp = int(have.replace("-", "")) if have else 0
    due = have_cmp < expected_period
    return FreshnessStatus(
        source="SP · cartera mensual", kind="sp_cartera", frequency="monthly",
        latest_have=have, expected=db_period, due=due,
        params={"period": expected_period, "db_period": db_period},
    )


def probe_all(db, today: Optional[dt.date] = None) -> list[FreshnessStatus]:
    """Construye el estado de frescura de todas las fuentes (solo lectura)."""
    today = today or dt.date.today()
    statuses: list[FreshnessStatus] = []
    statuses.extend(_probe_bcentral(db, today))
    statuses.append(_probe_cmf_empresas(db, today))
    statuses.append(_probe_cmf_bancos(db, today))
    statuses.append(_probe_sp_cuotas(db, today))
    statuses.append(_probe_sp_precios(db, today))
    statuses.append(_probe_sp_cartera(db, today))
    return statuses
