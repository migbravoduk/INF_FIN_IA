"""
db/entities.py — Maestra de entidades (`dim_entidad`) y sus alias.

Una fila por RUT para todo actor que aparece en alguna fuente: emisores con EEFF, bancos,
AGF, AFP, aseguradoras e intermediarios. Antes cada tabla identificaba a su manera
(`bank_code` sin RUT en bancos, `afp_name` en texto en la SP, RUT con grafías sucias en
la cartera de seguros) y cruzar exigía resolver nombres a mano en cada vista.

Reglas:
- RUT sin dígito verificador ni ceros a la izquierda (igual que el resto del repo).
- El nombre canónico es el MÁS RECIENTE con que la entidad reporta; los anteriores quedan
  en `dim_entidad_alias` con su rango de períodos (cambios de razón social, fusiones).
- Los RUT de bancos y AFP NO vienen en sus fuentes: se curan en `config/entidades.yaml`
  con su verificación anotada. No se infieren por parecido de nombre.
- `rebuild_entities` es idempotente: borra y reconstruye las dos tablas.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from db.database import AGF_SQL_PREDICATE

logger = logging.getLogger(__name__)

from config.settings import config_path

CONFIG = config_path("entidades.yaml")

# Prioridad cuando un RUT aparece en varias fuentes (menor = manda).
_PRIORIDAD = {"estado": 0, "banco": 0, "afp": 1, "agf": 2, "aseguradora_vida": 3,
              "aseguradora_generales": 3, "corredora": 4, "agente_valores": 4, "emisor": 5,
              "otro": 6}


def load_config() -> dict:
    if not CONFIG.exists():
        return {}
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _alias_sql() -> str:
    """Todas las apariciones (rut, nombre, fuente, tipo, período) de todas las tablas."""
    return f"""
    SELECT rut, company_name AS nombre, 'cmf_financial_statements' AS fuente,
           CASE WHEN {AGF_SQL_PREDICATE} THEN 'agf'
                WHEN upper(company_name) LIKE '%ADMINISTRADORA DE FONDOS DE PENSIONES%' THEN 'afp'
                ELSE 'emisor' END AS tipo,
           MIN(period) AS desde, MAX(period) AS hasta
    FROM cmf_financial_statements GROUP BY ALL
    UNION ALL
    SELECT rut, company_name, 'cmf_insurer_statements', 'aseguradora_' || insurance_type,
           MIN(period), MAX(period)
    FROM cmf_insurer_statements GROUP BY ALL
    UNION ALL
    SELECT rut, company_name, 'cmf_insurer_portfolio_control', 'aseguradora_' || insurance_type,
           MIN(period), MAX(period)
    FROM cmf_insurer_portfolio_control WHERE company_name <> '' GROUP BY ALL
    UNION ALL
    SELECT rut, company_name, 'cmf_broker_statements',
           CASE WHEN broker_type = 'AGENTES' THEN 'agente_valores' ELSE 'corredora' END,
           MIN(period), MAX(period)
    FROM cmf_broker_statements GROUP BY ALL
    UNION ALL
    SELECT agf_rut, agf_name, 'cmf_mutual_fund_agf', 'agf', NULL, NULL
    FROM cmf_mutual_fund_agf
    UNION ALL
    SELECT agf_rut, agf_name, 'cmf_mutual_fund_quota_values', 'agf',
           MIN(year(date) * 100 + month(date)), MAX(year(date) * 100 + month(date))
    FROM cmf_mutual_fund_quota_values WHERE agf_rut IS NOT NULL GROUP BY ALL
    """


def rebuild_entities(db) -> dict:
    """Reconstruye `dim_entidad` y `dim_entidad_alias`. Devuelve conteos por tipo."""
    from api.insurer_glossary import clean_company_name

    cfg = load_config()
    rows = db.conn.execute(_alias_sql()).fetchall()

    alias: list[tuple] = []
    ent: dict[str, dict] = {}
    for rut, nombre, fuente, tipo, desde, hasta in rows:
        rut = str(rut or "").strip().lstrip("0")
        if not rut:
            continue
        if fuente == "cmf_insurer_portfolio_control":
            nombre = clean_company_name(nombre)
        nombre = " ".join(str(nombre or "").split())
        alias.append((rut, nombre, fuente, desde, hasta))
        e = ent.setdefault(rut, {"tipo": tipo, "fuentes": set(), "nombres": [], "corto": None})
        if _PRIORIDAD.get(tipo, 9) < _PRIORIDAD.get(e["tipo"], 9):
            e["tipo"] = tipo
        e["fuentes"].add(fuente)
        if fuente == "cmf_mutual_fund_agf":
            e["corto"] = nombre
        else:
            e["nombres"].append((hasta or 0, _PRIORIDAD.get(tipo, 9), nombre))

    # Curados: bancos (bank_code → RUT) y AFP (nombre SP → RUT).
    def curado(rut, nombre, tipo, corto=None, fuente="config/entidades.yaml", **extra):
        rut = str(rut).lstrip("0")
        e = ent.setdefault(rut, {"tipo": tipo, "fuentes": set(), "nombres": [], "corto": None})
        e["tipo"] = tipo
        e["fuentes"].add(fuente)
        e["corto"] = corto or e["corto"]
        e["nombres"].append((999999, -1, nombre))  # el nombre curado manda
        e.update(extra)
        alias.append((rut, nombre, fuente, None, None))

    bank_names = dict(db.conn.execute(
        "SELECT bank_code, arg_max(bank_name, period) FROM cmf_bank_statements GROUP BY 1").fetchall())
    bank_last = dict(db.conn.execute(
        "SELECT bank_code, MAX(period) FROM cmf_bank_statements GROUP BY 1").fetchall())
    for code, b in (cfg.get("bancos") or {}).items():
        code = str(code).zfill(3)
        curado(b["rut"], b.get("nombre") or bank_names.get(code, code), "banco",
               corto=b.get("corto"), bank_code=code, ultimo=bank_last.get(code))
        for n in db.conn.execute("SELECT DISTINCT bank_name FROM cmf_bank_statements WHERE bank_code = ?",
                                 [code]).fetchall():
            alias.append((str(b["rut"]).lstrip("0"), n[0], "cmf_bank_statements", None, None))
    for rut, em in (cfg.get("emisores") or {}).items():
        curado(rut, em["nombre"], em.get("tipo", "emisor"), corto=em.get("corto"))
    for afp, a in (cfg.get("afp") or {}).items():
        curado(a["rut"], a["nombre"], "afp", corto=afp)
        alias.append((str(a["rut"]).lstrip("0"), afp, "sp_quota_values", None, None))

    out = []
    for rut, e in ent.items():
        nombres = sorted(e["nombres"], key=lambda x: (x[0], -x[1]))
        canon = nombres[-1][2] if nombres else (e["corto"] or rut)
        ultimo = e.get("ultimo") or max((x[0] for x in nombres if 0 < x[0] < 999999), default=None)
        out.append((rut, None, canon, e["corto"], e["tipo"], e.get("bank_code"), None,
                    None, ultimo, ",".join(sorted(e["fuentes"]))))

    # vigente: reporta en alguno de los dos últimos períodos de su fuente.
    db.conn.execute("BEGIN TRANSACTION")
    try:
        db.conn.execute("DELETE FROM dim_entidad")
        db.conn.execute("DELETE FROM dim_entidad_alias")
        db.conn.executemany("INSERT INTO dim_entidad VALUES (?,?,?,?,?,?,?,?,?,?)", out)
        db.conn.executemany("INSERT INTO dim_entidad_alias VALUES (?,?,?,?,?)",
                            list({a: None for a in alias}))
        db.conn.execute("""
            UPDATE dim_entidad SET vigente = ultimo_periodo >= (
                SELECT MAX(period) FROM (SELECT period FROM cmf_financial_statements
                                         UNION ALL SELECT period FROM cmf_bank_statements) t) - 100
        """)
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
    res = dict(db.conn.execute("SELECT tipo, COUNT(*) FROM dim_entidad GROUP BY 1").fetchall())
    logger.info(f"dim_entidad reconstruida: {sum(res.values())} entidades {res}")
    return res


def issuer_coverage(db, period: int | None = None) -> tuple[int, int]:
    """(emisores distintos en B.1, cuántos resuelven a un nombre en dim_entidad)."""
    if period is None:
        period = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    return db.conn.execute("""
        WITH e AS (SELECT DISTINCT ltrim(rut_emisor, '0') r FROM cmf_insurer_portfolio_fixed_income
                   WHERE period = ? AND rut_emisor IS NOT NULL)
        SELECT COUNT(*), COUNT(d.rut) FROM e LEFT JOIN dim_entidad d ON d.rut = e.r
    """, [period]).fetchone()
