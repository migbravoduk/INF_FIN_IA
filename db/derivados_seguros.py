"""
db/derivados_seguros.py — Derivados de las compañías de seguros, compañía por compañía
(PLAN_DERIVADOS fase 3). Fuente: Circular 1.835, archivo B.7 (`cmf_insurer_derivatives`).

- Posición en moneda extranjera: por contrato, + valor en M$ de la pata larga si es moneda
  extranjera, − el de la pata corta si lo es. En USD equivalentes (÷ dólar del cierre). Se
  compara con el sector seguros del Banco Central (F099.DER, sector 44, moneda extranjera vs.
  peso), que mide lo mismo desde los reportes de los bancos y del mercado cambiario.
- Valor razonable (M$) de forwards + swaps + opciones = total "productos derivados" del B.8
  (M10) — reconciliado en la carga, ver `main.py backfill-derivados-seguros`.
"""

from __future__ import annotations

import pandas as pd

LOCALES = ("$$", "CLP", "UF", "0UF", "NO APLICA", "")
BCCH_SEGUROS_NETO = "F099.DER.STO.Z.44.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M"
TIPO_CONTRAPARTE = {"01": "ECC nacional", "02": "Banco nacional", "03": "Otra nacional",
                    "04": "ECC exterior", "05": "Banco exterior", "06": "Otra exterior"}


# Las posiciones (monedas, nocionales) solo se leen bien con el layout vigente desde el
# 1-dic-2024; antes, el registro tiene otro orden y solo el valor razonable es confiable
# (se ubica por patrón y cuadra con el B.8). Ver la verificación en docs/PLAN_DERIVADOS.md.
DESDE_LAYOUT_NUEVO = 202412
TOL_PRECIO = 0.20   # una pata vale si su precio implícito está a ±20% de la referencia


def _frame(db, desde: int) -> pd.DataFrame:
    df = db.conn.execute("""
        SELECT d.*,
               (SELECT arg_max(o.value, o.date) FROM observations o WHERE o.series_id = 'F073.TCO.PRE.Z.D'
                  AND o.date <= last_day(make_date(d.period // 100, d.period % 100, 1))) AS usd,
               (SELECT arg_max(o.value, o.date) FROM observations o WHERE o.series_id = 'F073.UFF.PRE.Z.D'
                  AND o.date <= last_day(make_date(d.period // 100, d.period % 100, 1))) AS uf
        FROM cmf_insurer_derivatives d WHERE d.period >= ?""", [desde]).fetchdf()
    if df.empty:
        return df
    nuevo = df.period >= DESDE_LAYOUT_NUEVO
    for lado, m, u, v in (("l", "moneda_larga", "unidades_larga", "nocional_larga_m"),
                          ("c", "moneda_corta", "unidades_corta", "nocional_corta_m")):
        mon = df[m].fillna("").str.strip().replace({"USD": "PROM", "0UF": "UF"})
        implicito = df[v] * 1e3 / df[u].where(df[u] > 0)
        ref = pd.Series(float("nan"), index=df.index)
        ref = ref.mask(mon == "PROM", df.usd).mask(mon == "UF", df.uf).mask(mon == "$$", 1.0)
        # Monedas sin referencia cargada (EUR, GBP…): mediana del mismo mes y moneda.
        med = implicito.groupby([df.period, mon]).transform("median")
        ref = ref.fillna(med)
        ok = nuevo & ((implicito / ref - 1).abs() < TOL_PRECIO)
        df[f"mon_{lado}"] = mon
        df[f"val_{lado}"] = df[v].where(ok)          # M$, solo si la pata es coherente
        df[f"ok_{lado}"] = ok
    ext_l = ~df.mon_l.isin(LOCALES)
    ext_c = ~df.mon_c.isin(LOCALES)
    df["valido"] = df.ok_l | df.ok_c
    df["me_m"] = df.val_l.fillna(0).where(ext_l, 0) - df.val_c.fillna(0).where(ext_c, 0)
    df["me_usd"] = df.me_m * 1e3 / df.usd / 1e6           # millones de USD
    df["bruto_m"] = df[["val_l", "val_c"]].max(axis=1).fillna(0)
    df["moneda_ext"] = df.mon_c.where(ext_c, df.mon_l.where(ext_l, "local")).replace({"PROM": "USD"})
    return df


def resumen_seguros(db, meses: int = 57) -> dict:
    ult = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_derivatives").fetchone()[0]
    if not ult:
        return {}
    t = pd.Period(f"{ult // 100}-{ult % 100:02d}", "M") - (meses - 1)
    df = _frame(db, t.year * 100 + t.month)
    per = sorted(df.period.unique())
    neto = df.groupby("period").me_usd.sum()
    bcch = dict(db.conn.execute(
        "SELECT year(date) * 100 + month(date), value FROM observations WHERE series_id = ?",
        [BCCH_SEGUROS_NETO]).fetchall())
    vr = df[df.tipo.isin(["forward", "swap", "opción"])].groupby("period").valor_razonable_m.sum()
    serie = {"meses": [f"{p // 100}-{p % 100:02d}" for p in per],
             "neto_me_usd": [round(float(neto.get(p, 0.0)), 1) if p >= DESDE_LAYOUT_NUEVO else None for p in per],
             "bcch": [None if bcch.get(p) is None else round(float(bcch[p]), 1) for p in per],
             "vr_mmm": [round(float(vr.get(p, 0.0)) / 1e6, 1) for p in per],
             "contratos": [int((df.period == p).sum()) for p in per]}

    u = df[df.period == ult]
    fw = u[u.tipo.isin(["forward", "swap"])]
    validos = {"pct": float(fw.valido.mean() * 100) if len(fw) else None,
               "excluidos": int((~fw.valido).sum()), "total": int(len(fw))}
    vr_cia = u.groupby(["rut", "insurance_type"]).valor_razonable_m.sum()   # todos los contratos
    u = u[u.valido | ~u.tipo.isin(["forward", "swap"])]
    from api.insurer_glossary import clean_company_name
    nombres = dict(db.conn.execute("""SELECT rut, arg_max(company_name, period) FROM cmf_insurer_portfolio_control
                                      WHERE company_name <> '' GROUP BY 1""").fetchall())
    g = (u.groupby(["rut", "insurance_type"])
          .agg(contratos=("tipo", "size"), swaps=("tipo", lambda s: int((s == "swap").sum())),
               forwards=("tipo", lambda s: int((s == "forward").sum())),
               otros=("tipo", lambda s: int((~s.isin(["swap", "forward"])).sum())),
               bruto=("bruto_m", "sum"), me_usd=("me_usd", "sum"), vr=("valor_razonable_m", "sum"),
               cobertura=("objetivo", lambda s: float((s != "INV").mean() * 100)),
               exterior=("tipo_contraparte", lambda s: float(s.isin(["04", "05", "06"]).mean() * 100)))
          .reset_index().sort_values("bruto", ascending=False))
    companias = [{"rut": r.rut, "nombre": clean_company_name(nombres.get(r.rut, ""), r.rut),
                  "ramo": r.insurance_type, "contratos": int(r.contratos), "swaps": int(r.swaps),
                  "forwards": int(r.forwards), "otros": int(r.otros),
                  "bruto_mm": float(r.bruto) / 1e3, "me_usd": float(r.me_usd),
                  "vr_mm": float(vr_cia.get((r.rut, r.insurance_type), 0.0)) / 1e3,
                  "pct_cobertura": float(r.cobertura), "pct_exterior": float(r.exterior)}
                 for r in g.itertuples()]
    por_tipo = (u.groupby(["tipo", "moneda_ext"]).agg(contratos=("tipo", "size"), bruto=("bruto_m", "sum"),
                                                      me_usd=("me_usd", "sum"))
                 .reset_index().sort_values("bruto", ascending=False))
    contrapartes = (u.assign(tc=u.tipo_contraparte.map(TIPO_CONTRAPARTE).fillna("sin dato"))
                     .groupby("tc").bruto_m.sum().sort_values(ascending=False))
    tot = float(contrapartes.sum()) or 1.0
    return {"ultimo": f"{ult // 100}-{ult % 100:02d}", "serie": serie, "companias": companias,
            "validos": validos, "desde_posiciones": f"{DESDE_LAYOUT_NUEVO // 100}-{DESDE_LAYOUT_NUEVO % 100:02d}",
            "por_tipo": [{"tipo": r.tipo, "moneda": r.moneda_ext, "contratos": int(r.contratos),
                          "bruto_mm": float(r.bruto) / 1e3, "me_usd": float(r.me_usd)}
                         for r in por_tipo.itertuples()],
            "contrapartes": [{"tipo": k, "pct": float(v) / tot * 100} for k, v in contrapartes.items()],
            "objetivos": {k: int(v) for k, v in u.objetivo.fillna("—").value_counts().items()}}
