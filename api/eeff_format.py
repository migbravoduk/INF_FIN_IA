"""
api/eeff_format.py — Normalización de presentación de estados financieros (CMF).

El archivo plano de la CMF repite el mismo nombre de cuenta bajo distintos contextos
XBRL que no quedan en las columnas (taxonomy_code es siempre 'TAX CI'). Esto produce:
  - filas exactamente duplicadas (mismo nombre y valor) → ruido visual.
  - filas con el mismo nombre y valores distintos (ítems separados sin etiqueta propia).

build_statement_groups() colapsa los duplicados exactos y numera los nombres que
quedan repetidos con valores distintos, para que la vista sea legible sin perder datos.
"""

import re
from collections import Counter

from db.database import is_total_account

# Etiqueta uniforme por código (sin distinguir variantes en la UI).
GROUP_LABELS = {
    "ESF C/NC": "Estado de Situación Financiera",
    "ESF OL": "Estado de Situación Financiera",
    "ERFG": "Estado de Resultados",
    "ERNG": "Estado de Resultados",
    "ERI": "Estado de Resultado Integral",
    "EFMD": "Estado de Flujo de Efectivo",
    "EFMI": "Estado de Flujo de Efectivo",
}
# Slots de presentación con PREFERENCIA por el estándar (primero el preferido):
#   balance corriente/no corriente > orden de liquidez; resultados por función > naturaleza;
#   flujo directo > indirecto. Se muestra UNO por slot (el estándar si existe).
GROUP_SLOTS = [
    ["ESF C/NC", "ESF OL"],
    ["ERFG", "ERNG"],
    ["ERI"],
    ["EFMD", "EFMI"],
]
# Códigos estándar (para selectores y orden por defecto).
GROUP_ORDER = ["ESF C/NC", "ERFG", "ERI", "EFMD"]


def _assign_balance_sections(rows):
    """
    Marca cada fila del balance (ESF) con su sección (Activos / Pasivos / Patrimonio),
    usando los grandes totales como delimitadores (orden IFRS garantizado por `id`).
    Si no encuentra el marcador 'Total de activos', no secciona (deja las filas sin section).
    """
    section = "Activos"
    found = False
    for r in rows:
        r["section"] = section
        nm = re.sub(r"\s*\(\d+\)$", "", r["account_name"]).strip().lower()
        if nm == "total de activos":
            section = "Pasivos"
            found = True
        elif nm == "total de pasivos":
            section = "Patrimonio"
    if not found:
        for r in rows:
            r.pop("section", None)


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
            rows.append({"account_name": label, "value": d["value"],
                         "is_total": is_total_account(label)})

        # Sub-secciones del balance (Activos / Pasivos / Patrimonio).
        if code.startswith("ESF"):
            _assign_balance_sections(rows)
        by_code[code] = rows

    # 3. Emitir un estado por slot, prefiriendo el estándar; fallback al no estándar
    #    solo si el estándar no existe (p. ej. utilities que reportan por naturaleza).
    groups = []
    for slot in GROUP_SLOTS:
        for code in slot:
            if code in by_code:
                groups.append({"group": GROUP_LABELS[code], "code": code, "rows": by_code[code]})
                break
    return groups
