"""
api/eeff_format.py — Normalización de presentación de estados financieros (CMF).

El archivo plano de la CMF repite el mismo nombre de cuenta bajo distintos contextos
XBRL que no quedan en las columnas (taxonomy_code es siempre 'TAX CI'). Esto produce:
  - filas exactamente duplicadas (mismo nombre y valor) → ruido visual.
  - filas con el mismo nombre y valores distintos (ítems separados sin etiqueta propia).

build_statement_groups() colapsa los duplicados exactos y numera los nombres que
quedan repetidos con valores distintos, para que la vista sea legible sin perder datos.
"""

from collections import Counter

# Códigos de estado financiero de la CMF → nombre legible.
GROUP_LABELS = {
    "ESF C/NC": "Estado de Situación Financiera (corriente / no corriente)",
    "ESF OL": "Estado de Situación Financiera (orden de liquidez)",
    "ERFG": "Estado de Resultados (por función)",
    "ERNG": "Estado de Resultados (por naturaleza)",
    "ERI": "Estado de Resultado Integral",
    "EFMD": "Estado de Flujo de Efectivo (método directo)",
    "EFMI": "Estado de Flujo de Efectivo (método indirecto)",
}
# Orden lógico de presentación: balance → resultados → integral → flujo de efectivo.
GROUP_ORDER = ["ESF C/NC", "ESF OL", "ERFG", "ERNG", "ERI", "EFMD", "EFMI"]


def build_statement_groups(df) -> list[dict]:
    """
    Agrupa un DataFrame de estados financieros por estado, con nombre legible y orden
    lógico; colapsa duplicados exactos y numera los nombres repetidos con valores distintos.

    Devuelve: [{"group": str legible, "code": str, "rows": [{"account_name", "value"}]}]
    """
    by_code = {}
    for gname, sub in df.groupby("statement_group", sort=False):
        code = gname or "—"
        # 1. Colapsar duplicados exactos (mismo nombre + mismo valor).
        seen, deduped = set(), []
        for _, r in sub.iterrows():
            name, val = str(r["account_name"]), r["value"]
            if (name, val) in seen:
                continue
            seen.add((name, val))
            deduped.append({"account_name": name, "value": val})

        # 2. Numerar los nombres que aún quedan repetidos (valores distintos).
        counts = Counter(d["account_name"] for d in deduped)
        running, rows = {}, []
        for d in deduped:
            name = d["account_name"]
            if counts[name] > 1:
                running[name] = running.get(name, 0) + 1
                label = f"{name} ({running[name]})"
            else:
                label = name
            rows.append({"account_name": label, "value": d["value"]})
        by_code[code] = rows

    # 3. Emitir en orden lógico, con etiqueta legible (extras desconocidos al final).
    groups, seen_codes = [], set()
    for code in GROUP_ORDER:
        if code in by_code:
            groups.append({"group": GROUP_LABELS.get(code, code), "code": code, "rows": by_code[code]})
            seen_codes.add(code)
    for code, rows in by_code.items():
        if code not in seen_codes:
            groups.append({"group": GROUP_LABELS.get(code, code), "code": code, "rows": rows})
    return groups
