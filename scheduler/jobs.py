"""
scheduler/jobs.py — Definición de jobs de actualización automática

Usa APScheduler para ejecutar fetches periódicos según la frecuencia
de cada serie definida en config/series_catalog.yaml.

Jobs:
  - daily_job     : series con frequency='daily'   → cada día hábil a las 08:00
  - monthly_job   : series con frequency='monthly'  → día 5 de cada mes
  - quarterly_job : series con frequency='quarterly' → día 10 de ene/abr/jul/oct
"""

import logging
from datetime import datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from collectors.bcentral import BCentralCollector
from collectors.sp_pensions import SPPensionCollector
from db.database import Database
from processors.normalizer import normalize_observations
from config.settings import settings

logger = logging.getLogger(__name__)


# ============================================================
# Funciones de fetch
# ============================================================

def fetch_bcentral_series(series_meta: dict, db: Database) -> None:
    """Descarga y almacena una serie del BCCh."""
    series_id = series_meta["id"]
    start = datetime.now()

    logger.info(f"🔄 Iniciando fetch: {series_meta['name']} ({series_id})")

    # Registrar la serie en la BD si no existe
    db.upsert_series({
        **series_meta,
        "source_id": "bcentral",
    })

    collector = BCentralCollector()
    try:
        raw_records = collector.fetch_series(series_id)
        clean_records = normalize_observations(raw_records, series_id)
        new, updated = db.upsert_observations(series_id, clean_records)

        db.log_fetch(
            series_id=series_id,
            source_id="bcentral",
            status="ok",
            records_new=new,
            records_updated=updated,
            started_at=start,
        )
        logger.info(f"✅ {series_meta['name']}: +{new} nuevas, {updated} revisadas")

    except Exception as e:
        db.log_fetch(
            series_id=series_id,
            source_id="bcentral",
            status="error",
            error_msg=str(e),
            started_at=start,
        )
        logger.error(f"❌ Error en {series_id}: {e}")


def run_fetch_by_frequency(frequency: str) -> None:
    """Ejecuta todos los fetches de una frecuencia determinada."""
    logger.info(f"⏰ Job '{frequency}' iniciado a las {datetime.now():%Y-%m-%d %H:%M}")

    catalog_path = settings.CATALOG_PATH
    with open(catalog_path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    bcentral_series = catalog.get("bcentral", [])
    target = [s for s in bcentral_series if s.get("frequency") == frequency]

    if not target:
        logger.info(f"No hay series configuradas para frecuencia '{frequency}'")
        return

    with Database() as db:
        for series_meta in target:
            try:
                fetch_bcentral_series(series_meta, db)
            except Exception as e:
                logger.error(f"Error procesando {series_meta.get('id')}: {e}")

    logger.info(f"✔️  Job '{frequency}' completado. {len(target)} series procesadas.")


def run_all_series() -> None:
    """Descarga todas las series del catálogo (útil para backfill inicial)."""
    logger.info("🚀 Ejecutando fetch COMPLETO de todas las series...")

    catalog_path = settings.CATALOG_PATH
    with open(catalog_path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    bcentral_series = catalog.get("bcentral", [])

    with Database() as db:
        for series_meta in bcentral_series:
            try:
                fetch_bcentral_series(series_meta, db)
            except Exception as e:
                logger.error(f"Error procesando {series_meta.get('id')}: {e}")

    logger.info(f"✔️  Backfill completo. {len(bcentral_series)} series procesadas.")


# ============================================================
# Tareas de la Superintendencia de Pensiones (SP)
# ============================================================

def run_sp_daily_fetch() -> None:
    """Descarga diaria de la cinta de precios e ingesta de valores cuota de la SP."""
    from datetime import date
    import datetime as dt_mod

    today_str = date.today().strftime("%Y-%m-%d")
    logger.info(f"🔄 [Scheduler SP] Iniciando descargas diarias de la SP para {today_str}...")

    collector = SPPensionCollector()
    with Database() as db:
        # 1. Descarga e ingesta de la cinta de precios diaria (pYYYYMMDD.zip)
        try:
            records_prices = collector.fetch_daily_prices(today_str)
            if records_prices:
                inserted = db.insert_sp_instrument_prices(records_prices)
                logger.info(f"✅ [Scheduler SP] Cinta de precios {today_str}: {inserted} precios ingresados.")
            else:
                logger.info(f"⚠️ [Scheduler SP] Cinta de precios {today_str} no disponible o es día inhábil.")
        except Exception as e:
            logger.error(f"❌ [Scheduler SP] Error al descargar precios para {today_str}: {e}")

        # 2. Ingesta de valores cuota (actualiza el año en curso)
        try:
            current_year = date.today().year
            fecconf = collector.fetch_fecconf()
            total_inserted = 0
            for fund in ["A", "B", "C", "D", "E"]:
                records_cuotas = collector.fetch_quota_values(current_year, current_year, fund_type=fund, fecconf=fecconf)
                if records_cuotas:
                    inserted = db.insert_sp_quota_values(records_cuotas)
                    total_inserted += inserted
            logger.info(f"✅ [Scheduler SP] Valores cuota: +{total_inserted} registros actualizados para el año {current_year}.")
        except Exception as e:
            logger.error(f"❌ [Scheduler SP] Error al descargar valores cuota para el año {current_year}: {e}")


def run_sp_monthly_fetch() -> None:
    """Descarga mensual de la cartera de inversión desagregada del mes anterior."""
    from datetime import date
    import datetime as dt_mod

    # Calcular el período del mes anterior (YYYYMM)
    today = date.today()
    first_day_current_month = today.replace(day=1)
    last_day_prev_month = first_day_current_month - dt_mod.timedelta(days=1)
    prev_period = int(last_day_prev_month.strftime("%Y%m"))

    logger.info(f"🔄 [Scheduler SP] Iniciando descarga mensual de cartera para período {prev_period}...")

    collector = SPPensionCollector()
    try:
        records = collector.fetch_portfolio(prev_period)
        if records:
            with Database() as db:
                db_period = f"{str(prev_period)[:4]}-{str(prev_period)[4:]}"
                inserted = db.insert_sp_portfolio_holdings(db_period, records)
                logger.info(f"✅ [Scheduler SP] Cartera mensual {db_period}: {inserted} activos ingresados.")
        else:
            logger.warning(f"⚠️ [Scheduler SP] Cartera mensual {prev_period} no disponible o vacía aún.")
    except Exception as e:
        logger.error(f"❌ [Scheduler SP] Error al descargar cartera mensual para {prev_period}: {e}")


# ============================================================
# Scheduler
# ============================================================

def _on_job_error(event):
    logger.error(f"Job falló: {event.job_id} — {event.exception}")


def _on_job_executed(event):
    logger.info(f"Job completado: {event.job_id}")


def create_scheduler(blocking: bool = True) -> BlockingScheduler | BackgroundScheduler:
    """
    Crea y configura el scheduler de APScheduler.

    Args:
        blocking: True para uso en CLI (bloquea el hilo principal).
                  False para uso embebido (background).
    """
    tz = settings.TIMEZONE
    hour, minute = settings.DAILY_FETCH_TIME.split(":")

    SchedulerClass = BlockingScheduler if blocking else BackgroundScheduler
    scheduler = SchedulerClass(timezone=tz)

    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)

    # Job diario — series con frequency='daily' (ej. tipo de cambio)
    scheduler.add_job(
        run_fetch_by_frequency,
        trigger="cron",
        args=["daily"],
        hour=int(hour),
        minute=int(minute),
        day_of_week="mon-fri",
        id="daily_fetch",
        name="Fetch diario (series diarias)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Job mensual — día 6 de cada mes (el BCCh publica IPC ~día 5-8)
    scheduler.add_job(
        run_fetch_by_frequency,
        trigger="cron",
        args=["monthly"],
        day=6,
        hour=9,
        minute=0,
        id="monthly_fetch",
        name="Fetch mensual (IPC, TPM, empleo, etc.)",
        replace_existing=True,
        misfire_grace_time=3600 * 6,
    )

    # Job trimestral — día 10 de enero, abril, julio, octubre
    scheduler.add_job(
        run_fetch_by_frequency,
        trigger="cron",
        args=["quarterly"],
        month="1,4,7,10",
        day=10,
        hour=9,
        minute=30,
        id="quarterly_fetch",
        name="Fetch trimestral (PIB, balanza de pagos)",
        replace_existing=True,
        misfire_grace_time=3600 * 12,
    )

    # Job anual — día 15 de febrero (datos fiscales del año anterior)
    scheduler.add_job(
        run_fetch_by_frequency,
        trigger="cron",
        args=["annual"],
        month=2,
        day=15,
        hour=10,
        minute=0,
        id="annual_fetch",
        name="Fetch anual (balance fiscal)",
        replace_existing=True,
    )

    # Job diario SP — Lunes-Viernes a las 18:30
    scheduler.add_job(
        run_sp_daily_fetch,
        trigger="cron",
        day_of_week="mon-fri",
        hour=18,
        minute=30,
        id="sp_daily_fetch",
        name="Fetch diario SP (valores cuota y precios diarios)",
        replace_existing=True,
        misfire_grace_time=3600 * 2,
    )

    # Job mensual SP — Día 15 de cada mes a las 20:00
    scheduler.add_job(
        run_sp_monthly_fetch,
        trigger="cron",
        day=15,
        hour=20,
        minute=0,
        id="sp_monthly_fetch",
        name="Fetch mensual SP (cartera de inversiones)",
        replace_existing=True,
        misfire_grace_time=3600 * 12,
    )

    logger.info("Scheduler configurado con 6 jobs (4 BCCh + 2 SP)")
    return scheduler
