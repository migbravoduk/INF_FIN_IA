"""
db/database.py — Capa de acceso a DuckDB

Uso:
    from db.database import Database
    db = Database()   # crea/abre la BD
    db.upsert_observations('F073.IPC.IND...', [{'date': '2024-01-01', 'value': 150.2}])
    df = db.get_series('F073.IPC.IND...', from_date='2020-01-01')
"""

import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import duckdb

from db.schema import SCHEMA_SQL, SEED_SOURCES_SQL
from config.settings import settings

logger = logging.getLogger(__name__)

# Utilidades de módulo: viven en db/common.py; se re-exportan acá porque el resto del
# repo las importa desde db.database.
from db.common import (is_total_account, AGF_SQL_PREDICATE, _agf_where, is_agf_name,  # noqa: F401
                       compute_ratios, _TOTAL_RE)
from db.repos.macro import MacroRepo
from db.repos.panel import PanelRepo
from db.repos.eeff import EeffRepo
from db.repos.bancos import BancosRepo
from db.repos.fecu import FecuRepo
from db.repos.sp import SpRepo
from db.repos.ffmm import FfmmRepo


class Database(MacroRepo, PanelRepo, EeffRepo, BancosRepo, FecuRepo, SpRepo, FfmmRepo):
    """Gestiona la conexión y operaciones sobre el DuckDB local."""

    def __init__(self, db_path: Optional[str] = None, read_only: bool = False):
        """
        Args:
            db_path: Ruta al archivo DuckDB (default: settings.DB_PATH).
            read_only: Si True, abre la BD en modo solo-lectura. Necesario para que
                       la API (proceso lector) coexista con el scheduler (único escritor),
                       ya que DuckDB admite un solo proceso escritor. En modo lectura
                       NO se inicializa el schema (no se puede crear/modificar).
        """
        path = db_path or settings.DB_PATH
        self.read_only = read_only
        if not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(path, read_only=read_only)
        if not read_only:
            self._init_schema()
        logger.info(f"Base de datos abierta en: {path} (read_only={read_only})")

    def _init_schema(self):
        """Crea tablas si no existen y carga fuentes base."""
        self.conn.execute(SCHEMA_SQL)
        self.conn.execute(SEED_SOURCES_SQL)
        from db.migrations import run_migrations
        run_migrations(self.conn)

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
