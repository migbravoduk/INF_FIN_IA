"""
db/common.py — Utilidades sin conexión compartidas por la capa de datos: detección de
cuentas totalizadoras, criterio AGF y ratios financieros.
"""

from typing import Optional

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


_INSERT_RE = _re.compile(r"^\s*INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\(", _re.I | _re.S)


def fast_executemany(conn, sql: str, rows) -> None:
    """
    `executemany` para cargas masivas: DuckDB inserta fila a fila con executemany (una
    cartera de fondos mutuos de 33 mil filas tardaba minutos). Si la sentencia es un
    `INSERT INTO t (cols) VALUES (?, …)` se inserta de una vez desde un DataFrame; si no,
    se cae al executemany normal.
    """
    rows = list(rows)
    m = _INSERT_RE.match(sql)
    if not m or len(rows) < 500:
        conn.executemany(sql, rows)
        return
    import pandas as pd
    table, cols = m.group(1), [c.strip() for c in m.group(2).split(",")]
    df = pd.DataFrame([list(r) for r in rows], columns=cols, dtype=object)
    conn.register("_bulk_df", df)
    try:
        conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _bulk_df")
    finally:
        conn.unregister("_bulk_df")
