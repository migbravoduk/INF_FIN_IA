"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/"      → panel multi-fuente (overview.html), KPIs reales + gráfico UF.
"/eeff"  → estados financieros corporativos (CMF): selector empresa/período + tabla.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import (build_statement_groups, GROUP_LABELS, GROUP_ORDER,
                             GROUP_SLOTS, _assign_balance_sections)
from api.company_profiles import get_profile
from db.database import Database

router = APIRouter()


# ----------------------------------------------------------
# Panel multi-fuente
# ----------------------------------------------------------

def build_overview_context(db: Database) -> dict:
    """Contexto del panel: KPIs + series UF y TPM (12 meses). Reusable por el export estático."""
    kpi = db.get_overview_kpis()
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    uf_series = records(db.get_series(db.KPI_SERIES["uf"], from_date=since))
    tpm_series = records(db.get_series(db.KPI_SERIES["tpm"], from_date=since))
    return {"kpi": kpi, "uf_series": uf_series, "tpm_series": tpm_series}


@router.get("/")
def overview(request: Request, db: Database = Depends(get_db)):
    """Panel multi-fuente (página principal)."""
    ctx = build_overview_context(db)
    return templates.TemplateResponse(request, "overview.html", ctx)


# ----------------------------------------------------------
# Estados financieros corporativos (CMF)
# ----------------------------------------------------------

@router.get("/eeff")
def eeff(request: Request, db: Database = Depends(get_db)):
    """Vista de estados financieros: selector de empresa y período."""
    return templates.TemplateResponse(request, "eeff.html", {
        "companies": records(db.get_cmf_companies()),
        "periods": db.get_cmf_periods(),
    })


@router.get("/eeff/table")
def eeff_table(
    request: Request,
    company: str = Query(""),
    serie: str = Query("anual5"),
    db: Database = Depends(get_db),
):
    """
    Fragmento HTMX: vista de empresa fusionada (EEFF + Evolución). Muestra reseña, ratios
    (último período), selectores de gráfico y la MATRIZ evolutiva (cuentas × períodos) de
    cada estado estándar, según el tipo de serie elegido.
    """
    empty = templates.TemplateResponse(request, "partials/eeff_table.html", {"meta": None})
    if not company.strip():
        return empty
    match = db.query_cmf_statements(company=company, limit=1)
    if match.empty:
        return empty
    rut, cname = str(match.iloc[0]["rut"]), str(match.iloc[0]["company_name"])

    periods = _select_periods(db.get_company_periods(rut), serie)
    if not periods:
        return empty
    latest = max(periods)
    latest_df = db.query_cmf_statements(rut=rut, period=latest, limit=3000)
    codes_present = set(str(x) for x in latest_df["statement_group"].unique()) if not latest_df.empty else set()

    statements = []
    for slot in GROUP_SLOTS:
        chosen = next((c for c in slot if c in codes_present), None)
        if not chosen:
            continue
        ev = db.get_statement_evolution(rut, periods, chosen)
        if chosen.startswith("ESF"):
            _assign_balance_sections(ev["accounts"])
        statements.append({"label": GROUP_LABELS[chosen], "code": chosen, "ev": ev})

    meta = {
        "company_name": cname, "rut": rut,
        "report_type": str(latest_df.iloc[0]["report_type"]) if not latest_df.empty else "",
        "currency": str(latest_df.iloc[0]["currency"]) if not latest_df.empty else "",
        "periods": periods, "latest": latest, "profile": get_profile(rut, cname),
    }
    return templates.TemplateResponse(request, "partials/eeff_table.html", {
        "meta": meta, "ratios": db.get_company_ratios(rut, latest),
        "graph_accounts": db.get_company_graphable_accounts(rut), "statements": statements,
    })


# Partidas de flujo/resultado (acumuladas en el año): se desacumulan y se muestran en
# valores del período (no base 100, que distorsiona income con signos/ceros).
_FLOW_PARTIDAS = {"Ganancia (pérdida)", "Ganancia bruta", "Costo de ventas",
                  "Ingresos de actividades ordinarias"}


def _deaccumulate(pts: list) -> list:
    """Desacumula intra-anual: valor = acumulado - acumulado del período previo del mismo año."""
    out, prev_by_year = [], {}
    for p in pts:
        year = p["period"][:4]
        v = p["value"] - prev_by_year[year] if year in prev_by_year else p["value"]
        out.append({"period": p["period"], "value": v})
        prev_by_year[year] = p["value"]
    return out


@router.get("/eeff/serie")
def eeff_serie(
    request: Request,
    rut: str = Query(...),
    account: str = Query(...),
    delta: bool = Query(False),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: evolución por período de una partida. delta=desacumular flujo intra-anual."""
    if not account.strip():
        return templates.TemplateResponse(request, "partials/eeff_serie.html",
                                          {"series": [], "account": None})
    df = db.get_company_account_series(rut, account)
    series = [{"period": str(int(r["period"])), "value": r["value"]}
              for _, r in df.iterrows()]

    if (delta or account in _FLOW_PARTIDAS) and series:
        series = _deaccumulate(series)

    suffix = " (variación intra-anual)" if (delta or account in _FLOW_PARTIDAS) else ""
    return templates.TemplateResponse(request, "partials/eeff_serie.html", {
        "series": series, "account": (account + suffix) if account else account,
    })


# ----------------------------------------------------------
# Bancos (CMF — desglose por moneda)
# ----------------------------------------------------------

@router.get("/banca")
def banca(request: Request, db: Database = Depends(get_db)):
    """Vista de estados financieros de bancos: selector banco/período/reporte."""
    return templates.TemplateResponse(request, "banca.html", {
        "banks": records(db.get_bank_list()),
        "periods": db.get_bank_periods(),
    })


@router.get("/banca/table")
def banca_table(
    request: Request,
    bank: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    report_type: str = Query("balance", alias="type"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: balance o resultados de un banco con desglose por moneda."""
    period_int = int(period) if period else None
    
    # Calcular los 12 períodos (el actual y 11 anteriores)
    periods = []
    if period_int:
        y = period_int // 100
        m = period_int % 100
        for _ in range(12):
            periods.append(y * 100 + m)
            m -= 1
            if m == 0:
                m = 12
                y -= 1
                
    df = db.query_bank_statements(
        bank_code=bank, periods=periods if periods else None,
        report_type=report_type, limit=50000,
    )
    meta, rows, graph_accounts = None, [], []
    history_periods = []
    
    if not df.empty:
        df_base = df[df["period"] == period_int] if period_int else df
        if not df_base.empty:
            first = df_base.iloc[0]
            meta = {
                "bank_name": str(first["bank_name"]),
                "bank_code": str(first["bank_code"]),
                "period": int(first["period"]),
            }
            from api.eeff_format import is_total_account
            
            history_periods = periods[1:] if periods else []
            
            hist_dict = {}
            for _, r in df.iterrows():
                hist_dict[(str(r["account_code"]), int(r["period"]))] = r["val_total"]
                
            raw_rows = records(df_base)
            for r in raw_rows:
                r["is_total"] = is_total_account(r["account_name"])
                code = str(r["account_code"])
                r["history"] = [hist_dict.get((code, p), None) for p in history_periods]
                
            rows = raw_rows
            graph_accounts = db.get_bank_graphable_accounts(bank, report_type)
            
    return templates.TemplateResponse(request, "partials/banca_table.html", {
        "meta": meta, "rows": rows, "report_type": report_type, "graph_accounts": graph_accounts,
        "history_periods": history_periods,
    })


@router.get("/comparar")
def comparar(request: Request, db: Database = Depends(get_db)):
    """Vista de comparación: misma partida en varias empresas (base 100)."""
    from api.company_profiles import get_all_sectors
    return templates.TemplateResponse(request, "compare.html", {
        "companies": records(db.get_cmf_companies()),
        "partidas": [
            "Total de activos", "Total de patrimonio", "Total de pasivos",
            "Ganancia (pérdida)", "Ganancia bruta", "Costo de ventas",
        ],
        "sectors": get_all_sectors(),
    })





@router.get("/comparar/chart")
def comparar_chart(
    request: Request,
    account: str = Query(""),
    c1: str = Query(""), c2: str = Query(""), c3: str = Query(""),
    sector: str = Query(""),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: overlay de una misma partida en varias empresas, o Top 5 de un sector."""
    if not account.strip():
        return templates.TemplateResponse(request, "partials/compare_chart.html",
                                          {"multi": None, "account": None, "mode": None})
    is_flow = account in _FLOW_PARTIDAS
    multi = []
    
    names_to_compare = []
    if sector:
        from api.company_profiles import get_profile
        all_comps = db.get_cmf_companies()
        for _, row in all_comps.iterrows():
            if get_profile(row["rut"], row["company_name"])["macro_sector"] == sector:
                names_to_compare.append(str(row["company_name"]))
                if len(names_to_compare) >= 5:
                    break
    else:
        names_to_compare = [n for n in (c1, c2, c3) if n.strip()]

    for name in names_to_compare:
        match = db.query_cmf_statements(company=name, limit=1)
        if match.empty:
            continue
        rut, cname = str(match.iloc[0]["rut"]), str(match.iloc[0]["company_name"])
        s = db.get_company_account_series(rut, account)
        pts = [{"period": str(int(r["period"])), "value": r["value"]}
               for _, r in s.iterrows() if r["value"] is not None]
        if not pts:
            continue
        if is_flow:
            pts = _deaccumulate(pts)
            points = [{"x": p["period"], "value": p["value"]} for p in pts]
        else:
            base = pts[0]["value"] or None
            points = [{"x": p["period"], "value": (p["value"] / base * 100.0) if base else None}
                      for p in pts]
        multi.append({"name": cname, "points": points})
    return templates.TemplateResponse(request, "partials/compare_chart.html", {
        "multi": multi, "account": account, "mode": "periodo" if is_flow else "base100",
    })


@router.get("/comparar/ratios")
def comparar_ratios(
    request: Request,
    c1: str = Query(""), c2: str = Query(""), c3: str = Query(""),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: tabla comparativa de ratios financieros entre empresas (último período)."""
    cols = []
    for name in (c1, c2, c3):
        name = name.strip()
        if not name:
            continue
        match = db.query_cmf_statements(company=name, limit=1)
        if match.empty:
            continue
        rut, cname = str(match.iloc[0]["rut"]), str(match.iloc[0]["company_name"])
        period = db.get_company_latest_period(rut)
        ratios = db.get_company_ratios(rut, period) if period else None
        if ratios:
            cols.append({"name": cname, "period": period, "ratios": ratios})
    return templates.TemplateResponse(request, "partials/compare_ratios.html", {"cols": cols})


_RATIO_LABELS = {
    "roe": "ROE", "roa": "ROA", "margen_neto": "Margen neto", "margen_bruto": "Margen bruto",
    "liquidez": "Liquidez corriente", "endeudamiento": "Deuda / Patrimonio",
}


@router.get("/ranking")
def ranking(request: Request, db: Database = Depends(get_db)):
    """Vista de ranking de empresas por indicador financiero."""
    from api.company_profiles import get_all_sectors
    return templates.TemplateResponse(request, "ranking.html", {
        "periods": db.get_cmf_periods(), "ratios": _RATIO_LABELS,
        "sectors": get_all_sectors(),
    })


@router.get("/ranking/table")
def ranking_table(
    request: Request,
    metric: str = Query("roe"),
    period: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: top empresas por un ratio en un período."""
    periods = db.get_cmf_periods()
    period_int = int(period) if period else (periods[0] if periods else None)
    sec = sector.strip() if sector else None
    rows = db.get_ratios_ranking(period_int, metric, 15, sec) if period_int else []
    return templates.TemplateResponse(request, "partials/ranking_table.html", {
        "rows": rows, "metric_label": _RATIO_LABELS.get(metric, metric),
        "period": period_int, "is_pct": metric not in ("liquidez", "endeudamiento"),
    })


def _select_periods(allp: list, serie: str) -> list:
    """Selecciona períodos según el tipo de serie evolutiva."""
    allp = sorted(int(p) for p in allp)
    if not allp:
        return []
    if serie.startswith("anual"):
        n = 10 if "10" in serie else 5
        return [p for p in allp if p % 100 == 12][-n:]
    if serie.startswith("trimq"):
        n = 10 if "10" in serie else 5
        q = max(allp) % 100
        return [p for p in allp if p % 100 == q][-n:]
    if serie == "trim8":
        return allp[-8:]
    return allp[-5:]


@router.get("/evolucion")
def evolucion():
    """La vista evolutiva se fusionó con /eeff; se redirige por compatibilidad."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/eeff", status_code=307)


@router.get("/eeff/ratios-serie")
def eeff_ratios_serie(
    request: Request,
    rut: str = Query(...),
    metric: str = Query(""),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: evolución de un indicador financiero de una empresa por período."""
    if not metric.strip():
        return templates.TemplateResponse(request, "partials/ratios_serie.html",
                                          {"series": [], "label": None, "is_pct": True})
    s = db.get_company_ratios_series(rut)
    series = [{"period": str(r["period"]), "value": r.get(metric)} for r in s]
    return templates.TemplateResponse(request, "partials/ratios_serie.html", {
        "series": series, "label": _RATIO_LABELS.get(metric, metric),
        "is_pct": metric not in ("liquidez", "endeudamiento"),
    })


@router.get("/banca/serie")
def banca_serie(
    request: Request,
    bank: str = Query(...),
    account: str = Query(...),
    report_type: str = Query("balance", alias="type"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: evolución por período de una cuenta bancaria (gráfico)."""
    if not account.strip():
        return templates.TemplateResponse(request, "partials/eeff_serie.html",
                                          {"series": [], "account": None})
    df = db.get_bank_account_series(bank, account, report_type)
    series = []
    for _, r in df.iterrows():
        series.append({
            "period": str(int(r["period"])),
            "value": r["value"],
            "clp_no_reaj": r["clp_no_reaj"],
            "clp_reaj_ipc": r["clp_reaj_ipc"],
            "clp_reaj_tc": r["clp_reaj_tc"],
            "extranjera": r["extranjera"]
        })
    return templates.TemplateResponse(request, "partials/banca_evolutiva.html", {
        "series": series, "account": account,
    })


# ----------------------------------------------------------
# AFP (SP — valor cuota por multifondo)
# ----------------------------------------------------------

@router.get("/afp")
def afp(request: Request, db: Database = Depends(get_db)):
    """Vista de valor cuota: selector AFP/fondo + gráfico de evolución."""
    return templates.TemplateResponse(request, "afp.html", {
        "afps": db.get_afp_list(),
        "funds": ["A", "B", "C", "D", "E"],
    })


@router.get("/afp/chart")
def afp_chart(
    request: Request,
    afp: Optional[str] = Query(None),
    fund: str = Query("A"),
    metric: str = Query("cuota"),
    db: Database = Depends(get_db),
):
    """
    Fragmento HTMX: evolución de fondos de pensiones, con varios modos:
      - una AFP + un fondo          → serie única.
      - una AFP + fund='TODOS'      → compara los 5 fondos de esa AFP.
      - afp='TODAS' + un fondo      → compara todas las AFP en ese fondo.
    metric='cuota' (valor cuota, normalizado base 100) | 'patrimonio' (absoluto).
    """
    since = (dt.date.today() - dt.timedelta(days=730)).isoformat()
    val_key = "equity_value" if metric == "patrimonio" else "quota_value"
    normalize = metric != "patrimonio"
    metric_label = "patrimonio" if metric == "patrimonio" else "valor cuota"
    funds = ["A", "B", "C", "D", "E"]

    def serie(afp_, fund_):
        df = db.query_sp_quota_values(afp=afp_, fund=fund_, from_date=since, limit=5000)
        return records(df.sort_values("date")) if not df.empty else None

    def norm(pts, name):
        base = pts[0][val_key] or None
        return {"name": name,
                "points": [{"date": p["date"],
                            "value": (p[val_key] / base * 100.0) if (normalize and base) else p[val_key]}
                           for p in pts]}

    multi, title, latest, note = [], "", None, None

    # Participación de mercado (% del patrimonio del fondo, por AFP en el tiempo).
    if metric == "share":
        if afp == "TODAS" and fund not in ("TODOS", None, ""):
            import pandas as _pd
            edf = db.get_fund_equity_rows(fund, since)
            if not edf.empty:
                edf["date"] = edf["date"].astype(str)
                piv = edf.pivot_table(index="date", columns="afp_name",
                                      values="equity_value", aggfunc="sum")
                shares = piv.div(piv.sum(axis=1), axis=0) * 100.0
                multi = [{"name": col,
                          "points": [{"date": idx, "value": (None if v != v else round(float(v), 2))}
                                     for idx, v in shares[col].items()]}
                         for col in shares.columns]
                title = f"Participación de mercado · Fondo {fund} · % del patrimonio"
            else:
                note = "Sin datos de patrimonio para ese fondo."
        else:
            note = "La participación de mercado aplica al modo 'Todas (comparar AFP)' + un fondo."
        return templates.TemplateResponse(request, "partials/afp_chart.html", {
            "multi": multi or None, "title": title, "normalized": False,
            "metric_label": "participación", "latest": None, "note": note,
            "afp": afp, "fund": fund,
        })

    if afp == "TODAS" and fund == "TODOS":
        note = "Elige una AFP específica o un fondo específico para comparar."
    elif afp == "TODAS":
        for a in db.get_afp_list():
            pts = serie(a, fund)
            if pts:
                multi.append(norm(pts, a))
        title = f"Comparación de AFP · Fondo {fund} · {metric_label}"
    elif fund == "TODOS":
        for f in funds:
            pts = serie(afp, f)
            if pts:
                multi.append(norm(pts, "Fondo " + f))
        title = f"{afp} · comparación de fondos · {metric_label}"
    else:
        pts = serie(afp, fund)
        if pts:
            multi.append(norm(pts, f"{afp} · Fondo {fund}"))
            latest = pts[-1]
        title = f"{afp} · Fondo {fund} · {metric_label}"

    return templates.TemplateResponse(request, "partials/afp_chart.html", {
        "multi": multi or None, "title": title, "normalized": normalize,
        "metric_label": metric_label, "latest": latest, "note": note,
        "afp": afp, "fund": fund,
    })


@router.get("/afp/rentabilidad")
def afp_rentabilidad(request: Request, db: Database = Depends(get_db)):
    """Fragmento HTMX: rentabilidad 12m por fondo, nominal vs real (descontando IPC)."""
    fr = db.get_fund_returns_12m()
    ipc = db.get_latest_value(db.KPI_SERIES["ipc_v12"])
    ipc12 = ipc["value"] if ipc else None
    rows = []
    for f in ("A", "B", "C", "D", "E"):
        nom = fr[f]["agg"]
        real = None
        if nom is not None and ipc12 is not None:
            real = ((1 + nom / 100.0) / (1 + ipc12 / 100.0) - 1) * 100.0
        rows.append({"fund": f, "nominal": nom, "real": real})
    return templates.TemplateResponse(request, "partials/afp_rentabilidad.html", {
        "rows": rows, "ipc": ipc12,
    })


@router.get("/afp/cartera")
def afp_cartera(request: Request, fund: str = Query("A"), db: Database = Depends(get_db)):
    """Fragmento HTMX: composición de cartera de un fondo (último período disponible)."""
    periods = db.get_portfolio_periods()
    if not periods:
        return templates.TemplateResponse(request, "partials/afp_cartera.html",
                                          {"rows": [], "period": None, "fund": fund})
    period = periods[0]
    df = db.get_portfolio_composition(period, fund, "TOTAL", 12)
    df_foreign = db.get_foreign_portfolio(period, fund, "TOTAL", 15)

    def clean(g: str) -> str:
        g = str(g).strip()
        if g.lower().startswith("total "):
            g = g[6:].strip()
        return (g[:1].upper() + g[1:].lower()) if g else g

    rows = [{"glosa": clean(r["instrument_glosa"]), "pct": float(r["porcentaje"])}
            for _, r in df.iterrows()]
    foreign_rows = [{"glosa": clean(r["instrument_glosa"]), "pct": float(r["porcentaje"])}
                    for _, r in df_foreign.iterrows()]

    return templates.TemplateResponse(request, "partials/afp_cartera.html", {
        "rows": rows, "foreign_rows": foreign_rows, "period": period, "fund": fund,
    })


@router.get("/afp/comparativa")
def afp_comparativa(request: Request, fund: str = Query("A"), db: Database = Depends(get_db)):
    """Fragmento HTMX: tabla comparativa detallada por AFP para un fondo."""
    import datetime as dt
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    
    # Obtener listado para el fondo seleccionado (si es TODOS, la query mezclará, así que iteramos)
    target_funds = ["A", "B", "C", "D", "E"] if fund == "TODOS" else [fund]
    afps = db.get_afp_list()
    
    results = []
    
    for f in target_funds:
        df = db.query_sp_quota_values(fund=f, from_date=since, limit=20000)
        if df.empty:
            continue
            
        df = df.sort_values("date")
        
        # Filtrar solo último día para patrimonio
        last_date = df["date"].max()
        df_last = df[df["date"] == last_date]
        total_equity = df_last["equity_value"].sum()
        
        for a in afps:
            df_afp = df[df["afp_name"] == a]
            if df_afp.empty:
                continue
                
            latest = df_afp.iloc[-1]
            earliest = df_afp.iloc[0]
            
            c_latest = latest["quota_value"]
            c_earliest = earliest["quota_value"]
            eq = latest["equity_value"]
            
            ret_12m = ((c_latest / c_earliest) - 1) * 100 if c_latest and c_earliest else None
            share = (eq / total_equity) * 100 if eq and total_equity else None
            
            # Formatear
            results.append({
                "afp": a,
                "fund": f,
                "rentabilidad": ret_12m,
                "cuota": c_latest,
                "patrimonio": eq,
                "share": share
            })
            
    # Si se seleccionó "TODOS", agrupar por AFP sumando patrimonio y promediando rentabilidad?
    # Mejor mostrarlo tal cual o dejarlo claro que es una vista combinada.
    # Por simplicidad, lo ordenaremos por Rentabilidad descendente
    results.sort(key=lambda x: x["rentabilidad"] or -999, reverse=True)
    
    return templates.TemplateResponse(request, "partials/afp_comparativa.html", {
        "results": results, "fund": fund
    })


@router.get("/salud")
def salud(request: Request, db: Database = Depends(get_db)):
    """Vista principal de Salud Financiera (Radar de anomalías)."""
    from api.company_profiles import get_all_sectors
    return templates.TemplateResponse(request, "salud.html", {
        "periods": db.get_cmf_periods(),
        "sectors": get_all_sectors(),
    })


@router.get("/salud/chart")
def salud_chart(
    request: Request,
    period: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: datos para scatter plot de riesgo vs rentabilidad."""
    periods = db.get_cmf_periods()
    period_int = int(period) if period else (periods[0] if periods else None)
    
    if not period_int:
        return templates.TemplateResponse(request, "partials/salud_chart.html", {"data": []})
        
    df = db.query_cmf_statements(period=period_int, limit=10**9)
    if df.empty:
        return templates.TemplateResponse(request, "partials/salud_chart.html", {"data": []})

    from api.company_profiles import get_profile
    sec = sector.strip() if sector else None
    data = []
    
    from db.database import compute_ratios
    for rut, sub in df.groupby("rut", sort=False):
        cname = str(sub.iloc[0]["company_name"])
        if sec:
            prof = get_profile(rut, cname)
            if prof["macro_sector"] != sec:
                continue
                
        r = compute_ratios(sub)
        if not r or r.get("roe") is None or r.get("endeudamiento") is None or r.get("liquidez") is None:
            continue
            
        roe = r["roe"]
        deuda = r["endeudamiento"]
        liquidez = r["liquidez"]
        
        if abs(roe) > 150 or deuda < 0 or deuda > 20 or liquidez < 0 or liquidez > 100:
            continue
            
        data.append({
            "name": cname,
            "roe": roe,
            "deuda": deuda,
            "liquidez": liquidez
        })
        
    return templates.TemplateResponse(request, "partials/salud_chart.html", {"data": data})


# ----------------------------------------------------------
# Proyecciones macrofundadas (Fase 6 — SARIMAX + senda EEE)
# ----------------------------------------------------------

def _period_label(period: str) -> str:
    """'202606' → '2026 T2'."""
    return f"{period[:4]} T{int(period[4:]) // 3}"


@router.get("/proyecciones")
def proyecciones(request: Request, db: Database = Depends(get_db)):
    """Vista de proyecciones: selector de empresa."""
    return templates.TemplateResponse(request, "proyecciones.html", {
        "companies": records(db.get_cmf_companies()),
    })


@router.get("/proyecciones/chart")
def proyecciones_chart(
    request: Request,
    company: str = Query(""),
    steps: int = Query(8),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: fan charts de ingresos y resultado + supuestos macro EEE."""
    empty = templates.TemplateResponse(request, "partials/proyecciones_chart.html",
                                       {"results": [], "meta": None})
    if not company.strip():
        return empty
    match = db.query_cmf_statements(company=company, limit=1)
    if match.empty:
        return empty
    rut, cname = str(match.iloc[0]["rut"]), str(match.iloc[0]["company_name"])
    currency = str(match.iloc[0]["currency"])

    from models.forecast import forecast_company
    forecasts = forecast_company(db, rut, steps=min(max(steps, 4), 12))
    if not forecasts:
        return templates.TemplateResponse(request, "partials/proyecciones_chart.html", {
            "results": [], "meta": {"company_name": cname, "rut": rut},
        })

    results, assumptions, survey_month, model_info = [], None, None, None
    for account, r in forecasts.items():
        hist = [{"period": _period_label(p), "value": v}
                for p, v in r.history.dropna().items()]
        fc = [{"period": _period_label(p), "mean": r.mean[p],
               "lo80": r.ci80.loc[p, "low"], "hi80": r.ci80.loc[p, "high"],
               "lo95": r.ci95.loc[p, "low"], "hi95": r.ci95.loc[p, "high"]}
              for p in r.mean.index]
        results.append({"account": account, "history": hist, "forecast": fc})
        survey_month, model_info = r.survey_month, r.model_info
        if assumptions is None:
            assumptions = [{"period": _period_label(p), **row.to_dict()}
                           for p, row in r.macro_assumptions.iterrows()]

    return templates.TemplateResponse(request, "partials/proyecciones_chart.html", {
        "results": results, "assumptions": assumptions,
        "meta": {
            "company_name": cname, "rut": rut, "currency": currency,
            "survey": survey_month.strftime("%B %Y") if survey_month is not None else "",
            "model": model_info,
        },
    })
