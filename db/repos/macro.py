"""
db/repos/macro.py — Series del Banco Central, catálogo macro y bitácora de ingestas (fetch_log).

Mixin de `db.database.Database`: los métodos usan `self.conn` (conexión DuckDB) y se
acceden como siempre, `Database().<método>`. Separado de database.py por dominio (Fase 5
del plan) sin cambiar el código de ningún método.
"""

import logging
import re as _re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from db.common import (is_total_account, AGF_SQL_PREDICATE, _agf_where, is_agf_name,
                       compute_ratios, _TOTAL_RE, fast_executemany)

logger = logging.getLogger("db.database")


class MacroRepo:

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

    # Categorías del catálogo BCCh que NO son dato observado sino encuesta de expectativas
    # (EEE mensual + EOF quincenal). Alimentan la senda macro de los modelos y se muestran
    # aparte: leerlas como historia efectiva sería un error de interpretación.
    EXPECTATIVAS_CATEGORY = "expectativas"

    def get_macro_catalog(self, source_id: str = "bcentral",
                          expectativas: Optional[bool] = None) -> list[dict]:
        """
        Catálogo de series macro con su cobertura real y último dato.

        `expectativas`: None = todas, False = solo dato observado, True = solo encuestas.
        Solo devuelve series CON observaciones: la tabla `series` registra algunas que
        nunca se llegaron a poblar, y listarlas como si tuvieran datos confunde.
        """
        clauses = ["s.source_id = ?"]
        params: list = [source_id]
        if expectativas is True:
            clauses.append("s.category = ?")
            params.append(self.EXPECTATIVAS_CATEGORY)
        elif expectativas is False:
            clauses.append("(s.category IS NULL OR s.category <> ?)")
            params.append(self.EXPECTATIVAS_CATEGORY)

        rows = self.conn.execute(f"""
            WITH agg AS (
                SELECT series_id, COUNT(*) AS n_obs,
                       MIN(date) AS first_date, MAX(date) AS last_date
                FROM observations GROUP BY series_id
            ),
            ult AS (
                SELECT series_id, value FROM (
                    SELECT series_id, value,
                           ROW_NUMBER() OVER (PARTITION BY series_id ORDER BY date DESC) AS rn
                    FROM observations
                ) WHERE rn = 1
            )
            SELECT s.id, s.name, s.category, s.frequency, s.unit, s.description,
                   a.n_obs, a.first_date, a.last_date, u.value AS last_value
            FROM series s
            JOIN agg a ON a.series_id = s.id
            LEFT JOIN ult u ON u.series_id = s.id
            WHERE {' AND '.join(clauses)}
            ORDER BY s.category NULLS LAST, s.name
        """, params).fetchall()

        cols = ["id", "name", "category", "frequency", "unit", "description",
                "n_obs", "first_date", "last_date", "last_value"]
        return [dict(zip(cols, r)) for r in rows]

    def get_series_meta(self, series_id: str) -> Optional[dict]:
        """Metadatos de una serie (nombre, categoría, frecuencia, unidad, descripción)."""
        row = self.conn.execute(
            "SELECT id, name, category, frequency, unit, description FROM series WHERE id = ?",
            [series_id]
        ).fetchone()
        if not row:
            return None
        return dict(zip(["id", "name", "category", "frequency", "unit", "description"], row))

    def get_series_stats(self, series_id: str, from_date: Optional[str] = None) -> dict:
        """
        Estadísticas de una serie en el rango pedido: último dato, variación interanual,
        mínimo y máximo con sus fechas, y nº de observaciones.

        La variación se calcula contra el dato **anterior o igual** a la fecha de hace un
        año, no contra "el de hace N filas": las series diarias tienen huecos por feriados
        y las mensuales pueden faltar meses, así que contar filas daría comparaciones
        desalineadas. Si no hay dato previo suficiente, queda en None en vez de inventarse.

        Se devuelven las DOS variaciones y la vista elige según la unidad:
          · `yoy_pct` (variación %) sirve para niveles — índices, precios, montos.
          · `yoy_abs` (diferencia absoluta = puntos porcentuales) es la correcta cuando la
            serie YA es una tasa o un porcentaje. Decir que la TPM "cayó 5,3%" al pasar de
            4,75 a 4,50 es engañoso: cayó 25 puntos base.
        """
        where = "series_id = ?" + (" AND date >= ?" if from_date else "")
        params = [series_id] + ([from_date] if from_date else [])
        row = self.conn.execute(f"""
            SELECT COUNT(*), MIN(value), MAX(value)
            FROM observations WHERE {where} AND value IS NOT NULL
        """, params).fetchone()
        n_obs, vmin, vmax = (row or (0, None, None))

        out = {"n_obs": int(n_obs or 0), "min": vmin, "max": vmax,
               "last": None, "last_date": None, "yoy_pct": None, "yoy_abs": None}
        if not n_obs:
            return out

        last = self.conn.execute(
            f"SELECT date, value FROM observations WHERE {where} AND value IS NOT NULL "
            f"ORDER BY date DESC LIMIT 1", params).fetchone()
        if not last:
            return out
        out["last_date"], out["last"] = last[0], last[1]

        d = last[0].date() if isinstance(last[0], datetime) else last[0]
        try:
            hace_un_ano = d.replace(year=d.year - 1)
        except ValueError:      # 29 de febrero
            hace_un_ano = d - timedelta(days=365)
        prev = self.conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND date <= ? "
            "AND value IS NOT NULL ORDER BY date DESC LIMIT 1",
            [series_id, str(hace_un_ano)]).fetchone()
        if prev and prev[0] is not None:
            out["yoy_abs"] = last[1] - prev[0]
            if prev[0] != 0:
                out["yoy_pct"] = (last[1] / prev[0] - 1) * 100
        return out

    def get_latest_value(self, series_id: str) -> Optional[dict]:
        """Retorna el valor más reciente de una serie."""
        row = self.conn.execute(
            "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 1",
            [series_id]
        ).fetchone()
        if not row:
            return None
        return {"date": row[0], "value": row[1]}

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
