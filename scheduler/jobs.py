"""
scheduler/jobs.py — Orquestación de la ingesta automática.

Un solo mecanismo: el catch-up por frescura. Cada hora hábil sondea todas las fuentes del
registro (`scheduler/sources.SOURCES`), descarga solo lo `due` y al final corre los
controles de calidad (`db/quality.py`). Los jobs cron fijos que había antes (diario,
mensual, trimestral, anual, SP diario y SP mensual) se eliminaron: duplicaban el catch-up
y no sabían de completitud ni de fuentes nuevas.

Además, una vez al mes se reingestan los 2 últimos períodos de las fuentes CMF que
corrigen datos ya publicados (`refresh_recent`).
"""

import datetime as dt
import logging
from datetime import datetime

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from db.database import Database
from config.settings import settings
from scheduler.freshness import probe_all, FreshnessStatus
from scheduler.sources import BY_KIND, SOURCES, fetch_bcentral, load_catalog

logger = logging.getLogger(__name__)


# ============================================================
# Backfill completo del catálogo BCCh (uso manual)
# ============================================================

def run_all_series(from_date: str | None = None) -> None:
    """Descarga todas las series vigentes del catálogo BCCh (backfill)."""
    series = [m for m in load_catalog().get("bcentral", []) if not m.get("discontinued")]
    logger.info(f"🚀 Backfill BCCh: {len(series)} series")
    with Database() as db:
        for meta in series:
            st = FreshnessStatus(meta.get("name", meta["id"]), "bcentral", meta.get("frequency", ""),
                                 None, None, True, {"series_id": meta["id"], "from_date": from_date, "meta": meta})
            try:
                fetch_bcentral(db, st)
            except Exception as e:
                db.log_fetch(meta["id"], "bcentral", "error", error_msg=str(e))
                logger.error(f"Error procesando {meta['id']}: {e}")


# ============================================================
# Catch-up dirigido por frescura
# ============================================================

def _dispatch_catchup(db: Database, st: FreshnessStatus) -> tuple[str, int]:
    """Descarga e ingesta un objetivo `due` con el fetch de su fuente. Devuelve (estado, n)."""
    src = BY_KIND.get(st.kind)
    if not src:
        logger.error(f"Fuente sin registrar: {st.kind}")
        return "skip", 0
    return src.fetch(db, st)


def run_catchup(dry_run: bool = False, today: dt.date = None, kinds: list[str] | None = None,
                validate: bool = True) -> list[FreshnessStatus]:
    """
    Ingesta dirigida por frescura: descarga solo lo faltante o incompleto y dentro de su
    ventana de publicación. Idempotente. Devuelve la lista de FreshnessStatus.
    """
    logger.info(f"⏰ Catch-up por frescura iniciado {datetime.now():%Y-%m-%d %H:%M} (dry_run={dry_run})")
    # En dry-run solo se sondea: abrir read_only evita el lock.
    with Database(read_only=dry_run) as db:
        statuses = probe_all(db, today=today, kinds=kinds)
        due = [s for s in statuses if s.due]
        logger.info(f"Frescura: {len(due)}/{len(statuses)} objetivos pendientes (due).")
        if dry_run:
            return statuses
        nuevos = 0
        for st in due:
            try:
                status, n = _dispatch_catchup(db, st)
                nuevos += n or 0
                logger.info(f"  {st.source}: {status} (+{n})")
            except Exception as e:
                logger.error(f"  {st.source}: error — {e}")
                try:
                    db.log_fetch(f"{st.kind}:{st.expected}", BY_KIND[st.kind].source_id,
                                 "error", error_msg=str(e))
                except Exception:
                    pass
        # Las capas derivadas y los controles cuestan ~20 s: solo si llegaron filas nuevas
        # (el catch-up corre cada hora y la mayoría de las veces no hay nada publicado).
        if nuevos:
            try:
                from db.products import rebuild_all
                rebuild_all(db)
            except Exception as e:
                logger.error(f"Reconstrucción de entidades/productos/métricas falló: {e}")
        if validate and nuevos:
            try:
                from db.quality import run_checks
                run_checks(db)
            except Exception as e:
                logger.error(f"Controles de calidad fallaron: {e}")
        if due:
            # Regla del proyecto: tras cada recarga se borran los crudos que la alimentaron.
            from scheduler.housekeeping import despues_de_cargar
            despues_de_cargar(db)
    return statuses


def refresh_recent(n: int = 2) -> None:
    """Reingesta los últimos `n` períodos de las fuentes que la CMF corrige tras publicar."""
    with Database() as db:
        for src in SOURCES:
            if src.refresh:
                try:
                    src.refresh(db, n)
                    logger.info(f"Reingesta de correcciones: {src.kind} OK")
                except Exception as e:
                    logger.error(f"Reingesta de correcciones {src.kind}: {e}")
        from scheduler.housekeeping import despues_de_cargar
        despues_de_cargar(db)


# ============================================================
# Scheduler
# ============================================================

def _on_job_error(event):
    logger.error(f"Job falló: {event.job_id} — {event.exception}")


def _on_job_executed(event):
    logger.info(f"Job completado: {event.job_id}")


def create_scheduler(blocking: bool = True) -> BlockingScheduler | BackgroundScheduler:
    """Scheduler con dos jobs: catch-up horario y reingesta mensual de correcciones."""
    SchedulerClass = BlockingScheduler if blocking else BackgroundScheduler
    scheduler = SchedulerClass(timezone=settings.TIMEZONE)
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)

    # Cada sonda se auto-restringe por su ventana de publicación: correr seguido es barato
    # e idempotente.
    scheduler.add_job(
        run_catchup, trigger="cron", day_of_week="mon-fri", hour="8-20", minute=0,
        id="catchup_fetch", name="Catch-up por frescura (ingesta al publicarse)",
        replace_existing=True, misfire_grace_time=3600)

    scheduler.add_job(
        refresh_recent, trigger="cron", day=20, hour=21, minute=0,
        id="refresh_recent", name="Reingesta mensual de los 2 últimos períodos (correcciones CMF)",
        replace_existing=True, misfire_grace_time=3600 * 12)

    logger.info("Scheduler configurado: catch-up horario + reingesta mensual de correcciones")
    return scheduler
