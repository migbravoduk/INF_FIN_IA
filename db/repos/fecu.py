"""
db/repos/fecu.py — FECU de seguros e intermediarios y cartera de inversiones de seguros (Circ. 1.835).

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


def _glosario_fecu() -> dict:
    """Texto original de la CMF → nombre legible (config/fecu_cuentas.yaml). La CMF publica los
    encabezados FECU sin espacios ('Totalactivo'); las cifras se guardan tal cual y solo se cambia
    el texto que se muestra. Se lee una vez por proceso."""
    global _GLOSARIO_FECU
    if _GLOSARIO_FECU is None:
        try:
            import yaml
            from config.settings import config_path
            _GLOSARIO_FECU = yaml.safe_load(config_path("fecu_cuentas.yaml").read_text(encoding="utf-8")) or {}
        except Exception as e:      # sin glosario se muestran los nombres como vienen
            logger.warning(f"Sin glosario de cuentas FECU: {e}")
            _GLOSARIO_FECU = {}
    return _GLOSARIO_FECU


_GLOSARIO_FECU = None


def nombre_cuenta_fecu(texto):
    """Nombre legible de una cuenta o sección FECU; si no está en el glosario, el texto original."""
    if texto is None:
        return None
    return _glosario_fecu().get(str(texto).strip(), texto)


class FecuRepo:

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
            fast_executemany(self.conn, """
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
            fast_executemany(self.conn, """
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
                meta[code] = (nombre_cuenta_fecu(str(r["account_name"])),
                              nombre_cuenta_fecu(_text(r["section"])))
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
            fast_executemany(self.conn, 
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
            fast_executemany(self.conn, 
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

    _DER_COLS = ("insurance_type", "period", "rut", "tipo", "objetivo", "tipo_operacion",
                 "tipo_contrato", "fecha_operacion", "fecha_vencimiento", "rut_contraparte",
                 "contraparte", "nacionalidad", "tipo_contraparte", "moneda_larga", "moneda_corta",
                 "unidades_larga", "unidades_corta", "nocional_larga_m", "nocional_corta_m",
                 "precio", "tasa_larga", "tasa_corta", "valor_razonable_m")

    def insert_insurer_derivatives(self, period: int, insurance_type: str, records: list[dict]) -> int:
        """Derivados de seguros (B.7) con Delete-then-Insert por (tipo, período)."""
        if not records:
            return 0
        ph = ", ".join(["?"] * len(self._DER_COLS))
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute("DELETE FROM cmf_insurer_derivatives WHERE insurance_type = ? AND period = ?",
                              [str(insurance_type), int(period)])
            fast_executemany(self.conn,
                             f"INSERT INTO cmf_insurer_derivatives ({', '.join(self._DER_COLS)}) VALUES ({ph})",
                             [tuple(r.get(c) for c in self._DER_COLS) for r in records])
            self.conn.execute("COMMIT")
            return len(records)
        except Exception:
            self.conn.execute("ROLLBACK")
            logger.error(f"Error ingestando derivados {insurance_type} {period}. Revertido.", exc_info=True)
            raise

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
        otras tablas (EEFF corporativos, seguros, corredoras). Que falte NO es un hueco de
        datos: los que encabezan el ranking son agentes administradores de mutuos hipotecarios
        y originadores de leasing, que no son emisores inscritos y por eso no aparecen en
        ninguna tabla de EEFF. Por eso se devuelve también `tipo_principal`, sin el cual una
        fila sin nombre es ilegible. Cobertura medida (vida, 202606): 183 de 321 emisores.
        """
        where = "WHERE fi.period = ? AND fi.insurance_type = ? AND fi.rut_emisor IS NOT NULL"
        params: list = [int(period), str(insurance_type)]
        if rut:
            where += " AND fi.rut = ?"
            params.append(str(rut))
        return self.conn.execute(f"""
            -- dim_entidad primero (trae además los bancos, curados por bank_code → RUT);
            -- las tablas de EEFF quedan de respaldo si la maestra aún no se construyó.
            WITH directorio AS (
                SELECT rut, arg_min(nombre, prioridad) AS nombre FROM (
                    SELECT rut, nombre_canonico AS nombre, 0 AS prioridad FROM dim_entidad
                    UNION ALL SELECT rut, company_name, 1 FROM cmf_financial_statements
                    UNION ALL SELECT rut, company_name, 1 FROM cmf_insurer_statements
                    UNION ALL SELECT rut, company_name, 1 FROM cmf_broker_statements
                ) WHERE nombre IS NOT NULL AND TRIM(nombre) <> ''
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
            ),
            -- Tipo de instrumento dominante de cada emisor: es lo que vuelve legible una
            -- fila sin razón social (un MHA no tiene "emisor" en sentido corporativo).
            tipos AS (
                SELECT rut_emisor, tipo_instrumento AS tipo_principal, n_tipo, n_tipos_distintos
                FROM (
                    SELECT fi.rut_emisor, fi.tipo_instrumento,
                           COUNT(*) AS n_tipo,
                           COUNT(*) OVER (PARTITION BY fi.rut_emisor) AS n_tipos_distintos,
                           ROW_NUMBER() OVER (PARTITION BY fi.rut_emisor
                                              ORDER BY COUNT(*) DESC) AS rn
                    FROM cmf_insurer_portfolio_fixed_income fi
                    {where}
                    GROUP BY fi.rut_emisor, fi.tipo_instrumento
                ) WHERE rn = 1
            )
            SELECT a.rut_emisor, d.nombre, a.unidad_monetaria,
                   a.n_instrumentos, a.nominal, r.total_instr,
                   t.tipo_principal, t.n_tipo, t.n_tipos_distintos
            FROM agg a
            JOIN ranked r ON r.rut_emisor = a.rut_emisor
            LEFT JOIN directorio d ON d.rut = a.rut_emisor
            LEFT JOIN tipos t ON t.rut_emisor = a.rut_emisor
            ORDER BY r.total_instr DESC, a.n_instrumentos DESC
        """, params + [int(limit)] + params).fetchdf()

    def count_insurer_issuers(self, period: int, insurance_type: str = "vida",
                              rut: Optional[str] = None) -> int:
        """Nº de emisores distintos en la renta fija, para poner el 'top N' en contexto."""
        where = "WHERE period = ? AND insurance_type = ? AND rut_emisor IS NOT NULL"
        params: list = [int(period), str(insurance_type)]
        if rut:
            where += " AND rut = ?"
            params.append(str(rut))
        row = self.conn.execute(
            f"SELECT COUNT(DISTINCT rut_emisor) FROM cmf_insurer_portfolio_fixed_income {where}",
            params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

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

    def get_latest_insurer_portfolio_period(
            self, insurance_type: Optional[str] = None) -> Optional[int]:
        """Último período (YYYYMM) de cartera de inversiones de seguros (Circular 1.835).

        Se mide sobre la tabla de control (B.8): es la que define si un período está
        cargado. La de renta fija (B.1) se ingesta en el mismo paso desde el mismo ZIP.
        """
        clause = "WHERE insurance_type = ?" if insurance_type else ""
        params = [insurance_type] if insurance_type else []
        row = self.conn.execute(
            f"SELECT MAX(period) FROM cmf_insurer_portfolio_control {clause}", params
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None
