"""
db/quality.py — Controles de calidad de datos (`main.py validate`).

Cada control devuelve una o más filas (estado, tabla, período, detalle) que se guardan en
`dq_results` con el mismo `run_at`. Estados:
  - ok     : pasó
  - warn   : anomalía a revisar (no bloquea)
  - error  : dato que no debe mostrarse tal cual (duplicados, identidad contable rota…)

Se ejecuta al final de cada catch-up con descargas y a pedido. El panel web muestra el
resumen de la última corrida. Los controles son de SOLO LECTURA sobre las tablas de datos.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Check:
    check_id: str
    tabla: str
    severidad: str          # severidad si falla: 'warn' | 'error'
    sql: str                # devuelve filas (periodo, n_fallas, detalle)
    descripcion: str


def _dup(tabla, llave, desc, where=""):
    """Duplicados de la llave natural, agrupados por período."""
    per = llave.split(",")[0].strip()
    return Check(
        f"unicidad_{tabla}", tabla, "error",
        f"""SELECT CAST({per} AS VARCHAR), COUNT(*), 'llaves repetidas'
            FROM (SELECT {llave} FROM {tabla} {where} GROUP BY {llave} HAVING COUNT(*) > 1)
            GROUP BY 1 ORDER BY 1""",
        desc)


CHECKS: list[Check] = [
    # --- Unicidad de la llave natural ---
    _dup("cmf_bank_statements", "period, bank_code, report_type, account_code",
         "Bancos: una fila por (período, banco, reporte, cuenta)"),
    _dup("cmf_insurer_statements",
         "period, insurance_type, report_freq, rut, statement_group, account_code",
         "Seguros FECU: una fila por (período, ramo, frecuencia, compañía, estado, cuenta)"),
    _dup("cmf_broker_statements", "period, rut, statement_group, section, account_code",
         "Intermediarios: una fila por (período, entidad, estado, sección, cuenta)"),
    _dup("cmf_mutual_fund_quota_values", "date, fund_run, series",
         "FFMM valor cuota: una fila por (fecha, fondo, serie)"),
    _dup("cmf_mutual_fund_fees", "periodo, fund_run, series",
         "FFMM comisiones: una fila por (período, fondo, serie)"),
    _dup("cmf_mutual_fund_returns", "fecha, fund_run, series",
         "FFMM comparador: una fila por (corte, fondo, serie)"),
    _dup("sp_portfolio_holdings", "period, afp_name, fund_type, section, row_order",
         "Cartera AFP: una fila por (período, AFP, fondo, sección, fila del XML)"),
    _dup("cmf_insurer_portfolio_control", "period, insurance_type, rut, investment_code",
         "Cartera seguros B.8: una fila por (período, ramo, compañía, tipo de inversión)"),

    # --- Identidad contable ---
    Check("identidad_eeff", "cmf_financial_statements", "warn", """
        WITH t AS (
          SELECT period, rut, report_type,
                 MIN(value) FILTER (WHERE account_name = 'Total de activos') a,
                 MIN(value) FILTER (WHERE account_name = 'Total de patrimonio y pasivos') pp
          FROM cmf_financial_statements WHERE statement_group = 'ESF C/NC'
          GROUP BY ALL)
        SELECT CAST(period AS VARCHAR), COUNT(*), 'empresas con |A − (P+Pat)| > 1%'
        FROM t WHERE a IS NOT NULL AND pp IS NOT NULL AND abs(a - pp) > 0.01 * abs(a)
        GROUP BY 1 ORDER BY 1""",
        "EEFF corporativos: Total de activos = Total de patrimonio y pasivos"),
    Check("identidad_seguros", "cmf_insurer_statements", "warn", """
        WITH t AS (
          SELECT period, insurance_type, rut,
                 MAX(value) FILTER (WHERE account_code = '5.10.00.00') a,
                 MAX(value) FILTER (WHERE account_code = '5.20.00.00') pp
          FROM cmf_insurer_statements WHERE statement_group = 'ESF' GROUP BY ALL)
        SELECT CAST(period AS VARCHAR), COUNT(*), 'compañías con |A − (P+Pat)| > 1%'
        FROM t WHERE a IS NOT NULL AND pp IS NOT NULL AND abs(a - pp) > 0.01 * abs(a)
        GROUP BY 1 ORDER BY 1""",
        "Seguros: 5.10.00.00 Total activo = 5.20.00.00 Total pasivo y patrimonio"),
    Check("identidad_intermediarios", "cmf_broker_statements", "warn", """
        WITH t AS (
          SELECT period, rut,
                 MAX(value) FILTER (WHERE account_code = '10.00.00') a,
                 MAX(value) FILTER (WHERE account_code = '20.00.00') pp
          FROM cmf_broker_statements WHERE statement_group = 'ESF' GROUP BY ALL)
        SELECT CAST(period AS VARCHAR), COUNT(*), 'entidades con |A − (P+Pat)| > 1%'
        FROM t WHERE a IS NOT NULL AND pp IS NOT NULL AND abs(a - pp) > 0.01 * abs(a)
        GROUP BY 1 ORDER BY 1""",
        "Intermediarios: 10.00.00 Total activos = 20.00.00 Total pasivos y patrimonio"),
    # Aviso y no error: las fallas medidas (nov-2026) son 116 filas, casi todas código J00 de
    # 13 compañías de generales, con cifras del propio informante que no cuadran (p. ej. BCI
    # 202608: valor_final 2.219.634 vs representativas 62.219.624). El parser lee bien.
    Check("identidad_cartera_seguros", "cmf_insurer_portfolio_control", "warn", """
        SELECT CAST(period AS VARCHAR), COUNT(*), 'filas con valor_final ≠ repr + no_repr'
        FROM cmf_insurer_portfolio_control
        WHERE abs(coalesce(valor_final,0) - coalesce(repr_rt_pr,0) - coalesce(no_repr_rt_pr,0)) > 1
        GROUP BY 1 ORDER BY 1""",
        "Cartera seguros B.8: valor_final = repr_rt_pr + no_repr_rt_pr"),

    # --- Completitud: entidades del último período vs. mediana de los 4 comparables ---
    Check("completitud_eeff", "cmf_financial_statements", "warn", """
        WITH n AS (SELECT period, COUNT(DISTINCT rut) n FROM cmf_financial_statements GROUP BY 1),
             w AS (SELECT period, n, median(n) OVER (PARTITION BY period % 100 ORDER BY period
                          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) med FROM n)
        SELECT CAST(period AS VARCHAR), n, 'empresas: ' || n || ' vs mediana ' || med
        FROM w WHERE med IS NOT NULL AND n < 0.9 * med AND period >= 202001 ORDER BY 1""",
        "EEFF: número de empresas por período ≥ 90% de los 4 mismos trimestres anteriores"),
    Check("completitud_bancos", "cmf_bank_statements", "warn", """
        WITH n AS (SELECT period, COUNT(DISTINCT bank_code) n FROM cmf_bank_statements GROUP BY 1),
             w AS (SELECT period, n, median(n) OVER (ORDER BY period
                          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) med FROM n)
        SELECT CAST(period AS VARCHAR), n, 'bancos: ' || n || ' vs mediana ' || med
        FROM w WHERE med IS NOT NULL AND n < 0.9 * med ORDER BY 1""",
        "Bancos: número de bancos por mes ≥ 90% de los 4 meses anteriores"),
    Check("completitud_ffmm", "cmf_mutual_fund_quota_values", "warn", """
        WITH n AS (SELECT date, COUNT(DISTINCT fund_run) n FROM cmf_mutual_fund_quota_values GROUP BY 1),
             w AS (SELECT date, n, median(n) OVER (ORDER BY date
                          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) med FROM n)
        SELECT CAST(date AS VARCHAR), n, 'fondos: ' || n || ' vs mediana ' || med
        FROM w WHERE med IS NOT NULL AND n < 0.9 * med ORDER BY 1""",
        "FFMM: número de fondos por corte ≥ 90% de los 4 cortes anteriores"),

    # --- Continuidad ---
    Check("continuidad_ffmm_cortes", "cmf_mutual_fund_quota_values", "warn", """
        WITH m AS (SELECT DISTINCT date_trunc('month', date) m FROM cmf_mutual_fund_quota_values),
             g AS (SELECT m, lag(m) OVER (ORDER BY m) prev FROM m)
        SELECT strftime(m, '%Y-%m'), datediff('month', prev, m) - 1, 'meses sin corte antes de este'
        FROM g WHERE prev IS NOT NULL AND datediff('month', prev, m) > 1 ORDER BY 1""",
        "FFMM: un corte por mes, sin meses faltantes"),
    Check("continuidad_bancos", "cmf_bank_statements", "warn", """
        WITH m AS (SELECT DISTINCT period FROM cmf_bank_statements),
             g AS (SELECT period, lag(period) OVER (ORDER BY period) prev FROM m)
        SELECT CAST(period AS VARCHAR),
               (period // 100 * 12 + period % 100) - (prev // 100 * 12 + prev % 100) - 1,
               'meses faltantes antes de este'
        FROM g WHERE prev IS NOT NULL
          AND (period // 100 * 12 + period % 100) - (prev // 100 * 12 + prev % 100) > 1""",
        "Bancos: sin meses faltantes"),
    Check("continuidad_afp_cuotas", "sp_quota_values", "warn", """
        WITH d AS (SELECT DISTINCT date FROM sp_quota_values WHERE date >= DATE '2020-01-01'),
             g AS (SELECT date, lag(date) OVER (ORDER BY date) prev FROM d)
        SELECT CAST(date AS VARCHAR), datediff('day', prev, date) - 1, 'días sin valor cuota antes de este'
        FROM g WHERE prev IS NOT NULL AND datediff('day', prev, date) > 4 ORDER BY 1""",
        "AFP valor cuota: sin huecos de más de 3 días (la SP publica todos los días)"),

    # --- Rangos ---
    Check("rango_cuota_ffmm", "cmf_mutual_fund_quota_values", "warn", """
        SELECT CAST(date AS VARCHAR), COUNT(*), 'valor cuota ≤ 0 o patrimonio < 0'
        FROM cmf_mutual_fund_quota_values
        WHERE quota_value <= 0 OR net_asset_value < 0 GROUP BY 1 ORDER BY 1""",
        "FFMM: valor cuota > 0 y patrimonio ≥ 0"),
    Check("rango_cuota_afp", "sp_quota_values", "warn", """
        WITH v AS (SELECT date, afp_name, fund_type, quota_value,
                          lag(quota_value) OVER (PARTITION BY afp_name, fund_type ORDER BY date) prev
                   FROM sp_quota_values)
        SELECT CAST(date AS VARCHAR), COUNT(*), 'variación diaria de cuota fuera de ±10%'
        FROM v WHERE prev > 0 AND abs(quota_value / prev - 1) > 0.10 GROUP BY 1 ORDER BY 1""",
        "AFP: variación diaria del valor cuota dentro de ±10%"),

    # --- Cruces ---
    Check("cruce_ffmm_cartera_cuotas", "cmf_mutual_fund_portfolio", "warn", """
        SELECT CAST(p.period_year * 100 + p.period_month AS VARCHAR), COUNT(DISTINCT p.fund_run),
               'fondos de la cartera sin valor cuota'
        FROM cmf_mutual_fund_portfolio p
        WHERE p.fund_run NOT IN (SELECT fund_run FROM cmf_mutual_fund_quota_values)
        GROUP BY 1 ORDER BY 1""",
        "FFMM: todo fondo con cartera tiene estadística de valor cuota"),
    Check("cruce_ffmm_agf_rut", "cmf_mutual_fund_quota_values", "warn", """
        SELECT CAST(date AS VARCHAR), COUNT(*), 'filas sin RUT de administradora'
        FROM cmf_mutual_fund_quota_values
        WHERE agf_rut IS NULL AND date = (SELECT MAX(date) FROM cmf_mutual_fund_quota_values)
        GROUP BY 1""",
        "FFMM: el último corte tiene RUT de administradora en todas las filas"),
    Check("series_sin_datos", "series", "warn", """
        SELECT s.id, 1, s.name FROM series s
        WHERE NOT EXISTS (SELECT 1 FROM observations o WHERE o.series_id = s.id)""",
        "BCCh: toda serie del catálogo tiene observaciones"),
]


def run_checks(db, persist: bool = True) -> list[dict]:
    """Corre todos los controles. Devuelve una fila por control (y por período fallido)."""
    run_at = dt.datetime.now().replace(microsecond=0)
    out: list[dict] = []
    for c in CHECKS:
        try:
            rows = db.conn.execute(c.sql).fetchall()
        except Exception as e:
            out.append(dict(run_at=run_at, check_id=c.check_id, tabla=c.tabla, periodo=None,
                            estado="error", n_fallas=None, detalle=f"el control no corrió: {e}",
                            descripcion=c.descripcion))
            continue
        if not rows:
            out.append(dict(run_at=run_at, check_id=c.check_id, tabla=c.tabla, periodo=None,
                            estado="ok", n_fallas=0, detalle="", descripcion=c.descripcion))
        for periodo, n, detalle in rows:
            out.append(dict(run_at=run_at, check_id=c.check_id, tabla=c.tabla, periodo=str(periodo),
                            estado=c.severidad, n_fallas=int(n or 0), detalle=str(detalle),
                            descripcion=c.descripcion))
    if persist:
        try:
            db.conn.executemany(
                "INSERT INTO dq_results (run_at, check_id, tabla, periodo, estado, n_fallas, detalle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [[r["run_at"], r["check_id"], r["tabla"], r["periodo"], r["estado"],
                  r["n_fallas"], r["detalle"]] for r in out])
        except Exception as e:  # BD en solo lectura: se informa igual, sin guardar
            logger.info(f"dq_results no se guardó: {e}")
    n_err = sum(r["estado"] == "error" for r in out)
    n_warn = sum(r["estado"] == "warn" for r in out)
    logger.info(f"Controles de calidad: {len(CHECKS)} controles, {n_err} errores, {n_warn} avisos")
    return out


def last_summary(db) -> dict | None:
    """Resumen de la última corrida guardada (para el panel web)."""
    try:
        row = db.conn.execute("SELECT MAX(run_at) FROM dq_results").fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    run_at = row[0]
    rows = db.conn.execute(
        "SELECT check_id, tabla, periodo, estado, n_fallas, detalle FROM dq_results "
        "WHERE run_at = ? AND estado <> 'ok' ORDER BY estado, check_id, periodo", [run_at]).fetchall()
    total = db.conn.execute(
        "SELECT COUNT(DISTINCT check_id) FROM dq_results WHERE run_at = ?", [run_at]).fetchone()[0]
    return {
        "run_at": run_at, "total": total,
        "errores": sum(r[3] == "error" for r in rows),
        "avisos": sum(r[3] == "warn" for r in rows),
        "detalle": [dict(zip(["check_id", "tabla", "periodo", "estado", "n_fallas", "detalle"], r))
                    for r in rows],
    }
