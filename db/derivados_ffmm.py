"""
db/derivados_ffmm.py — Derivados de los fondos mutuos, fondo por fondo (PLAN_DERIVADOS fase 2).

Fuente: archivos FUTU (forwards, swaps, futuros) y OPCI (opciones) de la cartera mensual de
fondos mutuos de la CMF, tipados en la vista `v_mutual_fund_derivatives` (migración 011).

- Montos en MM$: `monto_miles` (miles de la moneda del FONDO) / 1.000, y los fondos en
  dólares × dólar observado del cierre del mes.
- Neto USD: Σ (C = +1, V = −1) × nocional en dólares de los contratos con subyacente USD
  (forwards y futuros). Se compara con el sector AGF del Banco Central, que incluye además
  a los fondos de inversión: debe calzar en signo y orden de magnitud, no en nivel.
"""

from __future__ import annotations

import pandas as pd

BCCH_AGF_NETO = "F099.DER.STO.Z.38.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M"


def _base(db, desde: int) -> pd.DataFrame:
    return db.conn.execute("""
        WITH cur AS (SELECT fund_run, arg_max(currency, date) currency, arg_max(agf_name, date) agf
                     FROM cmf_mutual_fund_quota_values GROUP BY 1),
        fx AS (SELECT DISTINCT d.period,
                 (SELECT arg_max(o.value, o.date) FROM observations o
                  WHERE o.series_id = 'F073.TCO.PRE.Z.D'
                    AND o.date <= last_day(make_date(d.period // 100, d.period % 100, 1))) usd
               FROM v_mutual_fund_derivatives d WHERE d.period >= ?)
        SELECT d.*, cur.currency, cur.agf, fx.usd,
               d.monto_miles / 1e3 * CASE WHEN cur.currency = 'DOLAR' THEN fx.usd ELSE 1 END AS monto_mm,
               CASE d.direccion WHEN 'C' THEN 1 WHEN 'V' THEN -1 ELSE 0 END AS signo
        FROM v_mutual_fund_derivatives d LEFT JOIN cur USING (fund_run) LEFT JOIN fx USING (period)
        WHERE d.period >= ?
    """, [desde, desde]).fetchdf()


def resumen_ffmm(db, meses: int = 27) -> dict:
    ult = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_derivatives").fetchone()[0]
    if not ult:
        return {}
    t = pd.Period(f"{ult // 100}-{ult % 100:02d}", "M") - (meses - 1)
    df = _base(db, t.year * 100 + t.month)
    if df.empty:
        return {}

    usd = df[(df.subyacente == "USD") & df.tipo.isin(["forward", "futuro"])]
    neto = (usd.assign(x=usd.signo * usd.nocional_unidades).groupby("period")["x"].sum() / 1e6)
    periodos = sorted(df.period.unique())
    bcch = dict(db.conn.execute(
        "SELECT year(date) * 100 + month(date), value FROM observations WHERE series_id = ?",
        [BCCH_AGF_NETO]).fetchall())
    serie = {"meses": [f"{p // 100}-{p % 100:02d}" for p in periodos],
             "neto_usd": [round(float(neto.get(p, 0.0)), 1) for p in periodos],
             "bcch_agf": [None if bcch.get(p) is None else round(float(bcch[p]), 1) for p in periodos],
             "fondos": [int(df[df.period == p].fund_run.nunique()) for p in periodos]}

    u = df[df.period == ult]
    tabla = (u.assign(compra=u.monto_mm.where(u.signo > 0, 0.0), venta=u.monto_mm.where(u.signo < 0, 0.0))
               .groupby(["subyacente", "tipo"])
               .agg(contratos=("fund_run", "size"), fondos=("fund_run", "nunique"),
                    compra=("compra", "sum"), venta=("venta", "sum"))
               .reset_index())
    tabla["bruto"] = tabla.compra + tabla.venta
    tabla = tabla.sort_values("bruto", ascending=False)

    nav = dict(db.conn.execute("""
        SELECT fund_run, SUM(net_asset_value) FROM cmf_mutual_fund_quota_values
        WHERE date = (SELECT MAX(date) FROM cmf_mutual_fund_quota_values
                      WHERE year(date) * 100 + month(date) = ?)
        GROUP BY 1""", [int(ult)]).fetchall())
    g = (u.assign(neto_usd=(u.signo * u.nocional_unidades).where(
            (u.subyacente == "USD") & u.tipo.isin(["forward", "futuro"]), 0.0) / 1e6)
          .groupby(["fund_run", "fund_name", "agf", "currency"])
          .agg(bruto=("monto_mm", "sum"), neto_usd=("neto_usd", "sum"), contratos=("monto_mm", "size"),
               tipos=("subyacente", lambda s: ", ".join(sorted(set(s)))))
          .reset_index().sort_values("bruto", ascending=False))
    fondos = []
    for r in g.head(20).itertuples():
        pat = nav.get(r.fund_run)
        pat_mm = (pat * (u.usd.iloc[0] if r.currency == "DOLAR" else 1)) if pat else None
        fondos.append({"run": r.fund_run, "fondo": str(r.fund_name).title(),
                       "agf": " ".join(str(r.agf or "").split()).replace("ADMINISTRADORA GENERAL DE FONDOS", "AGF").title(),
                       "bruto": float(r.bruto), "neto_usd": float(r.neto_usd), "contratos": int(r.contratos),
                       "tipos": r.tipos, "veces_patrimonio": (float(r.bruto) / pat_mm) if pat_mm else None})

    inversion = u[u.subyacente.isin(["índices bursátiles", "acciones y ETF", "bonos y tasas"])]
    return {"ultimo": f"{ult // 100}-{ult % 100:02d}", "serie": serie,
            "tabla": [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()}
                      for r in tabla.to_dict("records")],
            "fondos": fondos,
            "n_fondos": int(u.fund_run.nunique()),
            "inversion": {"contratos": len(inversion), "fondos": int(inversion.fund_run.nunique()),
                          "monto": float(inversion.monto_mm.sum())}}
