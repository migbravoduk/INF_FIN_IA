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


# ---------------------------------------------------------------------------------------------
# Indicadores del ranking: una sola definición de etiqueta, tipo y sentido (qué es «mejor»).
#   pct    → se muestra en % (y se descartan |valor| > 150 por distorsión)
#   mejor  → "alto" | "bajo": en endeudamiento, más alto es peor
#   flujo  → usa cuentas del estado de resultados (en el ranking, de los últimos 12 meses)
#   unidad → sufijo al mostrar: "%" | "x" | "" | "mon" (monto en la moneda del reporte)
# ---------------------------------------------------------------------------------------------
RATIO_META = {
    "roe":                {"label": "ROE", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "roa":                {"label": "ROA", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "margen_neto":        {"label": "Margen neto", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "margen_bruto":       {"label": "Margen bruto", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "margen_ebit":        {"label": "Margen EBIT", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "margen_ebitda":      {"label": "Margen EBITDA", "pct": True, "mejor": "alto", "flujo": True, "unidad": "%"},
    "liquidez":           {"label": "Liquidez corriente", "pct": False, "mejor": "alto", "flujo": False, "unidad": "x"},
    "test_acido":         {"label": "Test Ácido", "pct": False, "mejor": "alto", "flujo": False, "unidad": "x"},
    "endeudamiento":      {"label": "Deuda / Patrimonio", "pct": False, "mejor": "bajo", "flujo": False, "unidad": "x"},
    "pasivo_activo":      {"label": "Pasivos / Activos", "pct": True, "mejor": "bajo", "flujo": False, "unidad": "%"},
    "cobertura_intereses": {"label": "Cobertura de Intereses", "pct": False, "mejor": "alto", "flujo": True, "unidad": "x"},
    "capital_trabajo":    {"label": "Capital de Trabajo", "pct": False, "mejor": "alto", "flujo": False, "unidad": "mon"},
    "z_altman":           {"label": "Z'' de Altman (salud financiera)", "pct": False, "mejor": "alto", "flujo": True, "unidad": ""},
    "deuda_neta_ebitda":  {"label": "Deuda financiera neta / EBITDA", "pct": False, "mejor": "bajo", "flujo": True, "unidad": "x"},
}

# Cuentas del estado de resultados (acumuladas en el año). El ranking las anualiza (LTM).
FLOW_ACCOUNTS = (
    "Ingresos de actividades ordinarias", "Ganancia bruta", "Ganancia (pérdida)",
    "Ganancias (pérdidas) de actividades operacionales", "Ganancia (pérdida), antes de impuestos",
    "Costos financieros", "Gasto por depreciación y amortización",
    "Ajustes por gastos de depreciación y amortización",
)
# Sectores donde el Z'' y el EBITDA no tienen sentido (el balance de un banco, una holding o una AGF no se lee igual).
SECTORES_SIN_Z = ("Servicios Financieros", "Holdings e Inversiones")

# Z'' de Altman para no manufactureras y mercados emergentes: zonas.
Z_SEGURA, Z_PELIGRO = 2.6, 1.1


def zona_z(z) -> Optional[str]:
    """'segura' (> 2,6) · 'gris' (1,1 a 2,6) · 'peligro' (< 1,1); None si no hay Z''."""
    if z is None or z != z:
        return None
    return "segura" if z > Z_SEGURA else ("gris" if z >= Z_PELIGRO else "peligro")


def first_values(df) -> dict:
    """{cuenta: valor} con la PRIMERA ocurrencia de cada cuenta (el orden de ingesta es el orden IFRS)."""
    vals = {}
    for n, v in zip(df["account_name"].astype(str), df["value"]):
        if n not in vals:
            vals[n] = v
    return vals


def ltm_flows(actual: dict, dic_anterior: Optional[dict], mismo_trim_anterior: Optional[dict], mes: int) -> dict:
    """
    Flujos de los últimos 12 meses (LTM) de una empresa. Las cuentas del estado de resultados vienen
    ACUMULADAS en el año, así que:
        LTM = acumulado actual + año anterior completo (diciembre) − acumulado del mismo trimestre del año anterior
    En diciembre el acumulado ya es el año: LTM = actual. Si a una cuenta le falta alguno de los tres
    valores, no se anualiza y queda fuera (None): esa empresa sale de los indicadores que la usan,
    pero no de los de balance.
    """
    out = {}
    for n in FLOW_ACCOUNTS:
        a = actual.get(n)
        if a is None or a != a:
            continue
        if mes == 12:
            out[n] = float(a)
            continue
        d = (dic_anterior or {}).get(n)
        q = (mismo_trim_anterior or {}).get(n)
        if d is not None and q is not None and d == d and q == q:
            out[n] = float(a) + float(d) - float(q)
    return out


def compute_ratios(df, ltm: Optional[dict] = None) -> Optional[dict]:
    """
    Calcula indicadores financieros desde un DataFrame de EEFF de UNA empresa/período.
    Reutilizable sin conexión (la usa Database.get_company_ratios y el export estático).
    Income es del período (acumulado en el año para trimestres) → ratios "del período".

    `ltm` ({cuenta de flujo: valor de 12 meses}, ver `ltm_flows`) reemplaza los valores acumulados de las
    cuentas de FLOW_ACCOUNTS: los ratios de flujo pasan a ser de los últimos 12 meses y los que no
    tengan su cuenta anualizada quedan en None.
    """
    if df is None or df.empty:
        return None
    vals = {}
    for _, r in df.iterrows():
        n = str(r["account_name"])
        if n not in vals:  # primera ocurrencia = orden IFRS
            vals[n] = r["value"]
    if ltm is not None:
        for n in FLOW_ACCOUNTS:
            vals[n] = ltm.get(n)

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

    # El EBITDA solo es EBITDA si la empresa informa depreciación; si no, `ebitda` = EBIT. Solo ~1 de cada 5
    # empresas la informa en el archivo plano, así que el ranking exige este dato para los indicadores con EBITDA.
    deprec_informada = g("Gasto por depreciación y amortización") is not None or \
        g("Ajustes por gastos de depreciación y amortización") is not None

    # Z'' de Altman (no manufactureras / mercados emergentes), Altman 1995:
    #   Z'' = 6,56·X1 + 3,26·X2 + 6,72·X3 + 1,05·X4
    #   X1 capital de trabajo / activos · X2 ganancias acumuladas / activos
    #   X3 EBIT / activos · X4 patrimonio / pasivos
    acumuladas = g("Ganancias (pérdidas) acumuladas")
    x1 = div(ac - pc, activos) if (ac is not None and pc is not None) else None
    x2, x3, x4 = div(acumuladas, activos), div(ebit, activos), div(patrim, pasivos)
    z_altman = (6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4) if None not in (x1, x2, x3, x4) else None

    # Deuda financiera neta / EBITDA: (otros pasivos financieros + arriendos − efectivo) / EBITDA.
    pf = [g("Otros pasivos financieros corrientes"), g("Otros pasivos financieros no corrientes"),
          g("Pasivos por arrendamientos corrientes"), g("Pasivos por arrendamientos no corrientes")]
    efectivo = g("Efectivo y equivalentes al efectivo")
    deuda_neta_ebitda, ebitda_negativo = None, False
    if any(x is not None for x in pf) and efectivo is not None and ebit is not None and deprec_informada:
        neta = sum(x for x in pf if x is not None) - efectivo
        if ebitda is not None and ebitda > 0:
            deuda_neta_ebitda = neta / ebitda
        else:
            ebitda_negativo = True       # sin ratio (no es un número); el ranking lo ordena como el peor

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
        "deprec_informada": deprec_informada,
        # Con patrimonio ≤ 0, ROE y deuda/patrimonio cambian de signo y dejan de comparar: el ranking
        # excluye el ROE y pone la deuda/patrimonio entre las peores.
        "patrimonio_negativo": patrim is not None and patrim <= 0,

        # Salud financiera
        "z_altman": z_altman,
        "deuda_neta_ebitda": deuda_neta_ebitda,
        "ebitda_negativo": ebitda_negativo,
    }


def ordenar_ranking(universo: list, metric: str, sector: Optional[str] = None) -> tuple[list, int]:
    """
    Ordena el universo de empresas (ver `Database.get_ratios_universo`) por un indicador, de MEJOR a
    peor según `RATIO_META[metric]["mejor"]` (en endeudamiento, el menor es el mejor). Devuelve
    (empresas con dato, total de empresas del alcance). Las de dato ausente quedan fuera. Las de
    «EBITDA negativo» (deuda neta / EBITDA) y de «Patrimonio negativo» (deuda / patrimonio) van al final,
    como las peores. Para métricas en % se
    descartan |valor| > 150 y las empresas sin margen neto (holdings/fondos sin operación).
    """
    meta = RATIO_META[metric]
    pool = [r for r in universo if not sector or r["sector"] == sector]
    con = []
    for r in pool:
        v = r.get(metric)
        texto = None
        if metric == "deuda_neta_ebitda" and r.get("ebitda_negativo"):
            v, texto = float("inf"), "EBITDA negativo"
        elif metric == "endeudamiento" and r.get("patrimonio_negativo"):
            v, texto = float("inf"), "Patrimonio negativo"
        elif metric == "roe" and r.get("patrimonio_negativo"):
            continue
        if v is None or v != v:
            continue
        if meta["pct"] and (abs(v) > 150 or r.get("margen_neto") is None):
            continue
        con.append({"company_name": r["company_name"], "rut": r["rut"], "value": v, "texto": texto,
                    "currency": r["currency"], "sector": r["sector"],
                    "z": r.get("z_altman"), "zona": zona_z(r.get("z_altman"))})
    con.sort(key=lambda x: x["value"], reverse=(meta["mejor"] == "alto"))
    return con, len(pool)


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
