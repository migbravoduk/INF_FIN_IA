"""
db/derivados_global.py — Derivados de toda la economía: mercado, industrias, riesgos y
coberturas por entidad. Vista /derivados (pestaña propia; el monitor de AFP vive en
/inversion-institucional/derivados).

Fuentes
- Banco Central, F099.DER (mensual desde ene-2022, ~2 meses de rezago). Los BANCOS reportan
  su posición frente a cada sector contraparte (AGF, corredoras, AFP, seguros, otros,
  empresas, no residentes). La posición del sector es la de SIGNO CONTRARIO a la del banco
  frente a él; se verificó que coincide exacto con lo que el propio sector reporta frente a
  los bancos (AFP, seguros, AGF, empresas). Identidades: Σ sectores residentes = "residentes
  no bancos" (R.63 / N.63) y neto bancario = residentes no bancos + no residentes (el
  interbancario neta cero). Ver `identidades()`.
- Por entidad: SP cartera desagregada (AFP × fondo), CMF B.7 (seguros por compañía), CMF
  FUTU/OPCI (fondos mutuos por fondo).

Convenciones
- Neto = comprador − vendedor de la moneda extranjera (o de la UF, o de la tasa variable).
  Neto negativo en dólar-peso = vende dólares a plazo: gana si el dólar cae, pierde si sube.
- Son NOCIONALES (tamaño y dirección): la sensibilidad a un shock lineal (forwards) es
  nocional × shock. Las de tasas se aproximan con el plazo original del contrato (cota
  superior: el plazo residual es menor).
"""

from __future__ import annotations

import pandas as pd

from db.derivados import _frame

P = "F099.DER.STO.Z."

# Sectores contraparte de los bancos (código en la serie del banco, etiqueta)
SECTORES = [("R.42", "AFP"), ("R.44", "Seguros"), ("R.38", "AGF (fondos)"), ("R.39", "Corredoras"),
            ("R.50", "Otros sectores"), ("R.55A", "Empresas"), ("N.NR", "No residentes")]
SHOCK_FX = 0.10        # depreciación del peso de 10%
SHOCK_UF = 0.01        # 1 punto de inflación no anticipada
# Plazo original de los swaps cámara (código → años) para aproximar la sensibilidad.
PLAZO_SPC = [("ME03", "3 meses", 0.25), ("ME06", "6 meses", 0.5), ("ME09", "9 meses", 0.75),
             ("ME12", "12 meses", 1.0), ("ME18", "18 meses", 1.5), ("AN02", "2 años", 2.0),
             ("AN05", "5 años", 4.6), ("MA10", "10 años o más", 8.5)]
PLAZO_FX = [("P17", "≤ 7 días"), ("P830", "8–30 días"), ("P3190", "31–90 días"),
            ("P91180", "91–180 días"), ("P181360", "181–360 días"), ("P361720", "1–2 años"),
            ("A2A5", "2–5 años"), ("A5A10", "5–10 años"), ("MA10", "> 10 años")]


def _mes(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m")


def _lista(s: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 1) for v in s]


def _ult(w: pd.DataFrame, col: str):
    s = w[col].dropna() if col in w else pd.Series(dtype=float)
    return float(s.iloc[-1]) if len(s) else None


def _hace12(w: pd.DataFrame, col: str):
    s = w[col].dropna() if col in w else pd.Series(dtype=float)
    return float(s.iloc[-13]) if len(s) >= 13 else None


def _precios(db, mes: pd.Timestamp) -> dict:
    """Dólar observado y UF al cierre del mes."""
    fin = (mes + pd.offsets.MonthEnd(0)).date()
    out = {}
    for k, sid in (("usd", "F073.TCO.PRE.Z.D"), ("uf", "F073.UFF.PRE.Z.D")):
        r = db.conn.execute("SELECT arg_max(value, date) FROM observations WHERE series_id = ? AND date <= ?",
                            [sid, fin]).fetchone()
        out[k] = float(r[0]) if r and r[0] else None
    return out


# ----------------------------------------------------------------------------------------
# 1 · Mercado: tamaño y composición (posiciones vigentes de los bancos, ambos lados)
# ----------------------------------------------------------------------------------------
def mercado(db) -> dict:
    ids = {"total": P + "40.Z.Z.TOT.Z.MMUSD.Z.Z.Z.0.M",
           "monedas": P + "40.Z.Z.TOT.Z.MMUSD.FXT.Z.Z.0.M",
           "tasas": P + "40.Z.Z.TOT.Z.MMUSD.IRT.Z.Z.0.M",
           "inflacion": P + "40.Z.Z.TOT.Z.MMUSD.MLML.R.Z.0.M",
           "spc": P + "40.Z.Z.TOT.SWP.MMUSD.SPC.Z.Z.0.M",
           "spcr": P + "40.Z.Z.TOT.SWP.MMUSD.SPCR.Z.Z.0.M",
           "ois": P + "40.Z.Z.TOT.SWP.MMUSD.EOIS.Z.Z.0.M",
           "efv": P + "40.Z.Z.TOT.SWP.MMUSD.EFV.Z.Z.0.M",
           "basis": P + "40.Z.Z.TOT.SWP.MMUSD.EBS.Z.Z.0.M",
           "fwd": P + "40.R.Z.TOT.FWD.MMUSD.MLME.Z.Z.0.M",
           "ccs": P + "40.R.Z.TOT.CCS.MMUSD.MLME.Z.Z.0.M",
           "otr": P + "40.R.Z.TOT.OTR.MMUSD.MLME.Z.Z.0.M",
           "meme": P + "40.Z.Z.TOT.Z.MMUSD.MEME.Z.Z.0.M",
           "mlme": P + "40.Z.Z.TOT.Z.MMUSD.MLME.Z.Z.0.M",
           "interb_mlme": P + "40.R.40.TOT.Z.MMUSD.MLME.Z.Z.0.M",
           "nr_mlme": P + "40.N.NR.TOT.Z.MMUSD.MLME.Z.Z.0.M",
           "res_mlme": P + "40.R.63.TOT.Z.MMUSD.MLME.Z.Z.0.M"}
    w = _frame(db, ids)
    w = w[w.index >= "2022-01-01"]
    if w.empty or w["total"].dropna().empty:
        return {}
    u = w["total"].last_valid_index()
    r = {k: _ult(w, k) for k in ids}
    h = {k: _hace12(w, k) for k in ids}
    otras_tasas = (r["tasas"] or 0) - sum(r[k] or 0 for k in ("spc", "spcr", "ois", "efv", "basis"))
    tasas = [("Swap cámara en pesos", r["spc"]), ("OIS sobre tasas extranjeras", r["ois"]),
             ("Swap cámara en UF", r["spcr"]), ("Fijo-variable sobre tasas extranjeras", r["efv"]),
             ("Basis sobre tasas extranjeras", r["basis"]), ("Otros de tasa", otras_tasas)]
    return {
        "ultimo": _mes(u),
        "total": r["total"], "total_12": h["total"],
        "clases": [{"clase": "Tasas de interés", "monto": r["tasas"], "hace12": h["tasas"]},
                   {"clase": "Monedas", "monto": r["monedas"], "hace12": h["monedas"]},
                   {"clase": "Inflación (UF-peso)", "monto": r["inflacion"], "hace12": h["inflacion"]}],
        "tasas": [{"tipo": t, "monto": v} for t, v in tasas if v],
        "instrumentos_fx": [{"tipo": "Forward", "monto": r["fwd"]},
                            {"tipo": "Cross currency swap", "monto": r["ccs"]},
                            {"tipo": "Otros (opciones, etc.)", "monto": r["otr"]}],
        "contraparte_fx": [{"tipo": "Residentes no bancos", "monto": r["res_mlme"]},
                           {"tipo": "No residentes", "monto": r["nr_mlme"]},
                           {"tipo": "Entre bancos", "monto": r["interb_mlme"]}],
        "serie": {"meses": [_mes(m) for m in w.index],
                  "tasas": _lista(w["tasas"]), "monedas": _lista(w["monedas"]),
                  "inflacion": _lista(w["inflacion"])},
    }


# ----------------------------------------------------------------------------------------
# 2 · Industrias: quién compra y quién vende (vista del sector = − la del banco)
# ----------------------------------------------------------------------------------------
def industrias(db) -> dict:
    ids = {}
    for cp, _ in SECTORES:
        for lado in ("COM", "VTA", "NET", "TOT"):
            ids[f"usd_{lado}_{cp}"] = P + f"40.{cp}.{lado}.Z.MMUSD.CLPUSD.Z.Z.0.M"
            ids[f"me_{lado}_{cp}"] = P + f"40.{cp}.{lado}.Z.MMUSD.MLME.Z.Z.0.M"
            ids[f"uf_{lado}_{cp}"] = P + f"40.{cp}.{lado}.Z.MUF.MLML.Z.Z.0.M"
    ids.update({"usd_NET_banco": P + "40.Z.Z.NET.Z.MMUSD.CLPUSD.Z.Z.0.M",
                "me_NET_banco": P + "40.Z.Z.NET.Z.MMUSD.MLME.Z.Z.0.M",
                "uf_NET_banco": P + "40.Z.Z.NET.Z.MUF.MLML.Z.Z.0.M",
                "ndf_nr": P + "40.N.NR.NET.NDF.MMUSD.CLPUSD.C.Z.0.M",
                "afp_activos": P + "42.Z.Z.TOT.Z.MMUSD.Z.Z.Z.0.M"})
    w = _frame(db, ids)
    w = w[w.index >= "2022-01-01"]
    if w.empty:
        return {}
    u = w["usd_NET_R.42"].last_valid_index()
    px = _precios(db, u)
    filas = []
    for cp, nombre in SECTORES:
        def v(k):
            return _ult(w, k)
        # vista del sector: su compra = venta del banco, su neto = − neto del banco
        usd_net = -v(f"usd_NET_{cp}") if v(f"usd_NET_{cp}") is not None else None
        me_net = -v(f"me_NET_{cp}") if v(f"me_NET_{cp}") is not None else None
        uf_net = -v(f"uf_NET_{cp}") if v(f"uf_NET_{cp}") is not None else None   # miles de UF
        h_usd = _hace12(w, f"usd_NET_{cp}")
        uf_mmusd = (uf_net * 1e3 * px["uf"] / px["usd"] / 1e6) if (uf_net is not None and px["uf"] and px["usd"]) else None
        filas.append({
            "sector": nombre, "cp": cp,
            "usd_compra": v(f"usd_VTA_{cp}"), "usd_venta": v(f"usd_COM_{cp}"), "usd_neto": usd_net,
            "usd_neto_12": -h_usd if h_usd is not None else None,
            "usd_bruto": v(f"usd_TOT_{cp}"),
            "me_neto": me_net, "me_bruto": v(f"me_TOT_{cp}"),
            "otras_me": (me_net - usd_net) if (me_net is not None and usd_net is not None) else None,
            "uf_neto_mill": uf_net / 1e3 if uf_net is not None else None,      # millones de UF
            "uf_neto_mmusd": uf_mmusd, "uf_bruto_mill": (v(f"uf_TOT_{cp}") or 0) / 1e3,
            # Sensibilidad (millones de USD): +10% dólar; +1 pp de inflación no anticipada
            "pyg_fx": usd_net * SHOCK_FX if usd_net is not None else None,
            "pyg_uf": uf_mmusd * SHOCK_UF if uf_mmusd is not None else None,
        })
    banco = {"usd_neto": _ult(w, "usd_NET_banco"), "me_neto": _ult(w, "me_NET_banco"),
             "uf_neto_mill": (_ult(w, "uf_NET_banco") or 0) / 1e3}
    banco["pyg_fx"] = banco["usd_neto"] * SHOCK_FX if banco["usd_neto"] is not None else None
    act = _ult(w, "afp_activos")
    return {
        "ultimo": _mes(u), "precios": px, "filas": filas, "banco": banco,
        "ndf_nr": _ult(w, "ndf_nr"),
        "shock": {"fx": SHOCK_FX * 100, "uf": SHOCK_UF * 100},
        "serie": {"meses": [_mes(m) for m in w.index],
                  "usd": {n: _lista(-w[f"usd_NET_{cp}"]) for cp, n in SECTORES},
                  "uf": {n: _lista(-w[f"uf_NET_{cp}"] / 1e3) for cp, n in SECTORES},
                  "banco_usd": _lista(w["usd_NET_banco"])},
        "afp_nocional_total": act,
    }


def identidades(db) -> list[dict]:
    """Chequeos que sostienen la lectura por sector (último mes, diferencia en millones)."""
    out = []
    for sub, unidad in (("CLPUSD", "MMUSD"), ("MLME", "MMUSD")):
        ids = {cp: P + f"40.{cp}.NET.Z.{unidad}.{sub}.Z.Z.0.M" for cp, _ in SECTORES}
        ids["R.63"] = P + f"40.R.63.NET.Z.{unidad}.{sub}.Z.Z.0.M"
        ids["Z.Z"] = P + f"40.Z.Z.NET.Z.{unidad}.{sub}.Z.Z.0.M"
        w = _frame(db, ids).dropna(how="all")
        if w.empty:
            continue
        r = w.iloc[-1]
        res = sum(r[cp] for cp, _ in SECTORES if cp != "N.NR")
        out.append({"chequeo": f"{sub}: Σ sectores residentes = residentes no bancos",
                    "dif": float(res - r["R.63"])})
        out.append({"chequeo": f"{sub}: residentes no bancos + no residentes = neto bancario",
                    "dif": float(r["R.63"] + r["N.NR"] - r["Z.Z"])})
    return out


# ----------------------------------------------------------------------------------------
# 3 · Tasas: swap cámara por plazo original y sensibilidad aproximada (DV01)
# ----------------------------------------------------------------------------------------
def tasas(db) -> dict:
    ids = {}
    for g in ("Z.Z", "R.63", "N.NR"):
        for c, _, _ in PLAZO_SPC:
            ids[f"{g}_{c}_NET"] = P + f"40.{g}.NET.SWP.MMMCLP.SPC.R.{c}.0.M"
            ids[f"{g}_{c}_TOT"] = P + f"40.{g}.TOT.SWP.MMMCLP.SPC.R.{c}.0.M"
    ids.update({"afp_ext_net": P + "42.Z.Z.NET.SWP.MMUSD.EXT.Z.Z.0.M",
                "afp_ext_tot": P + "42.Z.Z.TOT.SWP.MMUSD.EXT.Z.Z.0.M",
                "ois_net": P + "40.Z.Z.NET.SWP.MMUSD.EOIS.Z.Z.0.M",
                "efv_net": P + "40.Z.Z.NET.SWP.MMUSD.EFV.Z.Z.0.M"})
    w = _frame(db, ids)
    w = w[w.index >= "2022-01-01"]
    if w.empty:
        return {}
    u = w["Z.Z_ME03_TOT"].last_valid_index() or w.index[-1]
    px = _precios(db, u)
    filas, dv = [], {"R.63": 0.0, "N.NR": 0.0, "Z.Z": 0.0}
    for c, lab, anios in PLAZO_SPC:
        fila = {"plazo": lab, "anios": anios, "bruto": _ult(w, f"Z.Z_{c}_TOT")}
        for g in ("R.63", "N.NR", "Z.Z"):
            n = _ult(w, f"{g}_{c}_NET")
            # Vista del sector = − banco. Para los bancos (Z.Z) se deja su propia posición.
            n_vista = (-n if g != "Z.Z" else n) if n is not None else None
            fila[g] = n_vista
            # DV01 ≈ nocional × duración × 1 pb; MMM$ → millones de $ (×1.000).
            if n_vista is not None:
                dv[g] += n_vista * anios * 1e-4 * 1e3
        filas.append(fila)
    # Neto comprador de tasa variable (recibe variable, paga fija) gana si las tasas suben.
    return {
        "ultimo": _mes(u), "usd": px["usd"], "filas": filas,
        "dv01": {"residentes": dv["R.63"], "no_residentes": dv["N.NR"], "bancos": dv["Z.Z"]},
        "afp_ext": {"neto": _ult(w, "afp_ext_net"), "bruto": _ult(w, "afp_ext_tot"),
                    "neto_12": _hace12(w, "afp_ext_net")},
        "bancos_ext": {"ois_neto": _ult(w, "ois_net"), "efv_neto": _ult(w, "efv_net")},
    }


# ----------------------------------------------------------------------------------------
# 4 · Coberturas por entidad
# ----------------------------------------------------------------------------------------
def coberturas_afp(db) -> dict:
    """AFP × fondo (SP, último mes): inversión extranjera, venta neta de USD a plazo,
    cobertura, compra neta de UF a plazo y valorización de los swaps (% del fondo)."""
    ult = db.conn.execute("SELECT MAX(period) FROM sp_cartera_detalle WHERE tabla LIKE 'fwd%'").fetchone()[0]
    if not ult:
        return {}
    act = db.conn.execute("""
        SELECT afp, fondo,
               max(CASE WHEN glosa = 'TOTAL ACTIVOS MMU$' THEN valor END) AS activos,
               max(CASE WHEN glosa = 'TOTAL EXTRANJERO' AND metrica = 'porcentaje' THEN valor END) AS pct_ext
        FROM sp_cartera_detalle WHERE period = ? AND tabla = 'activos' AND afp <> 'TOTAL'
        GROUP BY ALL""", [ult]).fetchdf()
    fw = db.conn.execute("""
        SELECT afp, fondo,
               SUM(CASE WHEN grupo LIKE 'Dólar estadounidense%' AND glosa = 'Pesos' AND right(codigo, 1) = 'V' THEN valor ELSE 0 END) AS usd_v,
               SUM(CASE WHEN grupo LIKE 'Dólar estadounidense%' AND glosa = 'Pesos' AND right(codigo, 1) = 'C' THEN valor ELSE 0 END) AS usd_c,
               SUM(CASE WHEN grupo = 'Unidad de Fomento' AND glosa = 'Pesos' AND right(codigo, 1) = 'C' THEN valor ELSE 0 END) AS uf_c,
               SUM(CASE WHEN grupo = 'Unidad de Fomento' AND glosa = 'Pesos' AND right(codigo, 1) = 'V' THEN valor ELSE 0 END) AS uf_v,
               -- otras monedas contra dólar: cobertura de la canasta extranjera hacia USD
               SUM(CASE WHEN grupo NOT LIKE 'Dólar estadounidense%' AND grupo NOT IN ('Unidad de Fomento', 'Pesos')
                         AND glosa LIKE 'Dólar estadounidense%' AND right(codigo, 1) = 'V' THEN valor ELSE 0 END) AS cruz_v
        FROM sp_cartera_detalle
        WHERE period = ? AND tabla IN ('fwd_nac', 'fwd_ext') AND metrica = 'monto_dolares' AND afp <> 'TOTAL'
        GROUP BY ALL""", [ult]).fetchdf()
    sw = db.conn.execute("""
        SELECT afp, fondo, SUM(valor) AS swaps
        FROM sp_cartera_detalle
        WHERE period = ? AND tabla IN ('swap_nac', 'swap_ext') AND metrica = 'monto_dolares'
          AND afp <> 'TOTAL' AND glosa NOT LIKE 'TOTAL%'
        GROUP BY ALL""", [ult]).fetchdf()
    df = act.merge(fw, on=["afp", "fondo"], how="left").merge(sw, on=["afp", "fondo"], how="left").fillna(0.0)
    if df.empty:
        return {}
    df["ext"] = df.activos * df.pct_ext / 100
    df["usd_neto_venta"] = df.usd_v - df.usd_c
    df["cobertura"] = (df.usd_neto_venta / df.ext * 100).where(df.ext > 0)
    df["uf_neto"] = df.uf_c - df.uf_v
    df["swaps_pct"] = (df.swaps / df.activos * 100).where(df.activos > 0)
    filas = [{k: (None if pd.isna(v) else (float(v) if not isinstance(v, str) else v)) for k, v in r.items()}
             for r in df.sort_values(["afp", "fondo"]).to_dict("records")]
    # Agregado por fondo (todas las AFP)
    g = df.groupby("fondo")[["activos", "ext", "usd_neto_venta", "uf_neto", "swaps"]].sum()
    g["cobertura"] = g.usd_neto_venta / g.ext * 100
    g["swaps_pct"] = g.swaps / g.activos * 100
    por_fondo = [{"fondo": f, **{k: float(v) for k, v in r.items()}} for f, r in g.iterrows()]
    tot = df[["activos", "ext", "usd_neto_venta", "uf_neto", "swaps"]].sum()
    return {"ultimo": f"{ult // 100}-{ult % 100:02d}", "filas": filas, "por_fondo": por_fondo,
            "total": {**{k: float(v) for k, v in tot.items()},
                      "cobertura": float(tot.usd_neto_venta / tot.ext * 100) if tot.ext else None},
            "afps": sorted(df.afp.unique().tolist()), "fondos": sorted(df.fondo.unique().tolist())}


def coberturas_seguros(db, top: int = 12) -> dict:
    """Compañía (B.7 + B.8): posición neta en moneda extranjera vía derivados contra su
    inversión en el extranjero, y para qué declara usarlos."""
    from api.insurer_glossary import clean_company_name, describe
    from db.derivados_seguros import _frame as frame_seg
    ult = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_derivatives").fetchone()[0]
    if not ult:
        return {}
    d = frame_seg(db, int(ult))
    d = d[d.period == ult]
    d = d[d.valido | ~d.tipo.isin(["forward", "swap"])]
    usd = float(d.usd.iloc[0]) if len(d) else None
    ctrl = db.conn.execute("""SELECT rut, insurance_type, investment_code, valor_final
                              FROM cmf_insurer_portfolio_control WHERE period = ?""", [int(ult)]).fetchdf()
    ctrl["geo"] = ctrl.investment_code.map(lambda c: describe(c)["geo"])
    ext = (ctrl[ctrl.geo == "extranjero"].groupby(["rut", "insurance_type"]).valor_final.sum() * 1e3 / usd / 1e6
           if usd else pd.Series(dtype=float))
    cart = ctrl.groupby(["rut", "insurance_type"]).valor_final.sum() * 1e3 / usd / 1e6 if usd else pd.Series(dtype=float)
    nombres = dict(db.conn.execute("""SELECT rut, arg_max(company_name, period) FROM cmf_insurer_portfolio_control
                                      WHERE company_name <> '' GROUP BY 1""").fetchall())
    g = (d.groupby(["rut", "insurance_type"])
          .agg(contratos=("tipo", "size"), bruto_m=("bruto_m", "sum"), me_usd=("me_usd", "sum"),
               vr_m=("valor_razonable_m", "sum"),
               cbe=("objetivo", lambda s: float((s == "CBE").mean() * 100)),
               inv=("objetivo", lambda s: float((s == "INV").mean() * 100)),
               otro=("objetivo", lambda s: float((~s.isin(["CBE", "INV"])).mean() * 100)),
               exterior=("tipo_contraparte", lambda s: float(s.isin(["04", "05", "06"]).mean() * 100)),
               ncp=("contraparte", "nunique"))
          .reset_index())
    filas = []
    for r in g.itertuples():
        k = (r.rut, r.insurance_type)
        e, c = float(ext.get(k, 0.0)), float(cart.get(k, 0.0))
        filas.append({"nombre": clean_company_name(nombres.get(r.rut, ""), r.rut), "ramo": r.insurance_type,
                      "contratos": int(r.contratos), "bruto_mmusd": float(r.bruto_m) * 1e3 / usd / 1e6 if usd else None,
                      "me_usd": float(r.me_usd), "ext_usd": e, "cartera_usd": c,
                      "cobertura": (-float(r.me_usd) / e * 100) if e > 1 else None,
                      "vr_mmusd": float(r.vr_m) * 1e3 / usd / 1e6 if usd else None,
                      "pct_cbe": r.cbe, "pct_inv": r.inv, "pct_otro": r.otro,
                      "pct_exterior": r.exterior, "contrapartes": int(r.ncp)})
    filas.sort(key=lambda f: -(f["bruto_mmusd"] or 0))
    tot_bruto = sum(f["bruto_mmusd"] or 0 for f in filas)
    for f in filas:
        f["peso"] = (f["bruto_mmusd"] or 0) / tot_bruto * 100 if tot_bruto else None
    tot_me = sum(f["me_usd"] for f in filas)
    tot_ext = sum(f["ext_usd"] for f in filas)
    return {"ultimo": f"{ult // 100}-{ult % 100:02d}", "filas": filas[:top], "n": len(filas),
            "total": {"me_usd": tot_me, "ext_usd": tot_ext, "bruto": tot_bruto,
                      "cobertura": -tot_me / tot_ext * 100 if tot_ext else None},
            "top3": sum(f["peso"] or 0 for f in filas[:3])}


def coberturas_ffmm(db, top: int = 20) -> dict:
    """Fondo mutuo (FUTU/OPCI + cartera extranjera EXTR): dólares vendidos a plazo contra la
    inversión en el exterior, y nocional bruto contra el patrimonio (apalancamiento)."""
    from db.derivados_ffmm import _base
    ult = db.conn.execute("SELECT MAX(period) FROM v_mutual_fund_derivatives").fetchone()[0]
    if not ult:
        return {}
    d = _base(db, int(ult))
    d = d[d.period == ult]
    if d.empty:
        return {}
    usd = float(d.usd.dropna().iloc[0])
    ext = db.conn.execute("""
        WITH cur AS (SELECT fund_run, arg_max(currency, date) currency FROM cmf_mutual_fund_quota_values GROUP BY 1)
        SELECT p.fund_run, SUM(try_cast(json_extract_string(p.raw_data, '$.FFM_6021200') AS DOUBLE)) / 1e3
               * CASE WHEN any_value(cur.currency) = 'DOLAR' THEN ? ELSE 1 END AS ext_mm
        FROM cmf_mutual_fund_portfolio p LEFT JOIN cur USING (fund_run)
        WHERE p.portfolio_type = 'EXTR' AND p.period_year * 100 + p.period_month = ?
        GROUP BY 1""", [usd, int(ult)]).fetchdf()
    ext = dict(zip(ext.fund_run, ext.ext_mm / usd))           # millones de USD
    nav = dict(db.conn.execute("""
        WITH cur AS (SELECT fund_run, arg_max(currency, date) currency FROM cmf_mutual_fund_quota_values GROUP BY 1)
        SELECT q.fund_run, SUM(q.net_asset_value) * CASE WHEN any_value(cur.currency) = 'DOLAR' THEN ? ELSE 1 END
        FROM cmf_mutual_fund_quota_values q LEFT JOIN cur USING (fund_run)
        WHERE q.date = (SELECT MAX(date) FROM cmf_mutual_fund_quota_values WHERE year(date) * 100 + month(date) = ?)
        GROUP BY 1""", [usd, int(ult)]).fetchall())
    es_usd = (d.subyacente == "USD") & d.tipo.isin(["forward", "futuro"])
    g = (d.assign(neto_usd=(d.signo * d.nocional_unidades).where(es_usd, 0.0) / 1e6)
          .groupby(["fund_run", "fund_name", "agf"])
          .agg(bruto_mm=("monto_mm", "sum"), neto_usd=("neto_usd", "sum"), contratos=("monto_mm", "size"),
               tipos=("tipo", lambda s: ", ".join(sorted(set(s)))),
               subyacentes=("subyacente", lambda s: ", ".join(sorted(set(s)))))
          .reset_index().sort_values("bruto_mm", ascending=False))
    filas = []
    for r in g.itertuples():
        e = ext.get(r.fund_run)
        pat = nav.get(r.fund_run)               # millones de $ (patrimonio en millones en el archivo)
        filas.append({"fondo": str(r.fund_name).title(), "run": r.fund_run,
                      "agf": " ".join(str(r.agf or "").split()),
                      "bruto_mm": float(r.bruto_mm), "neto_usd": float(r.neto_usd),
                      "ext_usd": float(e) if e else None,
                      "cobertura": (-float(r.neto_usd) / e * 100) if e and e > 0.5 else None,
                      "veces": (float(r.bruto_mm) / pat) if pat else None,
                      "contratos": int(r.contratos), "tipos": r.tipos, "subyacentes": r.subyacentes})
    tot = sum(f["bruto_mm"] for f in filas)
    for f in filas:
        f["peso"] = f["bruto_mm"] / tot * 100 if tot else None
    cubiertos = sorted([f for f in filas if (f["ext_usd"] or 0) > 5], key=lambda f: -f["ext_usd"])
    ext_tot = sum(f["ext_usd"] for f in cubiertos)
    return {"ultimo": f"{ult // 100}-{ult % 100:02d}", "filas": filas[:top], "n": len(filas),
            "top3": sum(f["peso"] or 0 for f in filas[:3]), "bruto_total": tot,
            "apalancados": sorted([f for f in filas if (f["veces"] or 0) > 1.0], key=lambda f: -f["veces"])[:10],
            "cubiertos": cubiertos[:15],
            "cubiertos_total": {"ext_usd": ext_tot, "neto_usd": sum(f["neto_usd"] for f in cubiertos),
                                "cobertura": -sum(f["neto_usd"] for f in cubiertos) / ext_tot * 100 if ext_tot else None,
                                "n": len(cubiertos)}}


# ----------------------------------------------------------------------------------------
# 5 · Alertas de riesgo (reglas simples y explícitas)
# ----------------------------------------------------------------------------------------
def alertas(ind: dict, afp: dict, seg: dict, ffm: dict) -> list[dict]:
    out = []
    # AFP: una alerta por administradora (las mismas reglas valen para sus cinco fondos).
    por_afp = {}
    for f in (afp or {}).get("filas", []):
        por_afp.setdefault(f["afp"], []).append(f)
    for nombre, fs in por_afp.items():
        sobre = [f for f in fs if f["cobertura"] is not None and f["cobertura"] > 100]
        for f in sobre:
            out.append({"nivel": "alto", "entidad": f"AFP {nombre.title()} · Fondo {f['fondo']}",
                        "texto": f"vende a plazo más dólares que su inversión extranjera ({f['cobertura']:.0f}%): "
                                 "el exceso es una posición a que el dólar baje, no una cobertura"})
        neg = [f for f in fs if f["swaps_pct"] is not None and f["swaps_pct"] < -1]
        if neg:
            peor = min(neg, key=lambda f: f["swaps_pct"])
            out.append({"nivel": "alto" if peor["swaps_pct"] < -2 else "medio",
                        "entidad": f"AFP {nombre.title()}",
                        "texto": f"swaps valorizados bajo −1% del fondo en {', '.join(f['fondo'] for f in neg)} "
                                 f"(peor: Fondo {peor['fondo']}, {peor['swaps_pct']:.1f}%)".replace(".", ",")})
    for f in (seg or {}).get("filas", []):
        if f["cobertura"] is not None and f["cobertura"] > 120:
            out.append({"nivel": "medio", "entidad": f["nombre"],
                        "texto": f"posición corta en moneda extranjera equivalente al {f['cobertura']:.0f}% de su inversión en el exterior"})
    for f in (ffm or {}).get("apalancados", []):
        out.append({"nivel": "medio", "entidad": f"Fondo mutuo {f['fondo']}",
                    "texto": f"nocional de derivados {f['veces']:.1f} veces su patrimonio"})
    for f in (ind or {}).get("filas", []):
        if f["usd_neto"] is not None and f["usd_neto_12"] not in (None, 0):
            cambio = f["usd_neto"] - f["usd_neto_12"]
            if abs(cambio) > max(2000, abs(f["usd_neto_12"]) * 0.5):
                out.append({"nivel": "info", "entidad": f["sector"],
                            "texto": f"su neto dólar-peso cambió {cambio:+,.0f} MMUSD en 12 meses".replace(",", ".")})
    orden = {"alto": 0, "medio": 1, "info": 2}
    return sorted(out, key=lambda a: orden[a["nivel"]])


def resumen(db) -> dict:
    ind = industrias(db)
    afp = coberturas_afp(db)
    seg = coberturas_seguros(db)
    ffm = coberturas_ffmm(db)
    return {"mer": mercado(db), "ind": ind, "tas": tasas(db), "afp": afp, "seg": seg, "ffm": ffm,
            "alertas": alertas(ind, afp, seg, ffm), "ident": identidades(db)}
