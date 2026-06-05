"""
db/database.py — Capa de acceso a DuckDB

Uso:
    from db.database import Database
    db = Database()   # crea/abre la BD
    db.upsert_observations('F073.IPC.IND...', [{'date': '2024-01-01', 'value': 150.2}])
    df = db.get_series('F073.IPC.IND...', from_date='2020-01-01')
"""

import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import duckdb

from db.schema import SCHEMA_SQL, SEED_SOURCES_SQL
from config.settings import settings

logger = logging.getLogger(__name__)


class Database:
    """Gestiona la conexión y operaciones sobre el DuckDB local."""

    def __init__(self, db_path: Optional[str] = None):
        path = db_path or settings.DB_PATH
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(path)
        self._init_schema()
        logger.info(f"Base de datos abierta en: {path}")

    def _init_schema(self):
        """Crea tablas si no existen y carga fuentes base."""
        self.conn.execute(SCHEMA_SQL)
        self.conn.execute(SEED_SOURCES_SQL)

    # ----------------------------------------------------------
    # Series — metadatos
    # ----------------------------------------------------------

    def upsert_series(self, series_meta: dict) -> None:
        """Inserta o actualiza metadatos de una serie."""
        self.conn.execute("""
            INSERT INTO series (id, source_id, name, category, frequency, unit, description, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                frequency = excluded.frequency,
                unit = excluded.unit,
                description = excluded.description,
                last_updated = excluded.last_updated
        """, [
            series_meta['id'],
            series_meta['source_id'],
            series_meta['name'],
            series_meta.get('category'),
            series_meta.get('frequency'),
            series_meta.get('unit'),
            series_meta.get('description'),
            datetime.now(),
        ])

    def get_all_series(self, source_id: Optional[str] = None):
        """Retorna todas las series registradas."""
        if source_id:
            return self.conn.execute(
                "SELECT * FROM series WHERE source_id = ?", [source_id]
            ).fetchdf()
        return self.conn.execute("SELECT * FROM series").fetchdf()

    # ----------------------------------------------------------
    # Observations — datos en serie de tiempo
    # ----------------------------------------------------------

    def upsert_observations(self, series_id: str, records: list[dict]) -> tuple[int, int]:
        """
        Inserta o actualiza observaciones de forma eficiente (bulk).
        Retorna (nuevas, actualizadas).
        """
        if not records:
            return 0, 0

        new_count = 0
        updated_count = 0

        # 1. Insertar todos los registros nuevos ignorando conflictos de clave única
        for rec in records:
            try:
                self.conn.execute("""
                    INSERT INTO observations (id, series_id, date, value)
                    VALUES (nextval('obs_seq'), ?, ?, ?)
                """, [series_id, rec['date'], rec['value']])
                new_count += 1
            except Exception:
                # Ya existe — verificar si el valor cambió (revisión)
                existing = self.conn.execute(
                    "SELECT value FROM observations WHERE series_id = ? AND date = ?",
                    [series_id, rec['date']]
                ).fetchone()
                if existing and existing[0] != rec['value']:
                    self.conn.execute("""
                        UPDATE observations
                        SET value = ?, is_revised = true, fetched_at = now()
                        WHERE series_id = ? AND date = ?
                    """, [rec['value'], series_id, rec['date']])
                    updated_count += 1

        return new_count, updated_count

    def get_series(
        self,
        series_id: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        """Recupera observaciones de una serie como DataFrame."""
        query = "SELECT date, value FROM observations WHERE series_id = ?"
        params = [series_id]

        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)

        query += " ORDER BY date ASC"
        return self.conn.execute(query, params).fetchdf()

    def get_latest_value(self, series_id: str) -> Optional[dict]:
        """Retorna el valor más reciente de una serie."""
        row = self.conn.execute(
            "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 1",
            [series_id]
        ).fetchone()
        if row:
            return {'date': row[0], 'value': row[1]}
        return None

    # ----------------------------------------------------------
    # Fetch log
    # ----------------------------------------------------------

    def log_fetch(
        self,
        series_id: str,
        source_id: str,
        status: str,
        records_new: int = 0,
        records_updated: int = 0,
        error_msg: Optional[str] = None,
        started_at: Optional[datetime] = None,
    ) -> None:
        """Registra el resultado de un fetch en el log."""
        self.conn.execute("""
            INSERT INTO fetch_log 
                (id, series_id, source_id, started_at, finished_at, status, 
                 records_new, records_updated, error_msg)
            VALUES (nextval('log_seq'), ?, ?, ?, now(), ?, ?, ?, ?)
        """, [
            series_id, source_id,
            started_at or datetime.now(),
            status, records_new, records_updated, error_msg
        ])

    def get_fetch_history(self, series_id: Optional[str] = None, limit: int = 50):
        """Retorna el historial de fetches."""
        if series_id:
            return self.conn.execute(
                "SELECT * FROM fetch_log WHERE series_id = ? ORDER BY started_at DESC LIMIT ?",
                [series_id, limit]
            ).fetchdf()
        return self.conn.execute(
            "SELECT * FROM fetch_log ORDER BY started_at DESC LIMIT ?", [limit]
        ).fetchdf()

    # ----------------------------------------------------------
    # Estados Financieros CMF (Fase 3)
    # ----------------------------------------------------------

    def insert_cmf_records(self, period: int, records: list[dict]) -> int:
        """
        Inserta estados financieros corporativos de la CMF usando "Delete-then-Insert".
        Ejecuta la operación completa de forma transaccional y atómica.
        Retorna la cantidad de registros insertados.
        """
        if not records:
            return 0

        # Convertir a tuplas limpias para ejecutemany
        tuples_data = [
            (
                int(rec['period']),
                str(rec['rut']).strip().replace(".", "").replace("-", ""),
                str(rec['company_name']).strip(),
                str(rec['report_type']).strip(),
                str(rec['currency']).strip(),
                str(rec['account_name']).strip(),
                float(rec['value']),
                rec.get('taxonomy_code'),
                rec.get('statement_group')
            )
            for rec in records
        ]

        # Obtener los períodos únicos presentes en el lote de registros
        unique_periods = list(set(int(rec['period']) for rec in records))

        # Iniciar transacción explícita
        self.conn.execute("BEGIN TRANSACTION")
        try:
            # 1. Eliminar datos existentes de forma atómica para cada período presente en el lote
            for p in unique_periods:
                self.conn.execute("DELETE FROM cmf_financial_statements WHERE period = ?", [p])

            # 2. Bulk insert usando la eficiencia nativa de DuckDB executemany
            self.conn.executemany("""
                INSERT INTO cmf_financial_statements 
                    (period, rut, company_name, report_type, currency, account_name, value, taxonomy_code, statement_group)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)

            self.conn.execute("COMMIT")
            logger.info(f"Ingestados con éxito {len(records)} registros CMF para los períodos {unique_periods}.")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al ingestar registros CMF para el período {period}. Transacción revertida.", exc_info=True)
            raise e

    def get_cmf_companies(self):
        """Retorna un DataFrame con todas las empresas (RUT y Razón Social) registradas."""
        return self.conn.execute("""
            SELECT DISTINCT rut, company_name 
            FROM cmf_financial_statements 
            ORDER BY company_name ASC
        """).fetchdf()

    def query_cmf_statements(
        self,
        rut: Optional[str] = None,
        company: Optional[str] = None,
        period: Optional[int] = None,
        limit: int = 50
    ):
        """Recupera estados financieros de CMF en base a filtros flexibles como DataFrame."""
        query = """
            SELECT period, rut, company_name, report_type, currency, account_name, value, statement_group 
            FROM cmf_financial_statements 
            WHERE 1=1
        """
        params = []

        if period:
            query += " AND period = ?"
            params.append(period)
        if rut:
            # Limpiar RUT de entrada para coincidir con la base de datos
            clean_rut = str(rut).strip().replace(".", "").replace("-", "")
            query += " AND rut = ?"
            params.append(clean_rut)
        if company:
            query += " AND LOWER(company_name) LIKE ?"
            params.append(f"%{company.lower()}%")

        query += " ORDER BY company_name ASC, statement_group ASC, account_name ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    # ----------------------------------------------------------
    # Bancos CMF (Fase 4)
    # ----------------------------------------------------------

    def insert_bank_records(self, year: int, month: int, bank_code: str, report_type: str, records: list[dict]) -> int:
        """
        Inserta estados financieros de bancos usando "Delete-then-Insert" atómico.
        Garantiza que la operación completa sea transaccional.
        Retorna la cantidad de registros insertados.
        """
        if not records:
            return 0

        # Estandarizar código de banco a 3 dígitos (ej. '1' -> '001')
        clean_bank_code = str(bank_code).strip().zfill(3)

        tuples_data = [
            (
                int(rec['year']),
                int(rec['month']),
                int(rec['period']),
                clean_bank_code,
                str(rec['bank_name']).strip(),
                str(rec['report_type']).strip(),
                str(rec['account_code']).strip(),
                str(rec['account_name']).strip(),
                float(rec['val_clp_no_reaj']) if rec.get('val_clp_no_reaj') is not None else None,
                float(rec['val_clp_reaj_ipc']) if rec.get('val_clp_reaj_ipc') is not None else None,
                float(rec['val_clp_reaj_tc']) if rec.get('val_clp_reaj_tc') is not None else None,
                float(rec['val_extranjera']) if rec.get('val_extranjera') is not None else None,
                float(rec['val_total'])
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            # 1. Eliminar datos existentes del banco, período y tipo de reporte específicos
            self.conn.execute("""
                DELETE FROM cmf_bank_statements 
                WHERE year = ? AND month = ? AND bank_code = ? AND report_type = ?
            """, [year, month, clean_bank_code, report_type])

            # 2. Bulk insert masivo
            self.conn.executemany("""
                INSERT INTO cmf_bank_statements 
                    (year, month, period, bank_code, bank_name, report_type, 
                     account_code, account_name, val_clp_no_reaj, val_clp_reaj_ipc, 
                     val_clp_reaj_tc, val_extranjera, val_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)

            self.conn.execute("COMMIT")
            logger.info(f"Ingestados con éxito {len(records)} registros bancarios ({report_type}) para el banco {clean_bank_code} en {year}-{month:02d}.")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al ingestar registros bancarios para el banco {clean_bank_code} en {year}-{month:02d}. Transacción revertida.", exc_info=True)
            raise e

    def query_bank_statements(
        self,
        bank_code: Optional[str] = None,
        period: Optional[int] = None,
        account_code: Optional[str] = None,
        report_type: Optional[str] = None,
        limit: int = 50
    ):
        """Recupera estados financieros mensuales de bancos con filtros flexibles como DataFrame."""
        query = """
            SELECT year, month, period, bank_code, bank_name, report_type, 
                   account_code, account_name, val_clp_no_reaj, val_clp_reaj_ipc, 
                   val_clp_reaj_tc, val_extranjera, val_total 
            FROM cmf_bank_statements 
            WHERE 1=1
        """
        params = []

        if period:
            query += " AND period = ?"
            params.append(period)
        if bank_code:
            # Estandarizar a 3 dígitos (ej: '1' -> '001')
            clean_code = str(bank_code).strip().zfill(3)
            query += " AND bank_code = ?"
            params.append(clean_code)
        if account_code:
            query += " AND account_code = ?"
            params.append(str(account_code).strip())
        if report_type:
            query += " AND report_type = ?"
            params.append(str(report_type).strip())

        query += " ORDER BY period ASC, bank_name ASC, account_code ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    # ----------------------------------------------------------
    # Superintendencia de Pensiones (SP)
    # ----------------------------------------------------------

    def insert_sp_quota_values(self, records: list[dict]) -> int:
        """
        Inserta valores cuota y patrimonio de la SP de forma transaccional.
        Utiliza ON CONFLICT DO UPDATE para evitar duplicados.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['date']),
                str(rec['afp_name']).upper().strip(),
                str(rec['fund_type']).upper().strip(),
                float(rec['quota_value']),
                float(rec['equity_value'])
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany("""
                INSERT INTO sp_quota_values (date, afp_name, fund_type, quota_value, equity_value)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (date, afp_name, fund_type) DO UPDATE SET
                    quota_value = excluded.quota_value,
                    equity_value = excluded.equity_value,
                    fetched_at = now()
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error("Error al insertar valores cuota de la SP en DuckDB", exc_info=True)
            raise e

    def insert_sp_portfolio_holdings(self, period: str, records: list[dict]) -> int:
        """
        Inserta la cartera mensual desagregada de la SP usando Delete-then-Insert.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['period']),
                str(rec['afp_name']).upper().strip(),
                str(rec['fund_type']).upper().strip(),
                str(rec['instrument_glosa']).strip(),
                float(rec['monto_pesos']) if rec.get('monto_pesos') is not None else None,
                float(rec['monto_dolares']) if rec.get('monto_dolares') is not None else None,
                float(rec['porcentaje']) if rec.get('porcentaje') is not None else None
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM sp_portfolio_holdings WHERE period = ?", [period])
            self.conn.executemany("""
                INSERT INTO sp_portfolio_holdings (period, afp_name, fund_type, instrument_glosa, monto_pesos, monto_dolares, porcentaje)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error al insertar cartera SP para el período {period} en DuckDB", exc_info=True)
            raise e

    def insert_sp_instrument_prices(self, records: list[dict]) -> int:
        """
        Inserta precios diarios de instrumentos financieros de la SP.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['date']),
                str(rec['instrument_id']).strip(),
                str(rec.get('instrument_type', '')).strip(),
                str(rec.get('currency', '')).strip(),
                float(rec['price'])
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany("""
                INSERT INTO sp_instrument_prices (date, instrument_id, instrument_type, currency, price)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (date, instrument_id) DO UPDATE SET
                    instrument_type = excluded.instrument_type,
                    currency = excluded.currency,
                    price = excluded.price,
                    fetched_at = now()
            """, tuples_data)
            self.conn.execute("COMMIT")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error("Error al insertar precios de instrumentos SP en DuckDB", exc_info=True)
            raise e

    def query_sp_quota_values(
        self,
        afp: Optional[str] = None,
        fund: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 50
    ):
        """Consulta valores cuota y patrimonio registrados con filtros flexibles."""
        query = "SELECT date, afp_name, fund_type, quota_value, equity_value FROM sp_quota_values WHERE 1=1"
        params = []

        if afp:
            query += " AND UPPER(afp_name) = ?"
            params.append(afp.upper().strip())
        if fund:
            query += " AND UPPER(fund_type) = ?"
            params.append(fund.upper().strip())
        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)

        query += " ORDER BY date DESC, afp_name ASC, fund_type ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    def query_sp_instrument_prices(
        self,
        instrument_id: Optional[str] = None,
        date: Optional[str] = None,
        limit: int = 50
    ):
        """Consulta la cinta de precios diaria de instrumentos con filtros flexibles."""
        query = "SELECT date, instrument_id, instrument_type, currency, price FROM sp_instrument_prices WHERE 1=1"
        params = []

        if instrument_id:
            query += " AND UPPER(instrument_id) LIKE ?"
            params.append(f"%{instrument_id.upper().strip()}%")
        if date:
            query += " AND date = ?"
            params.append(date)

        query += " ORDER BY date DESC, instrument_id ASC LIMIT ?"
        params.append(limit)

        return self.conn.execute(query, params).fetchdf()

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
