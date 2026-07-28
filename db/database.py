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


import re as _re

# Detecta líneas de total/subtotal (para resaltarlas), incluyendo los totalizadores del
# flujo de efectivo y los resultados clave, que no contienen la palabra "total".
_TOTAL_RE = _re.compile(
    r"\btotal\b"
    r"|flujos de efectivo netos procedentes"
    r"|incremento \(disminución\) neto de efectivo"
    r"|^efectivo y equivalentes al efectivo al "
    r"|^ganancia bruta( \(\d+\))?$"
    r"|^ganancia \(pérdida\)( \(\d+\))?$",
    _re.IGNORECASE,
)


def is_total_account(name: str) -> bool:
    """True si la cuenta es un total/subtotal a resaltar (incluye totalizadores del EFE)."""
    return bool(_TOTAL_RE.search(name or ""))


# Predicado SQL para clasificar Administradoras Generales de Fondos (AGF) y afines
# (administradoras de fondos de inversión / para la vivienda) dentro de los EEFF corporativos.
# Se separan a su propia vista (como bancos y seguros). Requiere "ADMINISTRADORA" + "FONDOS";
# excluye AFP/cesantía (que además no están en este dataset). Un solo lugar para ajustarlo.
AGF_SQL_PREDICATE = (
    "(UPPER(company_name) LIKE '%ADMINISTRADORA%' "
    "AND UPPER(company_name) LIKE '%FONDOS%' "
    "AND UPPER(company_name) NOT LIKE '%PENSIONES%' "
    "AND UPPER(company_name) NOT LIKE '%CESANT%')"
)


def _agf_where(agf: Optional[bool], connector: str = "WHERE") -> str:
    """Fragmento SQL de filtro AGF: None=sin filtro, True=solo AGF, False=excluir AGF.
    `connector` es 'WHERE' o 'AND' según dónde se inserte."""
    if agf is None:
        return ""
    return f" {connector} {'' if agf else 'NOT '}{AGF_SQL_PREDICATE}"


def is_agf_name(name: str) -> bool:
    """Versión Python del predicado AGF (para clasificar fuera de SQL)."""
    u = (name or "").upper()
    if "PENSIONES" in u or "CESANT" in u:
        return False
    return "ADMINISTRADORA" in u and "FONDOS" in u


def compute_ratios(df) -> Optional[dict]:
    """
    Calcula indicadores financieros desde un DataFrame de EEFF de UNA empresa/período.
    Reutilizable sin conexión (la usa Database.get_company_ratios y el export estático).
    Income es del período (acumulado en el año para trimestres) → ratios "del período".
    """
    if df is None or df.empty:
        return None
    vals = {}
    for _, r in df.iterrows():
        n = str(r["account_name"])
        if n not in vals:  # primera ocurrencia = orden IFRS
            vals[n] = r["value"]

    def g(name):
        v = vals.get(name)
        return float(v) if (v is not None and v == v) else None

    def div(a, b):
        return (a / b) if (a is not None and b not in (None, 0)) else None

    activos, pasivos, patrim = g("Total de activos"), g("Total de pasivos"), g("Patrimonio total")
    ganancia = g("Ganancia (pérdida)")
    ac, pc = g("Activos corrientes totales"), g("Pasivos corrientes totales")
    ingresos, gbruta = g("Ingresos de actividades ordinarias"), g("Ganancia bruta")
    
    # Nuevas variables contables
    ebit = g("Ganancias (pérdidas) de actividades operacionales")
    ebt = g("Ganancia (pérdida), antes de impuestos")
    cf_val = g("Costos financieros")
    cf = abs(cf_val) if cf_val is not None else None
    
    deprec_val = g("Gasto por depreciación y amortización") or g("Ajustes por gastos de depreciación y amortización") or 0.0
    deprec = abs(deprec_val)
    ebitda = (ebit + deprec) if ebit is not None else None
    
    inventarios = abs(g("Inventarios corrientes") or 0.0)

    pct = lambda x: (x * 100.0) if x is not None else None
    
    # Ratios Dupont
    rotacion_activos = div(ingresos, activos)
    apalancamiento = div(activos, patrim)
    
    # Solvencia / Cobertura
    test_acido = div(ac - inventarios, pc) if ac is not None else None
    capital_trabajo = (ac - pc) if (ac is not None and pc is not None) else None
    cobertura_intereses = div(ebit, cf)
    
    # Márgenes operativos
    margen_ebit = pct(div(ebit, ingresos))
    margen_ebitda = pct(div(ebitda, ingresos))

    return {
        "currency": str(df.iloc[0]["currency"]),
        "roe": pct(div(ganancia, patrim)),
        "roa": pct(div(ganancia, activos)),
        "margen_neto": pct(div(ganancia, ingresos)),
        "margen_bruto": pct(div(gbruta, ingresos)),
        "liquidez": div(ac, pc),
        "endeudamiento": div(pasivos, patrim),
        "pasivo_activo": pct(div(pasivos, activos)),
        
        # Dupont
        "rotacion_activos": rotacion_activos,
        "apalancamiento": apalancamiento,
        
        # Nuevos ratios analíticos
        "ebit": ebit,
        "ebitda": ebitda,
        "test_acido": test_acido,
        "capital_trabajo": capital_trabajo,
        "cobertura_intereses": cobertura_intereses,
        "margen_ebit": margen_ebit,
        "margen_ebitda": margen_ebitda,
    }


class Database:
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

    def get_cmf_companies(self, agf: Optional[bool] = None):
        """Empresas (RUT + Razón Social) de los EEFF corporativos.
        `agf`: None=todas · False=excluye AGF · True=solo AGF (ver AGF_SQL_PREDICATE)."""
        return self.conn.execute(f"""
            SELECT DISTINCT rut, company_name
            FROM cmf_financial_statements
            {_agf_where(agf)}
            ORDER BY company_name ASC
        """).fetchdf()

    def search_cmf_companies(self, query: str, limit: int = 15, agf: Optional[bool] = None):
        """
        Busca empresas por nombre para el autocompletar del selector EEFF.
        Rankea prefijo > substring y deduplica por RUT quedándose con el nombre más
        reciente (mayor `period`) de cada empresa. Devuelve DataFrame (rut, company_name).
        `agf`: None=todas · False=excluye AGF · True=solo AGF.
        """
        q = (query or "").strip().lower()
        if len(q) < 2:
            # Devuelve estructura vacía con las columnas esperadas.
            return self.conn.execute(
                "SELECT rut, company_name FROM cmf_financial_statements WHERE 1=0"
            ).fetchdf()
        like_sub = f"%{q}%"
        like_pre = f"{q}%"
        return self.conn.execute(f"""
            WITH matches AS (
                SELECT rut, company_name, period,
                       ROW_NUMBER() OVER (PARTITION BY rut ORDER BY period DESC) AS rn
                FROM cmf_financial_statements
                WHERE LOWER(company_name) LIKE ?{_agf_where(agf, 'AND')}
            )
            SELECT rut, company_name
            FROM matches
            WHERE rn = 1
            ORDER BY CASE WHEN LOWER(company_name) LIKE ? THEN 0 ELSE 1 END ASC,
                     company_name ASC
            LIMIT ?
        """, [like_sub, like_pre, limit]).fetchdf()

    def get_cmf_periods(self) -> list[int]:
        """Lista de períodos (YYYYMM) disponibles en EEFF corporativos, más reciente primero."""
        rows = self.conn.execute("""
            SELECT DISTINCT period FROM cmf_financial_statements ORDER BY period DESC
        """).fetchall()
        return [int(r[0]) for r in rows]

    def get_bank_list(self):
        """Bancos con datos (código + nombre), ordenados por nombre."""
        return self.conn.execute("""
            SELECT bank_code, MAX(bank_name) AS bank_name
            FROM cmf_bank_statements
            GROUP BY bank_code
            ORDER BY bank_name ASC
        """).fetchdf()

    def get_bank_periods(self, bank_code: Optional[str] = None) -> list[int]:
        """Períodos (YYYYMM) disponibles en estados bancarios, más reciente primero.
        Si se pasa `bank_code`, solo los de ese banco."""
        if bank_code:
            clean = str(bank_code).strip().zfill(3)
            rows = self.conn.execute("""
                SELECT DISTINCT period FROM cmf_bank_statements
                WHERE bank_code = ? ORDER BY period DESC
            """, [clean]).fetchall()
        else:
            rows = self.conn.execute("""
                SELECT DISTINCT period FROM cmf_bank_statements ORDER BY period DESC
            """).fetchall()
        return [int(r[0]) for r in rows]

    def get_afp_list(self) -> list[str]:
        """Nombres de AFP con valores cuota (excluye el agregado TOTAL)."""
        rows = self.conn.execute("""
            SELECT DISTINCT afp_name FROM sp_quota_values
            WHERE afp_name <> 'TOTAL' ORDER BY afp_name ASC
        """).fetchall()
        return [str(r[0]) for r in rows]

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

        # ORDER BY id preserva el orden de ingesta = orden del archivo plano de la CMF,
        # que es el orden oficial de presentación de la taxonomía IFRS (no alfabético).
        query += " ORDER BY id ASC LIMIT ?"
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

    # ----------------------------------------------------------
    # Compañías de Seguros de Vida (FECU CMF)
    # ----------------------------------------------------------

    def insert_insurer_records(self, period: int, freq: str, records: list[dict],
                               insurance_type: str = "vida") -> int:
        """
        Inserta EEFF de seguros con "Delete-then-Insert" atómico por (insurance_type, period, freq).
        Una descarga = todas las compañías del ramo/período, así que el borrado es por esa clave.
        Retorna la cantidad de registros insertados.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec.get('insurance_type', insurance_type)).strip(),
                int(rec['year']), int(rec['month']), int(rec['period']),
                str(rec['report_freq']).strip(),
                str(rec['rut']).strip(),
                str(rec['company_name']).strip(),
                str(rec['statement_group']).strip(),
                str(rec['account_code']).strip(),
                str(rec['account_name']).strip(),
                float(rec['value']) if rec.get('value') is not None else None,
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM cmf_insurer_statements "
                "WHERE insurance_type = ? AND period = ? AND report_freq = ?",
                [str(insurance_type), int(period), str(freq)],
            )
            self.conn.executemany("""
                INSERT INTO cmf_insurer_statements
                    (insurance_type, year, month, period, report_freq, rut, company_name,
                     statement_group, account_code, account_name, value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)
            self.conn.execute("COMMIT")
            logger.info(f"Ingestados {len(records)} registros de seguros {insurance_type} ({freq}) período {period}.")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error ingestando seguros {insurance_type} período {period} ({freq}). Revertido.", exc_info=True)
            raise e

    def get_insurer_list(self, insurance_type: Optional[str] = None):
        """Compañías de seguros con datos (RUT + nombre más reciente), ordenadas por nombre.
        Filtra por ramo si se pasa `insurance_type` ('vida' | 'generales')."""
        where = "WHERE insurance_type = ?" if insurance_type else ""
        params = [insurance_type] if insurance_type else []
        return self.conn.execute(f"""
            SELECT rut, company_name, insurance_type FROM (
                SELECT rut, company_name, insurance_type,
                       ROW_NUMBER() OVER (PARTITION BY rut ORDER BY period DESC) AS rn
                FROM cmf_insurer_statements {where}
            ) WHERE rn = 1
            ORDER BY company_name ASC
        """, params).fetchdf()

    def get_insurer_periods(self, freq: str = "trimestral",
                            insurance_type: Optional[str] = None) -> list[int]:
        """Períodos (YYYYMM) disponibles para seguros, más reciente primero."""
        clause = "WHERE report_freq = ?"
        params = [freq]
        if insurance_type:
            clause += " AND insurance_type = ?"
            params.append(insurance_type)
        rows = self.conn.execute(f"""
            SELECT DISTINCT period FROM cmf_insurer_statements {clause} ORDER BY period DESC
        """, params).fetchall()
        return [int(r[0]) for r in rows]

    def get_latest_insurer_period(self, freq: str = "trimestral",
                                  insurance_type: str = "vida") -> Optional[int]:
        """Último período (YYYYMM) cargado para un ramo de seguros (para la sonda de frescura)."""
        row = self.conn.execute("""
            SELECT MAX(period) FROM cmf_insurer_statements
            WHERE report_freq = ? AND insurance_type = ?
        """, [freq, insurance_type]).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    # ----------------------------------------------------------
    # Intermediarios de valores: corredores de bolsa y agentes (FECU IFRS CMF)
    # ----------------------------------------------------------

    def insert_broker_records(self, period: int, records: list[dict]) -> int:
        """
        Inserta EEFF de intermediarios con "Delete-then-Insert" atómico por período.
        Una descarga = corredores + agentes del período, así que el borrado es por `period`.
        """
        if not records:
            return 0

        tuples_data = [
            (
                str(rec['broker_type']).strip(),
                int(rec['year']), int(rec['month']), int(rec['period']),
                str(rec['rut']).strip(),
                str(rec['company_name']).strip(),
                str(rec['statement_group']).strip(),
                str(rec.get('section') or '').strip() or None,
                str(rec['account_code']).strip(),
                str(rec['account_name']).strip(),
                float(rec['value']) if rec.get('value') is not None else None,
            )
            for rec in records
        ]

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM cmf_broker_statements WHERE period = ?", [int(period)])
            self.conn.executemany("""
                INSERT INTO cmf_broker_statements
                    (broker_type, year, month, period, rut, company_name,
                     statement_group, section, account_code, account_name, value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, tuples_data)
            self.conn.execute("COMMIT")
            logger.info(f"Ingestados {len(records)} registros de intermediarios para el período {period}.")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error ingestando intermediarios período {period}. Revertido.", exc_info=True)
            raise e

    def get_broker_list(self, broker_type: Optional[str] = None):
        """Intermediarios con datos (RUT + nombre más reciente + tipo), ordenados por nombre.
        `broker_type`: 'CORREDORES' | 'AGENTES' | None (todos)."""
        where = "WHERE broker_type = ?" if broker_type else ""
        params = [broker_type] if broker_type else []
        return self.conn.execute(f"""
            SELECT rut, company_name, broker_type FROM (
                SELECT rut, company_name, broker_type,
                       ROW_NUMBER() OVER (PARTITION BY rut ORDER BY period DESC) AS rn
                FROM cmf_broker_statements {where}
            ) WHERE rn = 1
            ORDER BY company_name ASC
        """, params).fetchdf()

    def _fecu_evolution(self, sql: str, params: list, periods: list[int]) -> dict:
        """
        Helper común para las matrices cuentas × períodos de las FECU (seguros e
        intermediarios). `sql` debe devolver (period, account_code, account_name, section, value)
        ORDENADO por `id` — que preserva el orden original de columnas del Excel de la CMF
        (mismo criterio que el orden IFRS de los EEFF corporativos: NO alfabético).
        Devuelve {"periods": [asc], "accounts": [{account_name, section, vals[], is_total}]}.
        """
        import pandas as _pd

        if not periods:
            return {"periods": [], "accounts": []}
        df = self.conn.execute(sql, params).fetchdf()
        if df.empty:
            return {"periods": [], "accounts": []}

        def _text(v):
            """Texto o None. `pd.isna` evita el 'boolean value of NA is ambiguous'
            cuando la columna viene toda nula (ej. `section` en seguros)."""
            if v is None or _pd.isna(v):
                return None
            s = str(v).strip()
            return s or None

        order: list[str] = []                      # códigos en orden de aparición (= orden FECU)
        pivot: dict[str, dict[int, float]] = {}
        meta: dict[str, tuple[str, str]] = {}      # código -> (glosa, sección)
        for _, r in df.iterrows():
            code = str(r["account_code"])
            if code not in pivot:
                pivot[code] = {}
                order.append(code)
                meta[code] = (str(r["account_name"]), _text(r["section"]))
            v = r["value"]
            if v is not None and not _pd.isna(v):
                pivot[code][int(r["period"])] = float(v)

        ordered_periods = sorted({int(p) for p in periods})
        accounts = []
        for code in order:
            name, section = meta[code]
            accounts.append({
                "account_name": name,
                "section": section,
                "vals": [pivot[code].get(p) for p in ordered_periods],
                "is_total": is_total_account(name),
            })
        return {"periods": ordered_periods, "accounts": accounts}

    def get_insurer_evolution(self, rut: str, periods: list[int], statement_group: str,
                              insurance_type: str = "vida", freq: str = "trimestral") -> dict:
        """Matriz cuentas × períodos de UN estado de una compañía de seguros."""
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": []}
        ph = ",".join(["?"] * len(periods))
        sql = f"""
            SELECT period, account_code, account_name, NULL AS section, value, id
            FROM cmf_insurer_statements
            WHERE rut = ? AND insurance_type = ? AND report_freq = ?
              AND statement_group = ? AND period IN ({ph})
            ORDER BY id ASC
        """
        return self._fecu_evolution(
            sql, [str(rut), insurance_type, freq, statement_group] + periods, periods)

    def get_broker_evolution(self, rut: str, periods: list[int], statement_group: str) -> dict:
        """Matriz cuentas × períodos de UN estado de un intermediario de valores.
        Conserva `section` (las 14 secciones del plan) para agrupar dentro del estado."""
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": []}
        ph = ",".join(["?"] * len(periods))
        sql = f"""
            SELECT period, account_code, account_name, section, value, id
            FROM cmf_broker_statements
            WHERE rut = ? AND statement_group = ? AND period IN ({ph})
            ORDER BY id ASC
        """
        return self._fecu_evolution(sql, [str(rut), statement_group] + periods, periods)

    # ----------------------------------------------------------
    # Cartera de inversiones de seguros (Circular 1.835, archivo B.8 Control)
    # ----------------------------------------------------------

    _PF_AMOUNTS = ["valor_final", "repr_rt_pr", "no_repr_rt_pr", "costo_amortizado",
                   "valor_razonable", "efectivo_equiv", "cui_apv", "otras_clasif",
                   "soc_filiales", "coligadas"]

    def insert_insurer_portfolio_control(self, period: int, insurance_type: str,
                                         records: list[dict]) -> int:
        """Inserta la cartera (control) con Delete-then-Insert por (insurance_type, period)."""
        if not records:
            return 0
        cols = ["insurance_type", "period", "rut", "company_name", "investment_code"] + self._PF_AMOUNTS
        tuples_data = [
            tuple([str(r["insurance_type"]), int(r["period"]), str(r["rut"]).strip(),
                   str(r["company_name"]).strip(), str(r["investment_code"]).strip()]
                  + [(float(r[a]) if r.get(a) is not None else None) for a in self._PF_AMOUNTS])
            for r in records
        ]
        ph = ", ".join(["?"] * len(cols))
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM cmf_insurer_portfolio_control "
                "WHERE insurance_type = ? AND period = ?",
                [str(insurance_type), int(period)],
            )
            self.conn.executemany(
                f"INSERT INTO cmf_insurer_portfolio_control ({', '.join(cols)}) VALUES ({ph})",
                tuples_data,
            )
            self.conn.execute("COMMIT")
            logger.info(f"Ingestadas {len(records)} filas de cartera de seguros "
                        f"({insurance_type} {period}).")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error ingestando cartera de seguros {insurance_type} {period}. Revertido.",
                         exc_info=True)
            raise e

    _FI_COLS = ["insurance_type", "period", "rut", "codigo_operacion", "folio_operacion",
                "item_operacion", "fecha_compra", "fecha_pago", "rut_emisor",
                "tipo_instrumento", "nemotecnico", "fecha_emision", "num_inscripcion",
                "fecha_inscripcion", "serie", "pais", "valor_nominal", "valor_nominal_vig",
                "unidad_monetaria"]

    def insert_insurer_portfolio_fixed_income(self, period: int, insurance_type: str,
                                              records: list[dict]) -> int:
        """Inserta el detalle de renta fija (B.1) con Delete-then-Insert por (tipo, período)."""
        if not records:
            return 0
        tuples_data = [tuple(r.get(c) for c in self._FI_COLS) for r in records]
        ph = ", ".join(["?"] * len(self._FI_COLS))
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "DELETE FROM cmf_insurer_portfolio_fixed_income "
                "WHERE insurance_type = ? AND period = ?",
                [str(insurance_type), int(period)],
            )
            self.conn.executemany(
                f"INSERT INTO cmf_insurer_portfolio_fixed_income "
                f"({', '.join(self._FI_COLS)}) VALUES ({ph})",
                tuples_data,
            )
            self.conn.execute("COMMIT")
            logger.info(f"Ingestados {len(records)} instrumentos de renta fija "
                        f"({insurance_type} {period}).")
            return len(records)
        except Exception as e:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error ingestando renta fija {insurance_type} {period}. Revertido.",
                         exc_info=True)
            raise e

    def get_insurer_portfolio_periods(self, insurance_type: Optional[str] = None) -> list[int]:
        """Períodos (YYYYMM) con cartera de inversiones cargada."""
        clause = "WHERE insurance_type = ?" if insurance_type else ""
        params = [insurance_type] if insurance_type else []
        rows = self.conn.execute(
            f"SELECT DISTINCT period FROM cmf_insurer_portfolio_control {clause} "
            f"ORDER BY period DESC", params).fetchall()
        return [int(r[0]) for r in rows]

    # --- Asset allocation de seguros (vistas de inversión institucional) ---
    #
    # UNIDADES: todos los montos vienen en miles de pesos (M$) según la Circular 1.835. Las
    # vistas los presentan en MMM$ (miles de millones de CLP) dividiendo por 1e6.
    #
    # Identidad verificada sobre los datos: valor_final = repr_rt_pr + no_repr_rt_pr (exacta).
    # En cambio costo_amortizado + valor_razonable + efectivo_equiv + otras_clasif NO suma
    # valor_final: los créditos, siniestros por cobrar y avances a tenedores no llevan método
    # de valorización IFRS. Por eso el mix de valorización se calcula sobre lo CLASIFICADO,
    # nunca sobre el total.

    def _insurer_pf_filter(self, period: int, insurance_type: str, rut: Optional[str]):
        where = "WHERE period = ? AND insurance_type = ?"
        params: list = [int(period), str(insurance_type)]
        if rut:
            where += " AND rut = ?"
            params.append(str(rut))
        return where, params

    def get_insurer_allocation(self, period: int, insurance_type: str = "vida",
                               rut: Optional[str] = None):
        """
        Cartera de un período agregada por código de inversión (todas las compañías del ramo,
        o una sola si se pasa `rut`). Devuelve DataFrame con el valor final y sus aperturas:
        representativas/no representativas de reservas técnicas, método de valorización y
        CUI/APV. La traducción del código a clase de activo la hace `api.insurer_glossary`.
        """
        where, params = self._insurer_pf_filter(period, insurance_type, rut)
        return self.conn.execute(f"""
            SELECT investment_code,
                   SUM(valor_final)      AS valor_final,
                   SUM(repr_rt_pr)       AS repr_rt_pr,
                   SUM(no_repr_rt_pr)    AS no_repr_rt_pr,
                   SUM(costo_amortizado) AS costo_amortizado,
                   SUM(valor_razonable)  AS valor_razonable,
                   SUM(efectivo_equiv)   AS efectivo_equiv,
                   SUM(otras_clasif)     AS otras_clasif,
                   SUM(cui_apv)          AS cui_apv
            FROM cmf_insurer_portfolio_control
            {where}
            GROUP BY investment_code
            ORDER BY valor_final DESC NULLS LAST
        """, params).fetchdf()

    def get_insurer_portfolio_companies(self, period: int, insurance_type: str = "vida"):
        """Compañías con cartera en el período, de mayor a menor tamaño (RUT, nombre, total)."""
        return self.conn.execute("""
            SELECT rut,
                   MAX(company_name)  AS company_name,
                   SUM(valor_final)   AS total
            FROM cmf_insurer_portfolio_control
            WHERE period = ? AND insurance_type = ?
            GROUP BY rut
            ORDER BY total DESC NULLS LAST
        """, [int(period), str(insurance_type)]).fetchdf()

    def get_insurer_allocation_series(self, insurance_type: str = "vida",
                                      rut: Optional[str] = None, since: Optional[int] = None):
        """
        Serie histórica de la cartera por período y código de inversión, para seguir la
        deriva del asset allocation. Devuelve DataFrame [period, investment_code, valor_final].
        """
        where = "WHERE insurance_type = ?"
        params: list = [str(insurance_type)]
        if rut:
            where += " AND rut = ?"
            params.append(str(rut))
        if since:
            where += " AND period >= ?"
            params.append(int(since))
        return self.conn.execute(f"""
            SELECT period, investment_code, SUM(valor_final) AS valor_final
            FROM cmf_insurer_portfolio_control
            {where}
            GROUP BY period, investment_code
            ORDER BY period ASC
        """, params).fetchdf()

    def get_insurer_issuer_exposure(self, period: int, insurance_type: str = "vida",
                                    rut: Optional[str] = None, limit: int = 20):
        """
        Concentración por emisor del detalle de renta fija (archivo B.1).

        CUIDADO CON LAS UNIDADES: `valor_nominal` viene en la unidad del instrumento (UF, $$,
        USD…), así que NO es sumable entre monedas — se devuelve abierto por
        `unidad_monetaria`. Además es valor NOMINAL, no de mercado (los montos de valorización
        del archivo B.1 no están reconciliados), por lo que sirve para ver concentración de
        emisores, no para ponderar la cartera.

        El nombre del emisor se resuelve, cuando se puede, contra los RUT que ya conocemos en
        otras tablas (EEFF corporativos, seguros, corredoras); muchos originadores hipotecarios
        y de leasing no están ahí y quedan solo con RUT.
        """
        where = "WHERE fi.period = ? AND fi.insurance_type = ? AND fi.rut_emisor IS NOT NULL"
        params: list = [int(period), str(insurance_type)]
        if rut:
            where += " AND fi.rut = ?"
            params.append(str(rut))
        return self.conn.execute(f"""
            WITH directorio AS (
                SELECT rut, MAX(company_name) AS nombre FROM (
                    SELECT rut, company_name FROM cmf_financial_statements
                    UNION ALL SELECT rut, company_name FROM cmf_insurer_statements
                    UNION ALL SELECT rut, company_name FROM cmf_broker_statements
                ) WHERE company_name IS NOT NULL AND TRIM(company_name) <> ''
                GROUP BY rut
            ),
            agg AS (
                SELECT fi.rut_emisor,
                       fi.unidad_monetaria,
                       COUNT(*)                AS n_instrumentos,
                       SUM(fi.valor_nominal)   AS nominal
                FROM cmf_insurer_portfolio_fixed_income fi
                {where}
                GROUP BY fi.rut_emisor, fi.unidad_monetaria
            ),
            ranked AS (
                SELECT rut_emisor, SUM(n_instrumentos) AS total_instr
                FROM agg GROUP BY rut_emisor
                ORDER BY total_instr DESC LIMIT ?
            )
            SELECT a.rut_emisor, d.nombre, a.unidad_monetaria,
                   a.n_instrumentos, a.nominal, r.total_instr
            FROM agg a
            JOIN ranked r ON r.rut_emisor = a.rut_emisor
            LEFT JOIN directorio d ON d.rut = a.rut_emisor
            ORDER BY r.total_instr DESC, a.n_instrumentos DESC
        """, params + [int(limit)]).fetchdf()

    # --- Series por cuenta para proyecciones (índice YYYYMM → valor) ---

    def get_bank_total_series(self, bank_code: str, account_name: str, report_type: str):
        """
        Serie mensual del TOTAL consolidado (`val_total`) de una cuenta bancaria — la
        "serie de EEFF total", sin el desglose por moneda.

        EMPALMA los dos planes de cuentas igual que `get_bank_statement_evolution`: la CMF
        cambió el plan en 2022 (los nombres pasaron de 'ACTIVOS' a 'TOTAL ACTIVOS' y las
        UNIDADES de millones de pesos a pesos). Sin el empalme la serie queda partida en
        2022 y con unidades mezcladas, lo que rompe cualquier proyección.
        """
        import unicodedata
        import pandas as _pd

        def norm(name: str) -> str:
            s = unicodedata.normalize("NFKD", str(name))
            s = "".join(c for c in s if not unicodedata.combining(c)).upper().strip()
            if s.startswith("TOTAL "):
                s = s[6:]
            return " ".join(s.split())

        NEW_PLAN, OLD_PLAN_UNIT = 202201, 1e6
        target = norm(account_name)
        rows = self.conn.execute("""
            SELECT period, account_name, MAX(val_total) AS value
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ?
            GROUP BY period, account_name ORDER BY period
        """, [str(bank_code).strip().zfill(3), report_type]).fetchall()

        out: dict[int, float] = {}
        for period, name, value in rows:
            if value is None or norm(name) != target:
                continue
            p = int(period)
            v = float(value) * (OLD_PLAN_UNIT if p < NEW_PLAN else 1.0)
            out[p] = v
        return _pd.Series(dict(sorted(out.items())))

    def get_insurer_account_series(self, rut: str, account_code: str,
                                   insurance_type: str = "vida", freq: str = "trimestral"):
        """Serie trimestral de una cuenta de una compañía de seguros."""
        import pandas as _pd
        rows = self.conn.execute("""
            SELECT period, MAX(value) AS value
            FROM cmf_insurer_statements
            WHERE rut = ? AND insurance_type = ? AND report_freq = ? AND account_code = ?
            GROUP BY period ORDER BY period
        """, [str(rut), insurance_type, freq, account_code]).fetchall()
        return _pd.Series({int(r[0]): float(r[1]) for r in rows if r[1] is not None})

    def get_broker_account_series(self, rut: str, account_code: str):
        """Serie trimestral de una cuenta de un intermediario.
        El GROUP BY deduplica: la FECU repite 'Utilidad (pérdida) del ejercicio' en dos
        secciones (cierra 'Otros resultados' y abre el estado de otros resultados
        integrales) con el mismo valor."""
        import pandas as _pd
        rows = self.conn.execute("""
            SELECT period, MAX(value) AS value
            FROM cmf_broker_statements
            WHERE rut = ? AND account_code = ?
            GROUP BY period ORDER BY period
        """, [str(rut), account_code]).fetchall()
        return _pd.Series({int(r[0]): float(r[1]) for r in rows if r[1] is not None})

    def get_broker_periods(self) -> list[int]:
        """Períodos (YYYYMM) disponibles para intermediarios, más reciente primero."""
        rows = self.conn.execute("""
            SELECT DISTINCT period FROM cmf_broker_statements ORDER BY period DESC
        """).fetchall()
        return [int(r[0]) for r in rows]

    def get_latest_broker_period(self) -> Optional[int]:
        """Último período cargado de intermediarios (para la sonda de frescura)."""
        row = self.conn.execute("SELECT MAX(period) FROM cmf_broker_statements").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def query_bank_statements(
        self,
        bank_code: Optional[str] = None,
        period: Optional[int] = None,
        periods: Optional[list[int]] = None,
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
        elif periods:
            placeholders = ",".join("?" for _ in periods)
            query += f" AND period IN ({placeholders})"
            params.extend(periods)
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
                int(rec['row_order']) if rec.get('row_order') is not None else None,
                str(rec['section']).strip() if rec.get('section') is not None else None,
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
                INSERT INTO sp_portfolio_holdings (period, afp_name, fund_type, instrument_glosa, row_order, section, monto_pesos, monto_dolares, porcentaje)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def query_sp_portfolio_holdings(
        self,
        period: Optional[str] = None,
        afp: Optional[str] = None,
        fund: Optional[str] = None,
        limit: int = 50,
    ):
        """Consulta la cartera mensual desagregada de la SP con filtros flexibles."""
        query = """
            SELECT period, afp_name, fund_type, instrument_glosa,
                   monto_pesos, monto_dolares, porcentaje
            FROM sp_portfolio_holdings WHERE 1=1
        """
        params = []
        if period:
            query += " AND period = ?"
            params.append(period)
        if afp:
            query += " AND UPPER(afp_name) = ?"
            params.append(afp.upper().strip())
        if fund:
            query += " AND UPPER(fund_type) = ?"
            params.append(fund.upper().strip())

        query += " ORDER BY porcentaje DESC NULLS LAST LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchdf()

    # IDs de catálogo BCCh usados por el panel multi-fuente.
    KPI_SERIES = {
        "uf": "F073.UFF.PRE.Z.D",
        "usd_clp": "F073.TCO.PRE.Z.D",
        "ipc_v12": "G073.IPC.V12.2023.M",
        "tpm": "F022.TPM.TIN.D001.NO.Z.D",
        "pib": "F032.PIB.FLU.R.CLP.EP18.Z.Z.0.T",
        "imacec": "F032.ICF.IND.Z.Z.EP18.Z.Z.0.M",
        "imacec_minero": "F032.IMC.IND.Z.Z.EP18.03.Z.0.M",
        "imacec_no_minero": "F032.IMC.IND.Z.Z.EP18.N03.Z.0.M",
        "bcp_5y": "F022.BCLP.TIS.AN05.NO.Z.D",
        "bcp_10y": "F022.BCLP.TIS.AN10.NO.Z.D",
        "bcu_5y": "F022.BUF.TIS.AN05.UF.Z.D",
        "bcu_10y": "F022.BUF.TIS.AN10.UF.Z.D",
        "rate_com": "F022.COM.TIP.Z.NO.Z.M",
        "rate_con": "F022.CON.TIP.Z.NO.Z.M",
        "rate_viv": "F022.VIV.TIP.MA03.UF.Z.M",
    }
    # Códigos de cuenta bancaria usados en los rankings.
    BANK_ASSETS_ACCOUNT = "100000000"      # Total activos (balance)
    BANK_RESULT_ACCOUNT = "590000000"      # Utilidad (pérdida) del ejercicio (resultado)

    def _yoy_pct(self, series_id: str) -> Optional[float]:
        """Variación interanual (%) del último dato vs el mismo período del año anterior."""
        latest = self.conn.execute(
            "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 1",
            [series_id]
        ).fetchone()
        if not latest or latest[1] is None:
            return None
        d = latest[0]
        if isinstance(d, datetime):
            d = d.date()
        try:
            prior = d.replace(year=d.year - 1)
        except ValueError:
            prior = d - __import__("datetime").timedelta(days=365)
        old = self.conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            [series_id, str(prior)]
        ).fetchone()
        if not old or not old[0]:
            return None
        return (latest[1] / old[0] - 1) * 100.0

    def _top_banks(self, account_code: str, report_type: str, n: int = 5) -> list[dict]:
        """Top-n bancos por val_total de una cuenta, en el último período del reporte."""
        return self.conn.execute("""
            SELECT bank_name, val_total
            FROM cmf_bank_statements
            WHERE account_code = ? AND report_type = ?
              AND period = (SELECT MAX(period) FROM cmf_bank_statements
                            WHERE account_code = ? AND report_type = ?)
            ORDER BY val_total DESC
            LIMIT ?
        """, [account_code, report_type, account_code, report_type, n]).fetchdf().to_dict(orient="records")

    def get_fund_returns_12m(self) -> dict:
        """
        Rentabilidad nominal a 12 meses por multifondo (A–E).
        Por cada fondo devuelve {agg, leader_afp, leader_ret}:
          - agg: rentabilidad agregada del sistema (ponderada por patrimonio).
          - leader_afp / leader_ret: la AFP nº1 del fondo y su rentabilidad.
        return individual = cuota_hoy / cuota_~12m_atrás - 1.
        """
        import datetime as _dt
        import pandas as _pd
        out = {}
        for f in ("A", "B", "C", "D", "E"):
            rows = self.conn.execute("""
                SELECT q.afp_name, q.date, q.quota_value, q.equity_value
                FROM sp_quota_values q
                JOIN (SELECT afp_name, MAX(date) md FROM sp_quota_values
                      WHERE fund_type = ? AND afp_name <> 'TOTAL' GROUP BY afp_name) l
                  ON q.afp_name = l.afp_name AND q.date = l.md
                WHERE q.fund_type = ?
            """, [f, f]).fetchdf()

            num = den = 0.0
            per_afp = []  # (afp_name, ret_pct)
            for _, r in rows.iterrows():
                d = _pd.Timestamp(r["date"]).date()
                target = d - _dt.timedelta(days=365)
                old = self.conn.execute("""
                    SELECT quota_value FROM sp_quota_values
                    WHERE afp_name = ? AND fund_type = ? AND date <= ?
                    ORDER BY date DESC LIMIT 1
                """, [r["afp_name"], f, str(target)]).fetchone()
                if old and old[0] and r["quota_value"]:
                    ret = r["quota_value"] / old[0] - 1.0
                    w = float(r["equity_value"] or 0.0)
                    per_afp.append((str(r["afp_name"]), ret * 100.0))
                    num += ret * w
                    den += w

            leader = max(per_afp, key=lambda x: x[1]) if per_afp else (None, None)
            out[f] = {
                "agg": (num / den * 100.0) if den else None,
                "leader_afp": leader[0],
                "leader_ret": leader[1],
            }
        return out

    def get_fund_equity_rows(self, fund: str, from_date: str):
        """Patrimonio por AFP y fecha para un fondo (para participación de mercado)."""
        return self.conn.execute("""
            SELECT date, afp_name, equity_value
            FROM sp_quota_values
            WHERE fund_type = ? AND afp_name <> 'TOTAL' AND date >= ?
            ORDER BY date ASC
        """, [fund, from_date]).fetchdf()

    def get_portfolio_composition(self, period: str, fund: str, afp: str = "TOTAL", limit: int = 12):
        """
        Composición de cartera de un fondo por CATEGORÍA legible (filas "TOTAL ..." que
        agrupan los instrumentos y suman ~100%), excluyendo el gran total "TOTAL ACTIVOS".
        Evita los códigos crípticos de instrumento (CMEV, ETFA, ...).
        Devuelve DataFrame [instrument_glosa, porcentaje, monto_pesos].
        """
        return self.conn.execute("""
            SELECT instrument_glosa, porcentaje, monto_pesos
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND UPPER(afp_name) = ?
              AND porcentaje IS NOT NULL AND porcentaje < 100
              AND UPPER(instrument_glosa) LIKE 'TOTAL %'
              AND UPPER(instrument_glosa) NOT LIKE 'TOTAL ACTIVOS%'
            ORDER BY porcentaje DESC
            LIMIT ?
        """, [period, fund, afp.upper(), limit]).fetchdf()

    def get_foreign_portfolio(self, period: str, fund: str, afp: str = "TOTAL", limit: int = 15):
        """
        Detalle de la cartera extranjera: los instrumentos individuales que cuelgan
        de la sección 'TOTAL EXTRANJERO' del informe SP. La sección se resuelve por
        posición en la ingesta (columna `section`), porque los códigos de instrumento
        son ambiguos: el MISMO código (p. ej. CFID(6), CFIV(6)) aparece tanto en
        secciones nacionales como en la extranjera. Devuelve DataFrame
        [instrument_glosa, porcentaje, monto_pesos].
        """
        return self.conn.execute("""
            SELECT instrument_glosa, porcentaje, monto_pesos
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND UPPER(afp_name) = ?
              AND porcentaje IS NOT NULL AND porcentaje > 0
              AND section = 'TOTAL EXTRANJERO'
              AND instrument_glosa NOT LIKE 'TOTAL%'
            ORDER BY porcentaje DESC
            LIMIT ?
        """, [period, fund, afp.upper(), limit]).fetchdf()

    def get_portfolio_periods(self) -> list[str]:
        """Períodos disponibles en la cartera de inversión SP."""
        rows = self.conn.execute(
            "SELECT DISTINCT period FROM sp_portfolio_holdings ORDER BY period DESC"
        ).fetchall()
        return [str(r[0]) for r in rows]

    def get_sp_fund_types(self) -> list[str]:
        """Tipos de fondo con cuotas (A-E hoy; los generacionales aparecerán solos)."""
        rows = self.conn.execute(
            "SELECT DISTINCT fund_type FROM sp_quota_values ORDER BY fund_type"
        ).fetchall()
        return [str(r[0]) for r in rows]

    # Horizontes de retorno: (etiqueta, días aprox., anualizar)
    FUND_HORIZONS = [("1M", 30, False), ("3M", 91, False), ("6M", 182, False),
                     ("12M", 365, False), ("3A", 3 * 365, True),
                     ("5A", 5 * 365, True), ("10A", 10 * 365, True)]

    def get_fund_horizon_returns(self, fund: str) -> dict:
        """
        Retornos nominales del valor cuota por AFP para un fondo, en varios horizontes.
        <=12M: retorno acumulado; >12M: anualizado (marcado en la etiqueta). La cuota
        base es la última disponible a la fecha objetivo (tolerancia 20 días; si la AFP
        no existía, el horizonte queda en None — ej. UNO antes de 2019).
        Devuelve {"horizons": [labels], "afps": [{afp, values: [...]}], "as_of": date}.
        """
        df = self.conn.execute("""
            SELECT afp_name, date, quota_value FROM sp_quota_values
            WHERE fund_type = ? AND afp_name <> 'TOTAL'
            ORDER BY afp_name, date
        """, [fund]).fetchdf()
        if df.empty:
            return {"horizons": [], "afps": [], "as_of": None}
        import pandas as pd

        df["date"] = pd.to_datetime(df["date"])
        as_of = df["date"].max()
        labels = [f"{lab} (anual)" if ann else lab for lab, _, ann in self.FUND_HORIZONS]
        out = []
        for afp, sub in df.groupby("afp_name"):
            sub = sub.set_index("date")["quota_value"].sort_index()
            q_now = float(sub.iloc[-1])
            vals = []
            for _, days, annualize in self.FUND_HORIZONS:
                target = as_of - pd.Timedelta(days=days)
                base = sub.loc[:target]
                if base.empty or (target - base.index[-1]).days > 20:
                    vals.append(None)
                    continue
                r = q_now / float(base.iloc[-1])
                years = days / 365.0
                vals.append(round(((r ** (1 / years)) - 1) * 100, 2) if annualize
                            else round((r - 1) * 100, 2))
            out.append({"afp": str(afp), "values": vals})
        out.sort(key=lambda a: (a["values"][3] is None, -(a["values"][3] or 0)))  # por 12M
        return {"horizons": labels, "afps": out, "as_of": str(as_of.date())}

    def get_fund_composition_by_afp(self, fund: str) -> dict:
        """
        Composición vigente de la cartera de un fondo, por AFP, agregada a las secciones
        del informe SP (totalizadores 'TOTAL <sección>'; se excluyen las filas de control
        en MM$). Devuelve {"period", "sections": [orden], "afps": [{afp, pcts: {sección: %}}]}.
        """
        period = self.conn.execute(
            "SELECT MAX(period) FROM sp_portfolio_holdings").fetchone()[0]
        if period is None:
            return {"period": None, "sections": [], "afps": []}
        df = self.conn.execute("""
            SELECT afp_name, instrument_glosa, porcentaje, row_order
            FROM sp_portfolio_holdings
            WHERE period = ? AND fund_type = ? AND afp_name <> 'TOTAL'
              AND instrument_glosa LIKE 'TOTAL %'
              AND instrument_glosa NOT LIKE '%MM$%' AND instrument_glosa NOT LIKE '%MMU$%'
              AND porcentaje IS NOT NULL
            ORDER BY afp_name, row_order
        """, [str(period), fund]).fetchdf()
        if df.empty:
            return {"period": str(period), "sections": [], "afps": []}

        # Orden de secciones = orden oficial del informe (row_order de la primera AFP)
        sections, seen = [], set()
        for _, r in df.iterrows():
            s = str(r["instrument_glosa"]).replace("TOTAL ", "").title()
            if s not in seen:
                sections.append(s)
                seen.add(s)
        afps = []
        for afp, sub in df.groupby("afp_name"):
            pcts = {str(r["instrument_glosa"]).replace("TOTAL ", "").title():
                    float(r["porcentaje"]) for _, r in sub.iterrows()}
            afps.append({"afp": str(afp), "pcts": pcts})
        return {"period": str(period), "sections": sections, "afps": afps}

    def get_afp_net_simulation(self, fund: str = "C", salary: float = 1_000_000,
                               months: int = 12) -> dict:
        """
        Simulación de rentabilidad NETA de comisiones por AFP para un afiliado con
        sueldo bruto `salary`: cada mes cotiza el 10% al fondo (compra cuotas al
        valor del primer día hábil del mes) y paga además la comisión de su AFP
        (% de la remuneración, desde config/afp_commissions.yaml). Se valoriza el
        saldo a la última cuota disponible y se compara contra el DESEMBOLSO total
        (aportes + comisiones) en los últimos `months` meses corridos completos.

        Devuelve {"rows": [...por AFP...], "window": {desde, hasta, valuacion},
        "vigencia_comisiones": str}. Rentabilidades en % (no anualizadas).
        """
        import yaml
        from pathlib import Path

        cfg = yaml.safe_load(Path("config/afp_commissions.yaml").read_text(encoding="utf-8"))
        commissions = {str(k).upper(): float(v) for k, v in cfg["comisiones"].items()}

        # Cuota del primer día hábil de cada mes + última cuota, por AFP
        df = self.conn.execute("""
            WITH firsts AS (
                SELECT afp_name, STRFTIME(date, '%Y-%m') AS ym, MIN(date) AS d
                FROM sp_quota_values
                WHERE fund_type = ? AND afp_name <> 'TOTAL'
                GROUP BY afp_name, ym
            )
            SELECT f.afp_name, f.ym, f.d AS date, q.quota_value
            FROM firsts f
            JOIN sp_quota_values q
              ON q.afp_name = f.afp_name AND q.date = f.d AND q.fund_type = ?
            ORDER BY f.afp_name, f.ym
        """, [fund, fund]).fetchdf()
        if df.empty:
            return {"rows": [], "window": None, "vigencia_comisiones": cfg.get("vigencia", "")}

        last = self.conn.execute("""
            SELECT afp_name, ARG_MAX(quota_value, date) AS quota, MAX(date) AS date
            FROM sp_quota_values WHERE fund_type = ? AND afp_name <> 'TOTAL'
            GROUP BY afp_name
        """, [fund]).fetchdf().set_index("afp_name")

        aporte = salary * 0.10
        # Meses completos: excluir el mes de la última cuota (aún en curso)
        val_month = str(last["date"].max())[:7]
        rows, window = [], None
        for afp, sub in df.groupby("afp_name"):
            sub = sub[sub["ym"] < val_month].tail(months)
            if len(sub) < months or afp not in last.index:
                continue
            rate = commissions.get(str(afp).upper())
            if rate is None:
                continue
            quotas = float((aporte / sub["quota_value"]).sum())
            last_q = float(last.loc[afp, "quota"])
            first_q = float(sub["quota_value"].iloc[0])
            saldo = quotas * last_q
            aportes = aporte * months
            comisiones = salary * rate / 100.0 * months
            desembolso = aportes + comisiones
            
            # Umbral de fondo inicial para tener rentabilidad neta positiva en 12 meses
            r_fondo = (last_q - first_q) / first_q
            ganancia_aportes = saldo - aportes
            if r_fondo > 0:
                umbral = (comisiones - ganancia_aportes) / r_fondo
                umbral = max(0.0, umbral)
            else:
                umbral = None

            rows.append({
                "afp": str(afp), "comision_pct": rate,
                "aportes": aportes, "comisiones": comisiones, "desembolso": desembolso,
                "saldo": saldo,
                "rent_fondo_pct": (saldo - aportes) / aportes * 100.0,
                "rent_neta_pct": (saldo - desembolso) / desembolso * 100.0,
                "ganancia_neta": saldo - desembolso,
                "umbral_inicial": umbral,
            })
            window = {"desde": str(sub["ym"].iloc[0]), "hasta": str(sub["ym"].iloc[-1]),
                      "valuacion": str(last.loc[afp, "date"])[:10]}

        rows.sort(key=lambda r: r["rent_neta_pct"], reverse=True)
        return {"rows": rows, "window": window,
                "vigencia_comisiones": str(cfg.get("vigencia", ""))}

    def get_afp_equity_ranking(self) -> list[dict]:
        """Ranking de AFP por patrimonio total (suma del último patrimonio de cada fondo)."""
        return self.conn.execute("""
            WITH latest AS (
                SELECT afp_name, fund_type, MAX(date) md
                FROM sp_quota_values WHERE afp_name <> 'TOTAL'
                GROUP BY afp_name, fund_type
            )
            SELECT q.afp_name, SUM(q.equity_value) AS equity
            FROM sp_quota_values q
            JOIN latest l ON q.afp_name = l.afp_name AND q.fund_type = l.fund_type AND q.date = l.md
            GROUP BY q.afp_name
            ORDER BY equity DESC
        """).fetchdf().to_dict(orient="records")

    # ----------------------------------------------------------
    # Series de partidas por empresa (para graficar en /eeff)
    # ----------------------------------------------------------

    def get_company_graphable_accounts(self, rut: str) -> list[str]:
        """
        Cuentas de una empresa que se pueden graficar: presentes en ≥2 períodos y con un
        único valor por período (evita las cuentas repetidas/ambiguas del XBRL plano).
        """
        clean = str(rut).strip().replace(".", "").replace("-", "")
        rows = self.conn.execute("""
            SELECT account_name
            FROM (
                SELECT account_name, period, COUNT(DISTINCT value) dv
                FROM cmf_financial_statements WHERE rut = ?
                GROUP BY account_name, period
            )
            GROUP BY account_name
            HAVING COUNT(DISTINCT period) >= 2 AND MAX(dv) = 1
            ORDER BY account_name
        """, [clean]).fetchall()
        return [str(r[0]) for r in rows]

    def get_company_account_series(self, rut: str, account_name: str):
        """Evolución de una partida por período (un valor por período), como DataFrame."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        return self.conn.execute("""
            SELECT period, MAX(value) AS value
            FROM cmf_financial_statements
            WHERE rut = ? AND account_name = ?
            GROUP BY period
            ORDER BY period ASC
        """, [clean, account_name]).fetchdf()

    def get_company_ratios(self, rut: str, period: int) -> Optional[dict]:
        """Indicadores financieros de una empresa/período (ver compute_ratios)."""
        return compute_ratios(self.query_cmf_statements(rut=rut, period=period, limit=2000))

    def get_ratios_ranking(self, period: int, metric: str = "roe", n: int = 15, sector: str = None) -> list[dict]:
        """
        Ranking de empresas por un ratio en un período. Calcula ratios de todas las empresas
        del período (una sola consulta + cómputo en memoria) y ordena desc.
        Filtra distorsiones: para métricas en % exige |valor| <= 150 y que haya ingresos.
        """
        df = self.query_cmf_statements(period=period, limit=10**9)
        if df.empty:
            return []
        is_pct = metric in ("roe", "roa", "margen_neto", "margen_bruto", "pasivo_activo")
        out = []
        from api.company_profiles import get_profile
        for rut, sub in df.groupby("rut", sort=False):
            cname = str(sub.iloc[0]["company_name"])
            if sector:
                prof = get_profile(rut, cname)
                if prof["macro_sector"] != sector:
                    continue
            r = compute_ratios(sub)
            if not r or r.get(metric) is None:
                continue
            v = r[metric]
            # excluye holdings/fondos sin operación y valores distorsionados
            if is_pct and (abs(v) > 150 or r.get("margen_neto") is None):
                continue
            out.append({"company_name": cname,
                        "rut": str(rut), "value": v, "currency": r["currency"]})
        out.sort(key=lambda x: x["value"], reverse=True)
        return out[:n]

    def get_company_ratios_series(self, rut: str) -> list[dict]:
        """
        Serie de ratios por período de una empresa (para graficar evolución).
        Devuelve [{period, roe, roa, margen_neto, margen_bruto, liquidez, endeudamiento}]
        ordenado por período. Ratios de income son "del período" (acumulado en el año).
        """
        df = self.query_cmf_statements(rut=rut, limit=10**9)
        if df.empty:
            return []
        out = []
        for period, sub in df.groupby("period", sort=False):
            r = compute_ratios(sub)
            if r:
                row = {"period": int(period)}
                row.update({k: v for k, v in r.items() if k != "currency"})
                out.append(row)
        out.sort(key=lambda x: x["period"])
        return out

    def get_company_periods(self, rut: str) -> list[int]:
        """Períodos (YYYYMM) con EEFF de una empresa, más reciente primero."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        rows = self.conn.execute(
            "SELECT DISTINCT period FROM cmf_financial_statements WHERE rut = ? ORDER BY period DESC",
            [clean]
        ).fetchall()
        return [int(r[0]) for r in rows]

    def get_statement_evolution(self, rut: str, periods: list[int], statement_group: str) -> dict:
        """
        Matriz de evolución de UN estado financiero de una empresa a través de varios períodos.
        Devuelve {"periods": [asc], "accounts": [{account_name, values:[por período]}]}.
        Orden de cuentas = posición relativa promedio en el balance para evitar cuentas descolocadas.
        """
        clean = str(rut).strip().replace(".", "").replace("-", "")
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": []}
        ph = ",".join(["?"] * len(periods))
        df = self.conn.execute(f"""
            WITH ranked AS (
                SELECT 
                    period, account_name, value, id,
                    ROW_NUMBER() OVER (PARTITION BY period ORDER BY id ASC) as rn,
                    COUNT(*) OVER (PARTITION BY period) as total
                FROM cmf_financial_statements
                WHERE rut = ? AND statement_group = ? AND period IN ({ph})
            ),
            avg_positions AS (
                SELECT account_name, AVG(rn * 1.0 / total) as avg_pos
                FROM ranked
                GROUP BY account_name
            )
            SELECT r.period, r.account_name, r.value, a.avg_pos
            FROM ranked r
            JOIN avg_positions a ON r.account_name = a.account_name
            ORDER BY a.avg_pos ASC, r.period DESC
        """, [clean, statement_group] + periods).fetchdf()
        if df.empty:
            return {"periods": [], "accounts": []}

        order, seen = [], set()
        for _, r in df.iterrows():
            a = str(r["account_name"])
            if a not in seen:
                order.append(a)
                seen.add(a)

        pivot = {}  # account -> {period: value} (primera ocurrencia por período)
        for _, r in df.iterrows():
            a, p = str(r["account_name"]), int(r["period"])
            pivot.setdefault(a, {})
            if p not in pivot[a]:
                pivot[a][p] = r["value"]

        periods_asc = sorted(set(int(p) for p in df["period"]))
        accounts = [{"account_name": a, "vals": [pivot[a].get(p) for p in periods_asc],
                     "is_total": is_total_account(a)}
                    for a in order]
        return {"periods": periods_asc, "accounts": accounts}

    def get_company_latest_period(self, rut: str) -> Optional[int]:
        """Último período (YYYYMM) con EEFF para una empresa."""
        clean = str(rut).strip().replace(".", "").replace("-", "")
        row = self.conn.execute(
            "SELECT MAX(period) FROM cmf_financial_statements WHERE rut = ?", [clean]
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_bank_statement_evolution(self, bank_code: str, periods: list[int],
                                     report_type: str) -> dict:
        """
        Matriz de evolución del TOTAL (val_total, no por moneda) de un banco a través de
        varios períodos. Devuelve {"periods": [asc], "accounts": [{account_name, vals[por
        período], is_total}], "note"}.

        En 2022 la CMF cambió el plan de cuentas bancario (códigos de 7 → 9 dígitos,
        nombres distintos: 'ACTIVOS' → 'TOTAL ACTIVOS', y UNIDADES: el plan antiguo
        reporta en MILLONES de pesos y el vigente en PESOS — verificado contra cifras
        públicas del Banco de Chile). Para que las series largas no queden partidas,
        las cuentas se EMPALMAN por nombre normalizado (mayúsculas, sin acentos, sin
        prefijo 'TOTAL') y los valores pre-2022 se homologan a pesos (×1.000.000); se
        muestra el nombre moderno. Las cuentas propias de un solo régimen quedan al
        final, sin fusiones forzadas.
        """
        import unicodedata

        def norm(name: str) -> str:
            s = unicodedata.normalize("NFKD", str(name))
            s = "".join(c for c in s if not unicodedata.combining(c)).upper().strip()
            if s.startswith("TOTAL "):
                s = s[6:]
            return " ".join(s.split())

        clean = str(bank_code).strip().zfill(3)
        periods = [int(p) for p in periods]
        if not periods:
            return {"periods": [], "accounts": [], "note": None}
        ph = ",".join(["?"] * len(periods))
        df = self.conn.execute(f"""
            SELECT period, account_code, account_name, val_total
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ? AND period IN ({ph})
            ORDER BY period ASC, account_code ASC
        """, [clean, report_type] + periods).fetchdf()
        if df.empty:
            return {"periods": [], "accounts": [], "note": None}

        NEW_PLAN = 202201       # primer período del plan de cuentas vigente
        OLD_PLAN_UNIT = 1e6     # plan antiguo en millones de pesos → homologar a pesos
        pivot: dict[str, dict[int, float]] = {}   # key -> {period: val en pesos}
        label: dict[str, tuple[int, str]] = {}    # key -> (period, nombre) más reciente
        code: dict[str, tuple[int, str]] = {}     # key -> (period, código) más reciente
        for _, r in df.iterrows():
            k, p = norm(r["account_name"]), int(r["period"])
            pivot.setdefault(k, {})
            if p not in pivot[k]:
                v = r["val_total"]
                pivot[k][p] = v * OLD_PLAN_UNIT if (v is not None and p < NEW_PLAN) else v
            if k not in label or p >= label[k][0]:
                label[k] = (p, str(r["account_name"]))
                code[k] = (p, str(r["account_code"]))

        periods_asc = sorted(set(int(p) for p in df["period"]))
        has_old = any(p < NEW_PLAN for p in periods_asc)
        has_new = any(p >= NEW_PLAN for p in periods_asc)

        # Orden: estructura del plan vigente primero (por código moderno), luego las
        # cuentas que solo existen en el plan antiguo (por su código), al final.
        def sort_key(k):
            in_new = any(p >= NEW_PLAN for p in pivot[k])
            c = code[k][1]
            return (0 if in_new else 1, len(c), c)

        accounts = []
        for k in sorted(pivot, key=sort_key):
            name = label[k][1]
            accounts.append({"account_name": name,
                             "vals": [pivot[k].get(p) for p in periods_asc],
                             "is_total": is_total_account(name)})
        note = ("La CMF cambió el plan de cuentas bancario en 2022: los totales se "
                "empalman por nombre y los valores pre-2022 se homologan de millones a "
                "pesos; el detalle fino puede no ser comparable entre ambos regímenes."
                ) if (has_old and has_new) else None
        return {"periods": periods_asc, "accounts": accounts, "note": note}

    def get_bank_graphable_accounts(self, bank_code: str, report_type: str) -> list[str]:
        """Cuentas de un banco/reporte presentes en ≥2 períodos (para graficar)."""
        clean = str(bank_code).strip().zfill(3)
        rows = self.conn.execute("""
            SELECT account_name
            FROM cmf_bank_statements
            WHERE bank_code = ? AND report_type = ?
            GROUP BY account_name
            HAVING COUNT(DISTINCT period) >= 2
            ORDER BY account_name
        """, [clean, report_type]).fetchall()
        return [str(r[0]) for r in rows]

    def get_bank_account_series(self, bank_code: str, account_name: str, report_type: str):
        """Evolución de una cuenta bancaria (val_total) por período, como DataFrame."""
        clean = str(bank_code).strip().zfill(3)
        return self.conn.execute("""
            SELECT
                period,
                SUM(val_total) AS value,
                SUM(val_clp_no_reaj) AS clp_no_reaj,
                SUM(val_clp_reaj_ipc) AS clp_reaj_ipc,
                SUM(val_clp_reaj_tc) AS clp_reaj_tc,
                SUM(val_extranjera) AS extranjera
            FROM cmf_bank_statements
            WHERE bank_code = ? AND account_name = ? AND report_type = ?
            GROUP BY period
            ORDER BY period ASC
        """, [clean, account_name, report_type]).fetchdf()

    def get_overview_kpis(self) -> dict:
        """Indicadores multi-fuente para el panel. Tolerante a tablas vacías."""
        def latest(sid):
            v = self.get_latest_value(sid)
            return v["value"] if v else None

        macro = {
            "pib_yoy": self._yoy_pct(self.KPI_SERIES["pib"]),
            "imacec_yoy": self._yoy_pct(self.KPI_SERIES["imacec"]),
            "imacec_minero_yoy": self._yoy_pct(self.KPI_SERIES["imacec_minero"]),
            "imacec_no_minero_yoy": self._yoy_pct(self.KPI_SERIES["imacec_no_minero"]),
            "ipc_v12": latest(self.KPI_SERIES["ipc_v12"]),
            "usd_clp": latest(self.KPI_SERIES["usd_clp"]),
        }

        curva_soberana = {
            "bcp_5y": latest(self.KPI_SERIES["bcp_5y"]),
            "bcp_10y": latest(self.KPI_SERIES["bcp_10y"]),
            "bcu_5y": latest(self.KPI_SERIES["bcu_5y"]),
            "bcu_10y": latest(self.KPI_SERIES["bcu_10y"]),
        }

        tasas_colocacion = {
            "comercial": latest(self.KPI_SERIES["rate_com"]),
            "consumo": latest(self.KPI_SERIES["rate_con"]),
            "vivienda": latest(self.KPI_SERIES["rate_viv"]),
        }

        bp = self.conn.execute(
            "SELECT MAX(period) FROM cmf_bank_statements WHERE report_type = 'balance'"
        ).fetchone()
        banca = {
            "period": int(bp[0]) if bp and bp[0] is not None else None,
            "top_assets": self._top_banks(self.BANK_ASSETS_ACCOUNT, "balance", 5),
            "top_results": self._top_banks(self.BANK_RESULT_ACCOUNT, "resultado", 5),
        }

        afp_returns = self.get_fund_returns_12m()
        afp_ranking = self.get_afp_equity_ranking()

        mercado = {"n_instrumentos": 0, "date": None}
        mrow = self.conn.execute("SELECT MAX(date) FROM sp_instrument_prices").fetchone()
        if mrow and mrow[0] is not None:
            last_d = str(mrow[0])
            cnt = self.conn.execute(
                "SELECT COUNT(*) FROM sp_instrument_prices WHERE date = ?", [last_d]
            ).fetchone()[0]
            mercado = {"n_instrumentos": int(cnt), "date": last_d}

        return {"macro": macro, "banca": banca, "afp_returns": afp_returns,
                "afp_ranking": afp_ranking, "mercado": mercado,
                "curva_soberana": curva_soberana, "tasas_colocacion": tasas_colocacion}

    # ----------------------------------------------------------
    # Frescura (catch-up dirigido por publicación)
    # ----------------------------------------------------------

    def get_latest_cmf_period(self) -> Optional[int]:
        """Último período (YYYYMM) ingresado de estados financieros corporativos CMF."""
        row = self.conn.execute("SELECT MAX(period) FROM cmf_financial_statements").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_latest_bank_period(self, bank_code: Optional[str] = None) -> Optional[int]:
        """Último período (YYYYMM) de estados bancarios; global o por banco (zfill 3)."""
        if bank_code:
            code = str(bank_code).strip().zfill(3)
            row = self.conn.execute(
                "SELECT MAX(period) FROM cmf_bank_statements WHERE bank_code = ?", [code]
            ).fetchone()
        else:
            row = self.conn.execute("SELECT MAX(period) FROM cmf_bank_statements").fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_latest_sp_quota_date(self) -> Optional[str]:
        """Última fecha (YYYY-MM-DD) de valores cuota SP."""
        row = self.conn.execute("SELECT MAX(date) FROM sp_quota_values").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def get_latest_sp_portfolio_period(self) -> Optional[str]:
        """Último período (YYYY-MM) de cartera desagregada SP."""
        row = self.conn.execute("SELECT MAX(period) FROM sp_portfolio_holdings").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def get_latest_sp_price_date(self) -> Optional[str]:
        """Última fecha (YYYY-MM-DD) de la cinta de precios SP."""
        row = self.conn.execute("SELECT MAX(date) FROM sp_instrument_prices").fetchone()
        return str(row[0]) if row and row[0] is not None else None

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
