"""
db/derivados_afp.py — Derivados de los fondos de pensiones por AFP, fondo y contraparte.

Fuente: cartera desagregada mensual de la SP (`sp_cartera_detalle`, collectors/sp_cartera_detalle.py).
Complementa al Banco Central (db/derivados.py), que da el sistema AFP agregado y más reciente;
la SP llega con ~4 meses de rezago pero abre por administradora, tipo de fondo y contraparte.

- Swaps (cuadros "swaps nacionales/extranjeros"): VALORIZACIÓN del contrato en MMUSD, con
  signo (negativo = pérdida no realizada del fondo). No es el nocional.
- Forwards: unidades del contrato en el activo objeto, MMUSD. Las filas con código de 4 letras
  (WNMC, YEMV…) son el total del código; las siguientes, su desglose por moneda contraparte.
  4.ª letra C/V = compra/venta de la moneda objeto: validado contra el Banco Central (jun-2026,
  USD contra pesos: SP neto −41.800 vs. BCCh −37.839 MMUSD; bancos locales −34.310 vs. −32.557).
  La SP no publica el diccionario de las otras letras (W/Y, M/N): no se interpretan.
"""

from __future__ import annotations

import pandas as pd

SWAPS = ("swap_nac", "swap_ext")
AFPS = ["CAPITAL", "CUPRUM", "HABITAT", "MODELO", "PLANVITAL", "PROVIDA", "UNO"]


def _mes(p: int) -> str:
    return f"{p // 100}-{p % 100:02d}"


def _tipo_swap(grupo: str | None) -> str:
    g = (grupo or "").upper()
    tipo = "moneda" if "MONEDA" in g else ("índices de tasas" if "INDICE" in g else "tasas")
    via = "cámara" if "CAMARA" in g else ("garantías bilaterales" if "BILATERAL" in g else "sin garantías")
    return f"{tipo} · {via}"


def swaps_afp(db, top: int = 15) -> dict:
    """Valorización de los swaps por AFP (serie), por AFP × fondo y por contraparte (último mes)."""
    df = db.conn.execute("""
        SELECT period, tabla, fondo, afp, grupo, glosa, valor
        FROM sp_cartera_detalle
        WHERE tabla IN ('swap_nac', 'swap_ext') AND metrica = 'monto_dolares'
          AND afp <> 'TOTAL' AND glosa NOT LIKE 'TOTAL%'""").fetchdf()
    if df.empty:
        return {}
    per = sorted(df.period.unique())
    ult = int(per[-1])
    piv = df.pivot_table(index="period", columns="afp", values="valor", aggfunc="sum").reindex(per)
    tot = df.groupby(["period", "tabla"]).valor.sum().unstack().reindex(per)
    serie = {"meses": [_mes(int(p)) for p in per],
             "afp": {a: [None if pd.isna(v) else round(float(v), 1) for v in piv[a]]
                     for a in AFPS if a in piv},
             "nac": [round(float(v), 1) if pd.notna(v) else None for v in tot.get("swap_nac", pd.Series(index=per))],
             "ext": [round(float(v), 1) if pd.notna(v) else None for v in tot.get("swap_ext", pd.Series(index=per))]}
    u = df[df.period == ult]
    tab = u.pivot_table(index="afp", columns="fondo", values="valor", aggfunc="sum").fillna(0.0)
    tab["Total"] = tab.sum(axis=1)
    tab = tab.sort_values("Total")
    fondos = [f for f in "ABCDE" if f in tab.columns]
    por_afp = [{"afp": a, "fondos": {f: float(r[f]) for f in fondos}, "total": float(r["Total"])}
               for a, r in tab.iterrows()]
    tot_fondo = {f: float(tab[f].sum()) for f in fondos}
    cp = (u.assign(origen=u.tabla.map({"swap_nac": "nacional", "swap_ext": "extranjera"}))
           .groupby(["glosa", "origen"])
           .agg(valor=("valor", "sum"), afps=("afp", "nunique")).reset_index())
    cp = cp.reindex(cp.valor.abs().sort_values(ascending=False).index).head(top)
    tipos = (u.assign(tipo=u.grupo.map(_tipo_swap),
                      origen=u.tabla.map({"swap_nac": "nacional", "swap_ext": "extranjero"}))
              .groupby(["origen", "tipo"]).valor.sum().reset_index().sort_values("valor"))
    # Cambio en el último año disponible (mismo mes del año anterior, si existe)
    hace12 = ult - 100
    var12 = None
    if hace12 in piv.index:
        var12 = {a: float(piv.loc[ult, a] - (piv.loc[hace12, a] if pd.notna(piv.loc[hace12, a]) else 0.0))
                 for a in AFPS if a in piv and pd.notna(piv.loc[ult, a])}
    return {"ultimo": _mes(ult), "serie": serie, "por_afp": por_afp, "fondos": fondos,
            "tot_fondo": tot_fondo, "total": float(u.valor.sum()),
            "nac": float(u[u.tabla == "swap_nac"].valor.sum()), "ext": float(u[u.tabla == "swap_ext"].valor.sum()),
            "var12": var12, "hace12": _mes(hace12) if var12 else None,
            "contrapartes": [{"contraparte": r.glosa, "origen": r.origen, "valor": float(r.valor),
                              "afps": int(r.afps)} for r in cp.itertuples()],
            "tipos": [{"origen": r.origen, "tipo": r.tipo, "valor": float(r.valor)} for r in tipos.itertuples()],
            "desde": _mes(int(per[0]))}


def forwards_usd_afp(db) -> dict:
    """Forwards sobre el dólar contra pesos por AFP (último mes): compra, venta y neto (MMUSD)."""
    ult = db.conn.execute("""SELECT MAX(period) FROM sp_cartera_detalle
                             WHERE tabla IN ('fwd_nac', 'fwd_ext')""").fetchone()[0]
    if not ult:
        return {}
    df = db.conn.execute("""
        SELECT tabla, afp, right(codigo, 1) AS cv, SUM(valor) AS v
        FROM sp_cartera_detalle
        WHERE period = ? AND tabla IN ('fwd_nac', 'fwd_ext') AND metrica = 'monto_dolares'
          AND grupo LIKE 'Dólar estadounidense%' AND glosa = 'Pesos' AND afp <> 'TOTAL'
        GROUP BY ALL""", [ult]).fetchdf()
    if df.empty:
        return {}
    w = df.pivot_table(index="afp", columns="cv", values="v", aggfunc="sum").fillna(0.0)
    loc = df[df.tabla == "fwd_nac"].pivot_table(index="afp", columns="cv", values="v", aggfunc="sum").fillna(0.0)
    filas = []
    for a in AFPS:
        if a not in w.index:
            continue
        c, v = float(w.loc[a].get("C", 0.0)), float(w.loc[a].get("V", 0.0))
        nl = float(loc.loc[a].get("C", 0.0) - loc.loc[a].get("V", 0.0)) if a in loc.index else 0.0
        filas.append({"afp": a, "compra": c, "venta": v, "neto": c - v, "neto_local": nl, "neto_exterior": c - v - nl})
    return {"ultimo": _mes(int(ult)), "filas": filas,
            "total": {k: sum(f[k] for f in filas) for k in ("compra", "venta", "neto", "neto_local", "neto_exterior")}}
