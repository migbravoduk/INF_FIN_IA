"""
db/derivados.py — Monitor de posiciones institucionales en derivados (docs/PLAN_DERIVADOS.md).

Fuente principal: Banco Central, familia F099.DER (montos vigentes y transados por sector,
mensual desde ene-2022, categoría `derivados_sector` del catálogo). Fondos mutuos y seguros
por entidad viven en `db/derivados_ffmm.py` y `db/derivados_seguros.py`.

Convenciones (verificadas, ver el plan):
- Neto = comprador − vendedor. En monedas, "comprador" compra la moneda extranjera (USD) a
  plazo: neto negativo = vendedor de dólares = protegido contra una caída del dólar.
- En swaps de tasa, "compra de tasa variable" = recibe variable y paga fija (Informe Mensual
  del Mercado de Derivados del BCCh: positivo = posición neta compradora de tasa variable).
  Neto negativo = recibe fija: gana si las tasas bajan, pierde si suben.
- Son NOCIONALES: dirección y tamaño, no valor de mercado ni pérdida.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

P = "F099.DER.STO.Z."
F = "F099.DER.FLU.Z."
EXT = "SWP.MMUSD.EXT.Z.Z.0.M"

S = {
    # AFP · resumen (millones de USD)
    "total": P + "42.Z.Z.TOT.Z.MMUSD.Z.Z.Z.0.M",
    "tasa": P + "42.Z.Z.TOT.Z.MMUSD.IRT.Z.Z.0.M",
    "monedas": P + "42.Z.Z.TOT.Z.MMUSD.FXT.Z.Z.0.M",
    "inflacion": P + "42.Z.Z.TOT.Z.MMUSD.MLML.R.Z.0.M",
    "spc_usd": P + "42.Z.Z.TOT.SWP.MMUSD.SPC.R.Z.0.M",
    "mlme": P + "42.Z.Z.TOT.Z.MMUSD.MLME.Z.Z.0.M",
    "meme": P + "42.Z.Z.TOT.Z.MMUSD.MEME.Z.Z.0.M",
    # AFP · swaps sobre tasas extranjeras
    "ext_com": P + "42.Z.Z.COM." + EXT, "ext_vta": P + "42.Z.Z.VTA." + EXT,
    "ext_net": P + "42.Z.Z.NET." + EXT, "ext_tot": P + "42.Z.Z.TOT." + EXT,
    "ext_flu_net": F + "42.Z.Z.NET." + EXT, "ext_flu_tot": F + "42.Z.Z.TOT." + EXT,
    # AFP · swap cámara (miles de millones de $)
    "spc_tot": P + "42.Z.Z.TOT.SWP.MMMCLP.SPC.R.Z.0.M",
    "spc_net_c": P + "42.Z.Z.NET.SWP.MMMCLP.SPC.R.HA02.0.M",
    "spc_net_l": P + "42.Z.Z.NET.SWP.MMMCLP.SPC.R.MA02.0.M",
    "spc_com_c": P + "42.Z.Z.COM.SWP.MMMCLP.SPC.R.HA02.0.M",
    "spc_vta_c": P + "42.Z.Z.VTA.SWP.MMMCLP.SPC.R.HA02.0.M",
    "spc_com_l": P + "42.Z.Z.COM.SWP.MMMCLP.SPC.R.MA02.0.M",
    "spc_vta_l": P + "42.Z.Z.VTA.SWP.MMMCLP.SPC.R.MA02.0.M",
    "spc_tot_c": P + "42.Z.Z.TOT.SWP.MMMCLP.SPC.R.HA02.0.M",
    "spc_tot_l": P + "42.Z.Z.TOT.SWP.MMMCLP.SPC.R.MA02.0.M",
    # AFP · USD-CLP
    "usd_com": P + "42.Z.Z.COM.Z.MMUSD.CLPUSD.Z.Z.0.M", "usd_vta": P + "42.Z.Z.VTA.Z.MMUSD.CLPUSD.Z.Z.0.M",
    "usd_net": P + "42.Z.Z.NET.Z.MMUSD.CLPUSD.Z.Z.0.M", "usd_tot": P + "42.Z.Z.TOT.Z.MMUSD.CLPUSD.Z.Z.0.M",
    "usd_net_bancos": P + "42.R.40.NET.Z.MMUSD.CLPUSD.Z.Z.0.M",
    "usd_net_nr": P + "42.N.NR.NET.Z.MMUSD.CLPUSD.Z.Z.0.M",
    # AFP · UF-CLP (miles de UF)
    "uf_net": P + "42.Z.Z.NET.Z.MUF.MLML.Z.Z.0.M", "uf_tot": P + "42.Z.Z.TOT.Z.MUF.MLML.Z.Z.0.M",
    # Contexto
    "ust10": "F019.TBG.TAS.10.M",
}
PLAZOS_USD = [("P17", "≤7 d"), ("P835", "8–35 d"), ("P3695", "36–95 d"), ("P96185", "96–185 d"),
              ("P186370", "186–370 d"), ("MA01", ">370 d")]
PARES = ["USDEUR", "USDJPY", "USDGBP", "USDBRL", "USDMXN", "USDCNY", "OPM"]

# Neto de monedas por sector (millones de USD). Los sectores 44/38/39/50/55A informan
# "moneda extranjera vs. peso" con contraparte total; no residentes, solo contra bancos.
SECTORES = [
    ("AFP", P + "42.Z.Z.NET.Z.MMUSD.CLPUSD.Z.Z.0.M", "USD-CLP, todas las contrapartes"),
    ("Seguros", P + "44.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M", "moneda extranjera vs. peso"),
    ("AGF (fondos)", P + "38.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M", "moneda extranjera vs. peso"),
    ("Corredoras", P + "39.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M", "moneda extranjera vs. peso"),
    ("Empresas", P + "55A.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M", "moneda extranjera vs. peso"),
    ("No residentes", P + "NR.R.40.NET.Z.MMUSD.CLPUSD.Z.Z.0.M", "USD-CLP contra bancos locales"),
]

# Reglas de alerta fijadas ANTES de mirar el episodio de 2026 (ver el plan): no se ajustan
# para que lo marquen.
ALERTA_K = 3.0          # salto: |Δ neto / activos| > K desvíos robustos de su historia previa
ALERTA_MIN_HIST = 12    # meses de historia mínima para juzgar un salto
ALERTA_SIGNO_MIN = 0.01 # cambio de signo solo cuenta si |neto| > 1% de los activos


def _frame(db, ids: dict[str, str]) -> pd.DataFrame:
    """Series → DataFrame mensual (índice = primer día del mes, como las guarda el BCCh)."""
    df = db.conn.execute(f"""
        SELECT series_id, date, value FROM observations
        WHERE series_id IN ({",".join("?" * len(ids))})
    """, list(ids.values())).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=list(ids))
    inv = {v: k for k, v in ids.items()}
    df["k"] = df["series_id"].map(inv)
    w = df.pivot_table(index="date", columns="k", values="value", aggfunc="last")
    w.index = pd.to_datetime(w.index).to_period("M").to_timestamp()
    return w.reindex(columns=list(ids)).sort_index()


def activos_afp_usd(db, meses: pd.DatetimeIndex) -> pd.Series:
    """Patrimonio de los fondos de pensiones (millones de USD) al cierre de cada mes."""
    q = db.conn.execute("""
        SELECT date, SUM(equity_value) eq FROM sp_quota_values WHERE afp_name <> 'TOTAL'
        GROUP BY date ORDER BY date""").fetchdf()
    fx = db.conn.execute("""SELECT date, value FROM observations
                            WHERE series_id = 'F073.TCO.PRE.Z.D' ORDER BY date""").fetchdf()
    if q.empty or fx.empty:
        return pd.Series(index=meses, dtype=float)
    q["date"] = pd.to_datetime(q["date"]); fx["date"] = pd.to_datetime(fx["date"])
    # Días donde no informaron todas las AFP suman de menos: se usa el último día "completo".
    n = db.conn.execute("""SELECT date, COUNT(DISTINCT afp_name) n FROM sp_quota_values
                           WHERE afp_name <> 'TOTAL' GROUP BY date""").fetchdf()
    n["date"] = pd.to_datetime(n["date"])
    q = q.merge(n, on="date")
    q = q[q["n"] == q["n"].max()]
    out = {}
    for m in meses:
        fin = m + pd.offsets.MonthEnd(0)
        a = q[q["date"] <= fin].tail(1)
        f = fx[fx["date"] <= fin].tail(1)
        if len(a) and len(f) and (fin - a["date"].iloc[0]).days <= 10:
            out[m] = float(a["eq"].iloc[0]) / float(f["value"].iloc[0]) / 1e6
    return pd.Series(out).reindex(meses)


def identidades(db) -> list[dict]:
    """
    Cuadraturas internas de las series de AFP (también se testean en tests/test_derivados.py).
    Devuelve, por identidad, el desvío relativo máximo en toda la historia.
    """
    w = _frame(db, S)
    reglas = [
        ("total = tasa + monedas + inflación", w["total"], w["tasa"] + w["monedas"] + w["inflacion"]),
        ("tasa = swap cámara + tasas extranjeras", w["tasa"], w["spc_usd"] + w["ext_tot"]),
        ("monedas = local/extranjera + pares extranjeros", w["monedas"], w["mlme"] + w["meme"]),
        ("tasas extranjeras: total = comprador + vendedor", w["ext_tot"], w["ext_com"] + w["ext_vta"]),
        ("tasas extranjeras: neto = comprador − vendedor", w["ext_net"], w["ext_com"] - w["ext_vta"]),
        ("USD-CLP: neto = comprador − vendedor", w["usd_net"], w["usd_com"] - w["usd_vta"]),
        ("swap cámara ≤2a: neto = comprador − vendedor", w["spc_net_c"], w["spc_com_c"] - w["spc_vta_c"]),
        ("swap cámara: total = ≤2a + >2a", w["spc_tot"], w["spc_tot_c"] + w["spc_tot_l"]),
    ]
    out = []
    for nombre, a, b in reglas:
        ok = pd.concat([a, b], axis=1).dropna()
        if ok.empty:
            out.append({"regla": nombre, "meses": 0, "desvio_max": None}); continue
        den = ok.iloc[:, 0].abs().clip(lower=1.0)
        dev = float(((ok.iloc[:, 0] - ok.iloc[:, 1]).abs() / den).max())
        out.append({"regla": nombre, "meses": len(ok), "desvio_max": dev})
    return out


def _alertas(serie: pd.Series, etiqueta: str) -> list[dict]:
    """
    Alertas sobre una serie de neto/activos (fracción). Fuera de muestra: cada mes se juzga
    solo con la historia anterior. Reglas fijas: ALERTA_K desvíos robustos (1,4826·MAD) del
    cambio mensual, con ALERTA_MIN_HIST meses previos; y cambio de signo con |neto| relevante.
    """
    s = serie.dropna()
    d = s.diff()
    out = []
    for i in range(1, len(s)):
        mes = s.index[i]
        hist = d.iloc[1:i]
        if len(hist) >= ALERTA_MIN_HIST:
            med = hist.median()
            mad = 1.4826 * (hist - med).abs().median()
            if mad > 0 and abs(d.iloc[i] - med) > ALERTA_K * mad:
                out.append({"mes": mes, "tipo": "salto", "serie": etiqueta,
                            "texto": f"{etiqueta}: el neto cambió {d.iloc[i] * 100:+.1f}% de los activos en un mes "
                                     f"({abs(d.iloc[i] - med) / mad:.0f} desvíos de su historia)"})
        if (np.sign(s.iloc[i]) != np.sign(s.iloc[i - 1]) and abs(s.iloc[i]) > ALERTA_SIGNO_MIN):
            out.append({"mes": mes, "tipo": "signo", "serie": etiqueta,
                        "texto": f"{etiqueta}: el neto cambió de signo ({s.iloc[i - 1] * 100:+.1f}% → "
                                 f"{s.iloc[i] * 100:+.1f}% de los activos)"})
    return out


def hitos() -> dict:
    import yaml
    from config.settings import config_path
    p = config_path("hitos_derivados.yaml")
    if not p.exists():
        return {"hitos": [], "plazo_cierre": None}
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {"hitos": d.get("hitos") or [], "plazo_cierre": d.get("plazo_cierre")}


def monitor_afp(db) -> dict:
    """Bloque 1: nocional/activos, dirección neta, saldo vs. flujo, contexto y alertas."""
    w = _frame(db, S)
    if w.empty or w["total"].dropna().empty:
        return {"meses": [], "ultimo": None}
    w = w[w.index >= "2022-01-01"]
    act = activos_afp_usd(db, w.index)
    # Swap cámara viene en miles de millones de pesos: a millones de USD con el total en USD
    # que el propio BCCh publica (spc_usd / spc_tot), sin elegir un tipo de cambio aparte.
    k_spc = (w["spc_usd"] / w["spc_tot"]).replace([np.inf, -np.inf], np.nan)
    spc_net_usd = (w["spc_net_c"] + w["spc_net_l"]) * k_spc

    df = pd.DataFrame({
        "activos": act,
        "bruto_tasa": w["tasa"], "bruto_ext": w["ext_tot"], "bruto_spc": w["spc_usd"],
        "ext_com": w["ext_com"], "ext_vta": w["ext_vta"], "ext_net": w["ext_net"],
        "ext_flu_net": w["ext_flu_net"], "spc_net": spc_net_usd,
        "ust10": w["ust10"],
    })
    df["ratio_bruto"] = df["bruto_tasa"] / df["activos"]
    df["ratio_ext_net"] = df["ext_net"] / df["activos"]
    df["ratio_spc_net"] = df["spc_net"] / df["activos"]
    # Saldo vs. flujo: Δ saldo neto − flujo neto del mes ≈ lo que venció o se cerró.
    df["d_ext_net"] = df["ext_net"].diff()
    df["vencido_ext"] = df["d_ext_net"] - df["ext_flu_net"]

    alertas = (_alertas(df["ratio_ext_net"], "Swaps sobre tasas extranjeras")
               + _alertas(df["ratio_spc_net"], "Swap cámara"))
    # Máximo histórico del bruto/activos (tras 12 meses de historia)
    rb = df["ratio_bruto"].dropna()
    for i in range(ALERTA_MIN_HIST, len(rb)):
        if rb.iloc[i] > rb.iloc[:i].max() * 1.10 and rb.iloc[i] > 1.0:
            alertas.append({"mes": rb.index[i], "tipo": "bruto", "serie": "Nocional de tasa",
                            "texto": f"Nocional bruto de swaps de tasa en máximo: {rb.iloc[i]:.2f}× los activos "
                                     f"(+{(rb.iloc[i] / rb.iloc[:i].max() - 1) * 100:.0f}% sobre el máximo previo)"})
    alertas.sort(key=lambda a: a["mes"])

    ult = df["ext_net"].last_valid_index()
    h = hitos()
    plazo = pd.Timestamp(h["plazo_cierre"]) if h["plazo_cierre"] else None
    if plazo is None or ult is None:
        cierre = None
    else:
        ult_fin = ult + pd.offsets.MonthEnd(0)
        if ult_fin < plazo:
            cierre = {"estado": "pendiente", "ultimo": ult.strftime("%Y-%m"), "plazo": plazo.strftime("%d-%m-%Y"),
                      "texto": f"El último dato del Banco Central es de {ult:%m/%Y}, anterior al plazo de cierre "
                               f"({plazo:%d-%m-%Y}). El dato de {plazo:%m/%Y} se publica ~2 meses después."}
        else:
            r = df.loc[ult, "ratio_ext_net"]
            cerrado = abs(r) < ALERTA_SIGNO_MIN
            cierre = {"estado": "cerrado" if cerrado else "abierto", "ultimo": ult.strftime("%Y-%m"),
                      "plazo": plazo.strftime("%d-%m-%Y"),
                      "texto": (f"Al {ult:%m/%Y} el neto en tasas extranjeras es {r * 100:+.1f}% de los activos: "
                                + ("la posición se cerró." if cerrado else "la posición sigue abierta después del plazo."))}

    def fila(m):
        r = df.loc[m]
        return {k: (None if pd.isna(v) else float(v)) for k, v in r.items()}

    return {
        "meses": [m.strftime("%Y-%m") for m in df.index],
        "serie": {c: [None if pd.isna(v) else float(v) for v in df[c]] for c in df.columns},
        "ultimo": ult.strftime("%Y-%m") if ult is not None else None,
        "ult": fila(ult) if ult is not None else {},
        "hace12": fila(ult - pd.DateOffset(months=12)) if ult is not None and (ult - pd.DateOffset(months=12)) in df.index else {},
        "alertas": [{**a, "mes": a["mes"].strftime("%Y-%m")} for a in alertas],
        "cierre": cierre,
        "hitos": [{**x, "fecha": str(x["fecha"])} for x in h["hitos"]],
    }


def retornos_afp(db, fondos=("E", "D"), desde: str = "2022-01-01") -> dict:
    """Retorno mensual (%) por AFP y fondo, y dispersión entre AFP (desvío estándar, pp)."""
    q = db.conn.execute("""SELECT date, afp_name, fund_type, quota_value FROM sp_quota_values
                           WHERE afp_name <> 'TOTAL' AND date >= ? AND fund_type IN (SELECT unnest(?))""",
                        [pd.Timestamp(desde) - pd.Timedelta(days=40), list(fondos)]).fetchdf()
    if q.empty:
        return {}
    q["date"] = pd.to_datetime(q["date"])
    corte = pd.Timestamp(db.sp_common_date() or q["date"].max())
    q = q[q["date"] <= corte]
    out = {}
    for f in fondos:
        w = q[q.fund_type == f].pivot_table(index="date", columns="afp_name", values="quota_value")
        m = w.resample("ME").last()
        # El último mes queda parcial si el corte no es fin de mes: se marca.
        r = (m.pct_change() * 100).iloc[1:]
        r = r[r.index >= desde]
        out[f] = {"meses": [i.strftime("%Y-%m") for i in r.index],
                  "afp": {a: [None if pd.isna(v) else round(float(v), 3) for v in r[a]] for a in r.columns},
                  "dispersion": [None if pd.isna(v) else round(float(v), 3) for v in r.std(axis=1)],
                  "parcial": corte < corte + pd.offsets.MonthEnd(0)}
    return out


def sectores_usd(db) -> dict:
    """Bloque 2: neto de monedas por sector (millones de USD)."""
    w = _frame(db, {n: i for n, i, _ in SECTORES})
    w = w[w.index >= "2022-01-01"]
    return {"meses": [m.strftime("%Y-%m") for m in w.index],
            "sectores": [{"nombre": n, "nota": nota,
                          "valores": [None if pd.isna(v) else float(v) for v in w[n]],
                          "ultimo": None if w[n].dropna().empty else float(w[n].dropna().iloc[-1]),
                          "hace12": (float(w[n].dropna().iloc[-13]) if len(w[n].dropna()) >= 13 else None)}
                         for n, _, nota in SECTORES]}


def detalle_afp(db) -> dict:
    """Bloque 3: dólar-peso (dirección, plazos, contraparte), cobertura, UF, cámara, pares."""
    ids = {**S, **{f"plazo_{c}": P + f"42.Z.Z.NET.Z.MMUSD.CLPUSD.R.{c}.0.M" for c, _ in PLAZOS_USD},
           **{f"par_{c}": P + f"42.Z.Z.TOT.Z.MMUSD.{c}.Z.Z.0.M" for c in PARES}}
    w = _frame(db, ids)
    w = w[w.index >= "2022-01-01"]
    if w.empty:
        return {}
    ult = w["usd_net"].last_valid_index()
    r = w.loc[ult]
    # Cobertura: venta neta de USD / inversión extranjera (cartera SP, millones de USD).
    ext = db.conn.execute("""
        SELECT period, SUM(monto_dolares) FROM sp_portfolio_holdings
        WHERE afp_name = 'TOTAL' AND instrument_glosa = 'TOTAL EXTRANJERO' GROUP BY 1 ORDER BY 1""").fetchall()
    ext_s = pd.Series({pd.Timestamp(p + "-01"): float(v) for p, v in ext if v})
    cob = (-w["usd_net"] / ext_s.reindex(w.index)).dropna() * 100
    return {
        "ultimo": ult.strftime("%Y-%m"),
        "usd": {"com": r["usd_com"], "vta": r["usd_vta"], "net": r["usd_net"],
                "net_bancos": r["usd_net_bancos"], "net_nr": r["usd_net_nr"]},
        "plazos": [{"plazo": lab, "net": None if pd.isna(r[f"plazo_{c}"]) else float(r[f"plazo_{c}"])}
                   for c, lab in PLAZOS_USD],
        "cobertura": {"meses": [m.strftime("%Y-%m") for m in cob.index],
                      "pct": [round(float(v), 1) for v in cob],
                      "ultimo": round(float(cob.iloc[-1]), 1) if len(cob) else None,
                      "mes": cob.index[-1].strftime("%Y-%m") if len(cob) else None},
        "uf": {"net": r["uf_net"], "tot": r["uf_tot"]},
        "spc": {"net_c": r["spc_net_c"], "net_l": r["spc_net_l"], "tot": r["spc_tot"]},
        "pares": [{"par": c.replace("USD", "USD-") if c != "OPM" else "Otros pares",
                   "tot": None if pd.isna(r[f"par_{c}"]) else float(r[f"par_{c}"])} for c in PARES],
        "serie_usd": {"meses": [m.strftime("%Y-%m") for m in w.index],
                      "com": [None if pd.isna(v) else float(v) for v in w["usd_com"]],
                      "vta": [None if pd.isna(v) else -float(v) for v in w["usd_vta"]],
                      "net": [None if pd.isna(v) else float(v) for v in w["usd_net"]]},
    }
