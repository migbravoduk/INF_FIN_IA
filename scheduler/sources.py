"""
scheduler/sources.py — Registro ÚNICO de fuentes de ingesta.

Antes, agregar una fuente exigía tocar tres lugares (sonda en freshness.py, dispatch en
jobs.py y menú en actualizar.py), y un nombre que no calzaba entre ellos dejó el catch-up
entero caído con un NameError (nov-2026). Ahora cada fuente es UNA entrada de `SOURCES`:

    Source(kind, titulo, source_id, probe, fetch, refresh)

- `probe(db, today) -> list[FreshnessStatus]`: solo lectura, sin red.
- `fetch(db, status) -> (estado, n)`: descarga e ingesta lo que la sonda marcó `due`.
- `refresh(db, n)`: (opcional) reingesta los últimos n períodos, porque la CMF corrige
  períodos ya publicados (ver CONTEXTO, "la CMF RESTATA").

`probe_all` (freshness), `_dispatch_catchup` (jobs) y el menú de `actualizar.py` se
generan recorriendo esta lista; un test verifica que no quede ninguna fuente sin cablear.

COMPLETITUD: "el período existe" no significa "el período está completo". La CMF publica
por goteo y una ingesta temprana deja un período a medias que la sonda antigua daba por
cargado (EEFF 202606 quedó con 200 de ~380 empresas). Por eso las fuentes por entidad
comparan el número de entidades del último período con la mediana de los comparables
anteriores, y si está bajo `COMPLETENESS_MIN` lo vuelven a pedir durante `COMPLETENESS_GRACE_DAYS`.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import yaml

from config.settings import settings
from scheduler.freshness import (
    FreshnessStatus, LAG_DEFAULTS, MAX_DAILY_BACKFILL,
    _add_months, _expected_month, _expected_quarter, _last_business_day_before,
    _month_end, _to_date,
)

logger = logging.getLogger(__name__)

# Rezagos de publicación (días tras el cierre del período).
LAG_CMF_EMPRESAS = 60
LAG_CMF_BANCOS = 35
LAG_CMF_SEGUROS = 60
LAG_CMF_INTERMEDIARIOS = 60
LAG_CMF_CARTERA_SEGUROS = 40   # 202607 no estaba el 10-ago y sí el 9-sep
# La cartera desagregada de la SP sale con ~4 meses de rezago: el 2026-11-16 estaba 202606 y
# 202607 respondía un volcado de error en vez del ZIP. Con 40 días la sonda pedía un mes
# inexistente y, como antes solo pedía ESE mes, nunca rellenaba los intermedios.
LAG_SP_CARTERA = 130
LAG_FFMM_CUOTAS = 3            # la estadística es diaria: el corte de fin de mes sale al día siguiente
LAG_FFMM_CARTERA = 40
LAG_FFMM_COMISIONES = 20
LAG_FFMM_COMPARADOR = 75       # trimestral; el corte de septiembre aún no estaba el 16-nov (>45 días)

COMPLETENESS_MIN = 0.90        # bajo 90% de la mediana de comparables = período incompleto
COMPLETENESS_GRACE_DAYS = 120  # se reintenta hasta 120 días después de la fecha esperada
MAX_MONTHLY_BACKFILL = 6       # meses faltantes a recuperar por corrida en fuentes mensuales


@dataclass
class Source:
    kind: str
    titulo: str
    source_id: str                                   # columna source_id de fetch_log
    probe: Callable
    fetch: Callable
    refresh: Optional[Callable] = None


# ============================================================
# Utilidades
# ============================================================

def _period_end(period: int) -> dt.date:
    return _month_end(period // 100, period % 100)


def _months_between(have: Optional[int], expected: int, cap: int) -> list[int]:
    """Períodos AAAAMM en (have, expected], topado en `cap` (los más antiguos primero)."""
    if have is None:
        return [expected]
    out, y, m = [], have // 100, have % 100
    while len(out) < cap:
        y, m = _add_months(y, m, 1)
        p = y * 100 + m
        if p > expected:
            break
        out.append(p)
    return out


def completeness(rows: list[tuple[int, int]], same_month: bool) -> Optional[tuple[int, int, float]]:
    """
    Evalúa el último período de `rows` = [(period, n_entidades)] ordenado ascendente.

    Comparables: los 4 períodos anteriores del mismo mes del año (`same_month`, para
    fuentes trimestrales donde diciembre trae más entidades que el resto) o los 4
    anteriores a secas (mensuales). Devuelve (period, n, ratio) o None si no hay base.
    """
    if not rows:
        return None
    last_p, last_n = rows[-1]
    prev = [n for p, n in rows[:-1] if (not same_month or p % 100 == last_p % 100)][-4:]
    if not prev:
        return None
    med = statistics.median(prev)
    return last_p, last_n, (last_n / med if med else 1.0)


def _incomplete_status(rows, same_month, lag, today, mk_status) -> Optional[FreshnessStatus]:
    """Si el último período está incompleto y aún en gracia, devuelve un status de reingesta."""
    c = completeness(rows, same_month)
    if not c:
        return None
    period, n, ratio = c
    if ratio >= COMPLETENESS_MIN:
        return None
    deadline = _period_end(period) + dt.timedelta(days=lag + COMPLETENESS_GRACE_DAYS)
    if today > deadline:
        logger.warning(f"Período {period} sigue incompleto ({ratio:.0%}) pasada la gracia; se acepta.")
        return None
    st = mk_status(period)
    st.note = f"incompleto: {n} entidades ({ratio:.0%} de lo habitual)"
    st.params["force"] = True
    return st


def _log(db, series_id, source_id, n, started, ok=None):
    ok = bool(n) if ok is None else ok
    db.log_fetch(series_id, source_id, "ok" if ok else "no_data", n, started_at=started)
    return ("ok" if ok else "no_data"), n


# ============================================================
# BCCh
# ============================================================

def _bcentral_lag(meta: dict) -> int:
    if meta.get("publish_lag_days") is not None:
        return int(meta["publish_lag_days"])
    return LAG_DEFAULTS.get(meta.get("frequency", "monthly"), 8)


def load_catalog() -> dict:
    with open(settings.CATALOG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def probe_bcentral(db, today):
    out = []
    if not settings.has_bcentral_credentials:
        return out
    for meta in load_catalog().get("bcentral", []):
        if meta.get("discontinued"):
            continue  # serie descontinuada por el BCCh: no se vuelve a pedir
        sid, freq, lag = meta["id"], meta.get("frequency", "monthly"), _bcentral_lag(meta)
        latest = db.get_latest_value(sid)
        latest_d = _to_date(latest["date"]) if latest else None
        if freq == "daily":
            expected_d = _last_business_day_before(today)
            due, expected_s = (latest_d is None or latest_d < expected_d), expected_d.isoformat()
        elif freq == "quarterly":
            ey, eq = _expected_quarter(today, lag)
            have_p = (latest_d.year * 100 + ((latest_d.month - 1) // 3 + 1) * 3) if latest_d else 0
            due, expected_s = have_p < ey * 100 + eq * 3, f"{ey}Q{eq}"
        elif freq == "annual":
            ey = today.year - 1 if today >= dt.date(today.year, 1, 1) + dt.timedelta(days=lag) else today.year - 2
            due, expected_s = (latest_d is None or latest_d.year < ey), str(ey)
        else:
            ey, em = _expected_month(today, lag)
            have_p = (latest_d.year * 100 + latest_d.month) if latest_d else 0
            due, expected_s = have_p < ey * 100 + em, f"{ey}-{em:02d}"
        out.append(FreshnessStatus(
            source=f"BCCh · {meta.get('name', sid)}", kind="bcentral", frequency=freq,
            latest_have=latest_d.isoformat() if latest_d else None, expected=expected_s, due=due,
            params={"series_id": sid, "from_date": latest_d.isoformat() if latest_d else None,
                    "meta": meta}))
    return out


def fetch_bcentral(db, st):
    from collectors.bcentral import BCentralCollector
    from processors.normalizer import normalize_observations
    started = datetime.now()
    sid = st.params["series_id"]
    # La tabla `series` es copia del catálogo: refrescarla en cada pasada (ver CONTEXTO).
    if st.params.get("meta"):
        db.upsert_series({**st.params["meta"], "source_id": "bcentral"})
    raw = BCentralCollector().fetch_series(sid, from_date=st.params.get("from_date"))
    clean = normalize_observations(raw, sid)
    new, updated = db.upsert_observations(sid, clean)
    db.log_fetch(sid, "bcentral", "ok", new, updated, started_at=started)
    return "ok", new


# ============================================================
# CMF · EEFF corporativos
# ============================================================

def _rows(db, sql, params=None):
    return [(int(p), int(n)) for p, n in db.conn.execute(sql, params or []).fetchall()]


def probe_cmf_emp(db, today):
    ey, eq = _expected_quarter(today, LAG_CMF_EMPRESAS)
    expected = ey * 100 + eq * 3
    have = db.get_latest_cmf_period()
    mk = lambda p: FreshnessStatus(
        source="CMF · EEFF empresas", kind="cmf_emp", frequency="quarterly",
        latest_have=str(have) if have else None, expected=str(p), due=True, params={"period": p})
    out = []
    rows = _rows(db, "SELECT period, COUNT(DISTINCT rut) FROM cmf_financial_statements GROUP BY 1 ORDER BY 1")
    inc = _incomplete_status(rows, True, LAG_CMF_EMPRESAS, today, mk)
    if inc:
        out.append(inc)
    st = mk(expected)
    st.due = have is None or have < expected
    out.append(st)
    return out


def fetch_cmf_emp(db, st):
    from collectors.cmf import CMFCollector
    started = datetime.now()
    p = st.params["period"]
    recs = CMFCollector().fetch_period(p, force_download=st.params.get("force", False))
    n = db.insert_cmf_records(p, recs) if recs else 0
    return _log(db, f"cmf_emp:{p}", "cmf", n, started)


def refresh_cmf_emp(db, n=2):
    for p in db.get_cmf_periods()[:n]:
        fetch_cmf_emp(db, FreshnessStatus("", "cmf_emp", "", None, str(p), True, {"period": p, "force": True}))


# ============================================================
# CMF · bancos
# ============================================================

def probe_cmf_bank(db, today):
    ey, em = _expected_month(today, LAG_CMF_BANCOS)
    expected = ey * 100 + em
    have = db.get_latest_bank_period()
    mk = lambda p: FreshnessStatus(
        source="CMF · EEFF bancos", kind="cmf_bank", frequency="monthly",
        latest_have=str(have) if have else None, expected=str(p), due=True,
        params={"year": p // 100, "month": p % 100, "expected_period": p})
    out = []
    rows = _rows(db, "SELECT period, COUNT(DISTINCT bank_code) FROM cmf_bank_statements GROUP BY 1 ORDER BY 1")
    inc = _incomplete_status(rows, False, LAG_CMF_BANCOS, today, mk)
    if inc:
        out.append(inc)
    st = mk(expected)
    st.due = have is None or have < expected
    out.append(st)
    return out


def fetch_cmf_bank(db, st):
    from collectors.cmf_banks import CMFBankCollector
    started = datetime.now()
    y, m, expected = st.params["year"], st.params["month"], st.params["expected_period"]
    collector = CMFBankCollector()
    have_codes = {r[0] for r in db.conn.execute(
        "SELECT DISTINCT bank_code FROM cmf_bank_statements WHERE period = ?", [expected]).fetchall()}
    total = 0
    for code in collector.BANKS_CATALOG:
        if str(code).zfill(3) in have_codes:
            continue  # este banco ya tiene el período
        for rtype in ("balance", "resultado"):
            recs = collector.fetch_bank_report(y, m, code, report_type=rtype)
            if recs:
                total += db.insert_bank_records(y, m, code, rtype, recs)
    return _log(db, f"cmf_bank:{expected}", "cmf", total, started)


# ============================================================
# CMF · seguros (FECU) y intermediarios
# ============================================================

def probe_cmf_insurer(db, today):
    out = []
    ey, eq = _expected_quarter(today, LAG_CMF_SEGUROS)
    expected = ey * 100 + eq * 3
    for itype in ("vida", "generales"):
        have = db.get_latest_insurer_period(freq="trimestral", insurance_type=itype)
        mk = lambda p, itype=itype, have=have: FreshnessStatus(
            source=f"CMF · EEFF seguros {itype}", kind="cmf_insurer", frequency="quarterly",
            latest_have=str(have) if have else None, expected=str(p), due=True,
            params={"year": p // 100, "month": p % 100, "freq": "trimestral", "insurance_type": itype})
        rows = _rows(db, """SELECT period, COUNT(DISTINCT rut) FROM cmf_insurer_statements
                            WHERE insurance_type = ? AND report_freq = 'trimestral' GROUP BY 1 ORDER BY 1""", [itype])
        inc = _incomplete_status(rows, True, LAG_CMF_SEGUROS, today, mk)
        if inc:
            out.append(inc)
        st = mk(expected)
        st.due = have is None or have < expected
        out.append(st)
    return out


def fetch_cmf_insurer(db, st):
    from collectors.cmf_insurers import CMFInsurerCollector
    started = datetime.now()
    y, m = st.params["year"], st.params["month"]
    freq, itype = st.params.get("freq", "trimestral"), st.params.get("insurance_type", "vida")
    p = y * 100 + m
    recs = CMFInsurerCollector(insurance_type=itype).fetch_period(
        y, m, freq=freq, force_download=st.params.get("force", False))
    n = db.insert_insurer_records(p, freq, recs, insurance_type=itype) if recs else 0
    return _log(db, f"cmf_insurer_{itype}:{p}", "cmf", n, started)


def refresh_cmf_insurer(db, n=2):
    for itype in ("vida", "generales"):
        for p in db.get_insurer_periods(freq="trimestral", insurance_type=itype)[:n]:
            fetch_cmf_insurer(db, FreshnessStatus("", "cmf_insurer", "", None, str(p), True, {
                "year": p // 100, "month": p % 100, "insurance_type": itype, "force": True}))


def probe_cmf_broker(db, today):
    ey, eq = _expected_quarter(today, LAG_CMF_INTERMEDIARIOS)
    expected = ey * 100 + eq * 3
    have = db.get_latest_broker_period()
    mk = lambda p: FreshnessStatus(
        source="CMF · EEFF intermediarios de valores", kind="cmf_broker", frequency="quarterly",
        latest_have=str(have) if have else None, expected=str(p), due=True,
        params={"year": p // 100, "month": p % 100})
    out = []
    rows = _rows(db, "SELECT period, COUNT(DISTINCT rut) FROM cmf_broker_statements GROUP BY 1 ORDER BY 1")
    inc = _incomplete_status(rows, True, LAG_CMF_INTERMEDIARIOS, today, mk)
    if inc:
        out.append(inc)
    st = mk(expected)
    st.due = have is None or have < expected
    out.append(st)
    return out


def fetch_cmf_broker(db, st):
    from collectors.cmf_brokers import CMFBrokerCollector
    started = datetime.now()
    y, m = st.params["year"], st.params["month"]
    p = y * 100 + m
    recs = CMFBrokerCollector().fetch_period(y, m, force_download=st.params.get("force", False))
    n = db.insert_broker_records(p, recs) if recs else 0
    return _log(db, f"cmf_broker:{p}", "cmf", n, started)


def refresh_cmf_broker(db, n=2):
    for p in db.get_broker_periods()[:n]:
        fetch_cmf_broker(db, FreshnessStatus("", "cmf_broker", "", None, str(p), True, {
            "year": p // 100, "month": p % 100, "force": True}))


# ============================================================
# CMF · cartera de inversiones de seguros (Circular 1.835)
# ============================================================

def ingest_cartera_zip(db, zip_path) -> tuple[int, int]:
    """Parsea e ingesta un ZIP de cartera: B.8 Control + B.1 Renta Fija. Devuelve (n_ctrl, n_fi)."""
    from collectors.seguros_cartera import parse_zip_control, parse_zip_fixed_income
    n_ctrl = n_fi = 0
    recs = parse_zip_control(zip_path)
    if recs:
        n_ctrl = db.insert_insurer_portfolio_control(recs[0]["period"], recs[0]["insurance_type"], recs)
    fi = parse_zip_fixed_income(zip_path)
    if fi:
        n_fi = db.insert_insurer_portfolio_fixed_income(fi[0]["period"], fi[0]["insurance_type"], fi)
    return n_ctrl, n_fi


def probe_cmf_cartera(db, today):
    out = []
    ey, em = _expected_month(today, LAG_CMF_CARTERA_SEGUROS)
    expected = ey * 100 + em
    for itype in ("vida", "generales"):
        have = db.get_latest_insurer_portfolio_period(itype)
        out.append(FreshnessStatus(
            source=f"CMF · cartera de seguros {itype}", kind="cmf_cartera", frequency="monthly",
            latest_have=str(have) if have else None, expected=str(expected),
            due=have is None or have < expected,
            params={"insurance_type": itype,
                    "periods": _months_between(have, expected, MAX_MONTHLY_BACKFILL)}))
    return out


def fetch_cmf_cartera(db, st):
    from collectors.seguros_cartera import download_cartera
    started = datetime.now()
    itype, total = st.params["insurance_type"], 0
    for p in st.params["periods"]:
        zp = download_cartera(p, itype, "data/seguros_cartera_raw", force=st.params.get("force", False))
        if not zp:
            break  # si un mes no está publicado, los siguientes tampoco
        n_ctrl, _ = ingest_cartera_zip(db, zp)
        total += n_ctrl
    return _log(db, f"cmf_cartera_{itype}:{st.params['periods'][-1]}", "cmf", total, started)


def refresh_cmf_cartera(db, n=2):
    for itype in ("vida", "generales"):
        periods = db.get_insurer_portfolio_periods(itype)[:n]
        if periods:
            fetch_cmf_cartera(db, FreshnessStatus("", "cmf_cartera", "", None, "", True, {
                "insurance_type": itype, "periods": sorted(periods), "force": True}))


# ============================================================
# CMF · fondos mutuos
# ============================================================

def _ffmm_last_cuota_period(db) -> Optional[int]:
    r = db.conn.execute("SELECT MAX(date) FROM cmf_mutual_fund_quota_values").fetchone()[0]
    return r.year * 100 + r.month if r else None


def probe_ffmm_cuotas(db, today):
    ey, em = _expected_month(today, LAG_FFMM_CUOTAS)
    expected = ey * 100 + em
    have = _ffmm_last_cuota_period(db)
    return [FreshnessStatus(
        source="CMF · fondos mutuos, valor cuota (corte mensual)", kind="cmf_fm_cuotas",
        frequency="monthly", latest_have=str(have) if have else None, expected=str(expected),
        due=have is None or have < expected,
        params={"periods": _months_between(have, expected, MAX_MONTHLY_BACKFILL)})]


def fetch_ffmm_cuotas(db, st):
    from collectors.cmf_mutual_funds import CMFMutualFundsCollector
    started, c, total = datetime.now(), CMFMutualFundsCollector(), 0
    for p in st.params["periods"]:
        _, res = c.fetch_month_end_stats(p // 100, p % 100)
        if not res["records"]:
            continue
        total += db.upsert_mutual_fund_quotas(res["records"])
        db.upsert_mutual_fund_types(res["fund_types"])
    if total:
        db.propagar_agf_rut()
    return _log(db, f"cmf_fm_cuotas:{st.params['periods'][-1]}", "cmf", total, started)


def probe_ffmm_cartera(db, today):
    ey, em = _expected_month(today, LAG_FFMM_CARTERA)
    expected = ey * 100 + em
    r = db.conn.execute(
        "SELECT MAX(period_year * 100 + period_month) FROM cmf_mutual_fund_portfolio").fetchone()[0]
    have = int(r) if r else None
    return [FreshnessStatus(
        source="CMF · fondos mutuos, cartera", kind="cmf_fm_cartera", frequency="monthly",
        latest_have=str(have) if have else None, expected=str(expected),
        due=have is None or have < expected,
        params={"periods": _months_between(have, expected, MAX_MONTHLY_BACKFILL)})]


def fetch_ffmm_cartera(db, st):
    from collectors.cmf_mutual_funds import CMFMutualFundsCollector
    started, c, total = datetime.now(), CMFMutualFundsCollector(), 0
    for p in st.params["periods"]:
        recs = c.fetch_monthly_portfolio(p // 100, p % 100)
        if recs:
            total += db.upsert_mutual_fund_portfolio(recs)["inserted"]
    return _log(db, f"cmf_fm_cartera:{st.params['periods'][-1]}", "cmf", total, started)


def probe_ffmm_comisiones(db, today):
    ey, em = _expected_month(today, LAG_FFMM_COMISIONES)
    expected = ey * 100 + em
    r = db.conn.execute("SELECT MAX(periodo) FROM cmf_mutual_fund_fees").fetchone()[0]
    have = int(r) if r else None
    return [FreshnessStatus(
        source="CMF · fondos mutuos, comisiones", kind="cmf_fm_comisiones", frequency="monthly",
        latest_have=str(have) if have else None, expected=str(expected),
        due=have is None or have < expected,
        params={"periods": _months_between(have, expected, MAX_MONTHLY_BACKFILL)})]


def fetch_ffmm_comisiones(db, st):
    from collectors.cmf_mutual_funds import CMFMutualFundsCollector
    started, c, total = datetime.now(), CMFMutualFundsCollector(), 0
    for p in st.params["periods"]:
        total += db.upsert_mutual_fund_fees(c.fetch_comisiones(p // 100, p % 100))
    return _log(db, f"cmf_fm_comisiones:{st.params['periods'][-1]}", "cmf", total, started)


def probe_ffmm_comparador(db, today):
    ey, eq = _expected_quarter(today, LAG_FFMM_COMPARADOR)
    expected = ey * 10000 + eq * 300 + _month_end(ey, eq * 3).day
    r = db.conn.execute("SELECT MAX(fecha) FROM cmf_mutual_fund_returns").fetchone()[0]
    have = int(r) if r else None
    return [FreshnessStatus(
        source="CMF · fondos mutuos, comparador oficial", kind="cmf_fm_comparador",
        frequency="quarterly", latest_have=str(have) if have else None, expected=str(expected),
        due=have is None or have < expected, params={})]


def fetch_ffmm_comparador(db, st):
    from collectors.cmf_mutual_funds import CMFMutualFundsCollector
    started = datetime.now()
    datos = CMFMutualFundsCollector().fetch_comparador()
    if not datos["returns"]:
        return _log(db, "cmf_fm_comparador", "cmf", 0, started)
    nuevo = datos["fecha"] != (db.get_mutual_fund_returns_corte() or "")
    n = db.upsert_mutual_fund_returns(datos["returns"])
    db.upsert_mutual_fund_agfs(datos["agfs"])
    db.propagar_agf_rut()
    # Si la CMF aún no publica el corte nuevo, se recarga el mismo: no cuenta como novedad.
    return _log(db, f"cmf_fm_comparador:{datos['fecha']}", "cmf", n if nuevo else 0, started)


# ============================================================
# Superintendencia de Pensiones
# ============================================================

def probe_sp_cuotas(db, today):
    expected_d = _last_business_day_before(today)
    have_d = _to_date(db.get_latest_sp_quota_date())
    years = sorted({(have_d or expected_d).year, expected_d.year})
    return [FreshnessStatus(
        source="SP · valores cuota", kind="sp_cuotas", frequency="daily",
        latest_have=have_d.isoformat() if have_d else None, expected=expected_d.isoformat(),
        due=have_d is None or have_d < expected_d, params={"years": years})]


def fetch_sp_cuotas(db, st):
    from collectors.sp_pensions import SPPensionCollector
    started, c, total = datetime.now(), SPPensionCollector(), 0
    fecconf = c.fetch_fecconf()
    for year in st.params.get("years") or [st.params.get("year")]:
        for fund in ("A", "B", "C", "D", "E"):
            recs = c.fetch_quota_values(year, year, fund_type=fund, fecconf=fecconf)
            if recs:
                total += db.insert_sp_quota_values(recs)
    return _log(db, "sp_cuotas", "sp", total, started)


def probe_sp_precios(db, today):
    expected_d = _last_business_day_before(today)
    have_d = _to_date(db.get_latest_sp_price_date())
    return [FreshnessStatus(
        source="SP · cinta de precios", kind="sp_precios", frequency="daily",
        latest_have=have_d.isoformat() if have_d else None, expected=expected_d.isoformat(),
        due=have_d is None or have_d < expected_d,
        params={"from_date": have_d.isoformat() if have_d else None, "to_date": expected_d.isoformat()})]


def _business_days(start_excl: dt.date, end_incl: dt.date, cap: int) -> list[str]:
    days, d = [], start_excl + dt.timedelta(days=1)
    while d <= end_incl and len(days) < cap:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    return days


def fetch_sp_precios(db, st):
    from collectors.sp_pensions import SPPensionCollector
    started, c, total = datetime.now(), SPPensionCollector(), 0
    to_d = dt.date.fromisoformat(st.params["to_date"])
    from_s = st.params.get("from_date")
    days = _business_days(dt.date.fromisoformat(from_s), to_d, MAX_DAILY_BACKFILL) if from_s else [to_d.isoformat()]
    for d_str in days:
        recs = c.fetch_daily_prices(d_str)
        if recs:
            total += db.insert_sp_instrument_prices(recs)
    return _log(db, "sp_precios", "sp", total, started)


def probe_sp_cartera(db, today):
    ey, em = _expected_month(today, LAG_SP_CARTERA)
    expected = ey * 100 + em
    have = db.get_latest_sp_portfolio_period()  # 'YYYY-MM'
    have_p = int(have.replace("-", "")) if have else None
    return [FreshnessStatus(
        source="SP · cartera mensual", kind="sp_cartera", frequency="monthly",
        latest_have=have, expected=f"{ey}-{em:02d}", due=have_p is None or have_p < expected,
        params={"periods": _months_between(have_p, expected, MAX_MONTHLY_BACKFILL)})]


def fetch_sp_cartera(db, st):
    from collectors.sp_pensions import SPPensionCollector
    started, c, total = datetime.now(), SPPensionCollector(), 0
    for p in st.params["periods"]:
        recs = c.fetch_portfolio(p)
        if not recs:
            break
        total += db.insert_sp_portfolio_holdings(f"{p // 100}-{p % 100:02d}", recs)
    return _log(db, f"sp_cartera:{st.params['periods'][-1]}", "sp", total, started)


# ============================================================
# Registro
# ============================================================

SOURCES: list[Source] = [
    Source("bcentral", "Banco Central (BCCh)", "bcentral", probe_bcentral, fetch_bcentral),
    Source("cmf_emp", "CMF Empresas (EEFF)", "cmf", probe_cmf_emp, fetch_cmf_emp, refresh_cmf_emp),
    Source("cmf_bank", "CMF Bancos (SBIF)", "cmf", probe_cmf_bank, fetch_cmf_bank),
    Source("cmf_insurer", "CMF Seguros (EEFF FECU)", "cmf", probe_cmf_insurer, fetch_cmf_insurer, refresh_cmf_insurer),
    Source("cmf_broker", "CMF Intermediarios de valores", "cmf", probe_cmf_broker, fetch_cmf_broker, refresh_cmf_broker),
    Source("cmf_cartera", "Cartera de seguros (Circ. 1.835)", "cmf", probe_cmf_cartera, fetch_cmf_cartera, refresh_cmf_cartera),
    Source("cmf_fm_cuotas", "Fondos mutuos · valor cuota", "cmf", probe_ffmm_cuotas, fetch_ffmm_cuotas),
    Source("cmf_fm_cartera", "Fondos mutuos · cartera", "cmf", probe_ffmm_cartera, fetch_ffmm_cartera),
    Source("cmf_fm_comisiones", "Fondos mutuos · comisiones", "cmf", probe_ffmm_comisiones, fetch_ffmm_comisiones),
    Source("cmf_fm_comparador", "Fondos mutuos · comparador oficial", "cmf", probe_ffmm_comparador, fetch_ffmm_comparador),
    Source("sp_cuotas", "SP Valores cuota AFP", "sp", probe_sp_cuotas, fetch_sp_cuotas),
    Source("sp_precios", "SP Cinta de precios", "sp", probe_sp_precios, fetch_sp_precios),
    Source("sp_cartera", "SP Cartera AFP", "sp", probe_sp_cartera, fetch_sp_cartera),
]

BY_KIND: dict[str, Source] = {s.kind: s for s in SOURCES}
