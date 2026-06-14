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


def build_statement_groups(df) -> list[dict]:
    """
    Agrupa un DataFrame de estados financieros por statement_group (en orden),
    colapsa duplicados exactos y numera los nombres repetidos con valores distintos.

    Devuelve: [{"group": str, "rows": [{"account_name": str, "value": float}]}]
    """
    groups = []
    for gname, sub in df.groupby("statement_group", sort=False):
        # 1. Colapsar duplicados exactos (mismo nombre + mismo valor).
        seen, deduped = set(), []
        for _, r in sub.iterrows():
            name, val = str(r["account_name"]), r["value"]
            key = (name, val)
            if key in seen:
                continue
            seen.add(key)
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

        groups.append({"group": gname or "—", "rows": rows})
    return groups
