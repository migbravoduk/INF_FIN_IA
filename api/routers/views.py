"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/"      → panel multi-fuente (overview.html), KPIs reales + gráfico UF.
"/eeff"  → estados financieros corporativos (CMF): selector empresa/período + tabla.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import build_statement_groups, GROUP_LABELS, GROUP_ORDER, _assign_balance_sections
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
    rut: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    period: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: estados financieros de una empresa/período, agrupados por estado."""
    period_int = int(period) if period else None

    # Si llega el nombre (buscador), resolverlo a un RUT único para no mezclar empresas.
    if company and not rut:
        match = db.query_cmf_statements(company=company, limit=1)
        if not match.empty:
            rut = str(match.iloc[0]["rut"])

    df = db.query_cmf_statements(rut=rut, period=period_int, limit=2000)

    meta, groups, graph_accounts, ratios = None, [], [], None
    if not df.empty:
        first = df.iloc[0]
        meta = {
            "company_name": str(first["company_name"]),
            "rut": str(first["rut"]),
            "period": int(first["period"]),
            "report_type": str(first["report_type"]),
            "currency": str(first["currency"]),
        }
        groups = build_statement_groups(df)
        graph_accounts = db.get_company_graphable_accounts(rut)
        ratios = db.get_company_ratios(rut, meta["period"])
        meta["profile"] = get_profile(meta["rut"], meta["company_name"])

    return templates.TemplateResponse(request, "partials/eeff_table.html", {
        "meta": meta, "groups": groups, "graph_accounts": graph_accounts, "ratios": ratios,
    })


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

    if delta and series:
        # Desacumular: dentro de un mismo año, valor = acumulado - acumulado del período previo.
        out, prev_by_year = [], {}
        for p in series:
            year = p["period"][:4]
            val = p["value"] - prev_by_year[year] if year in prev_by_year else p["value"]
            out.append({"period": p["period"], "value": val})
            prev_by_year[year] = p["value"]
        series = out

    suffix = " (variación intra-anual)" if delta else ""
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
    df = db.query_bank_statements(
        bank_code=bank, period=int(period) if period else None,
        report_type=report_type, limit=2000,
    )
    meta, rows, graph_accounts = None, [], []
    if not df.empty:
        first = df.iloc[0]
        meta = {
            "bank_name": str(first["bank_name"]),
            "bank_code": str(first["bank_code"]),
            "period": int(first["period"]),
        }
        rows = records(df)
        graph_accounts = db.get_bank_graphable_accounts(bank, report_type)
    return templates.TemplateResponse(request, "partials/banca_table.html", {
        "meta": meta, "rows": rows, "report_type": report_type, "graph_accounts": graph_accounts,
    })


@router.get("/comparar")
def comparar(request: Request, db: Database = Depends(get_db)):
    """Vista de comparación: misma partida en varias empresas (base 100)."""
    return templates.TemplateResponse(request, "compare.html", {
        "companies": records(db.get_cmf_companies()),
        "partidas": [
            "Total de activos", "Total de patrimonio", "Total de pasivos",
            "Ganancia (pérdida)", "Ganancia bruta", "Costo de ventas",
        ],
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


@router.get("/comparar/chart")
def comparar_chart(
    request: Request,
    account: str = Query(""),
    c1: str = Query(""), c2: str = Query(""), c3: str = Query(""),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: overlay de una misma partida en varias empresas."""
    if not account.strip():
        return templates.TemplateResponse(request, "partials/compare_chart.html",
                                          {"multi": None, "account": None, "mode": None})
    is_flow = account in _FLOW_PARTIDAS
    multi = []
    for name in (c1, c2, c3):
        name = name.strip()
        if not name:
            continue
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
    return templates.TemplateResponse(request, "ranking.html", {
        "periods": db.get_cmf_periods(), "ratios": _RATIO_LABELS,
    })


@router.get("/ranking/table")
def ranking_table(
    request: Request,
    metric: str = Query("roe"),
    period: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: top empresas por un ratio en un período."""
    periods = db.get_cmf_periods()
    period_int = int(period) if period else (periods[0] if periods else None)
    rows = db.get_ratios_ranking(period_int, metric, 15) if period_int else []
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


# Estados ofrecidos en la vista evolutiva (código → etiqueta), en orden lógico.
_ESTADOS = [(c, GROUP_LABELS[c]) for c in GROUP_ORDER if c in GROUP_LABELS]


@router.get("/evolucion")
def evolucion(request: Request, db: Database = Depends(get_db)):
    """Vista evolutiva: un estado financiero de una empresa a través de varios períodos."""
    return templates.TemplateResponse(request, "evolucion.html", {
        "companies": records(db.get_cmf_companies()), "estados": _ESTADOS,
    })


@router.get("/evolucion/table")
def evolucion_table(
    request: Request,
    company: str = Query(""),
    serie: str = Query("anual5"),
    estado: str = Query("ESF C/NC"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: matriz de un estado (filas = cuentas, columnas = períodos)."""
    if not company.strip():
        return templates.TemplateResponse(request, "partials/evolucion_table.html", {"ev": None})
    match = db.query_cmf_statements(company=company, limit=1)
    if match.empty:
        return templates.TemplateResponse(request, "partials/evolucion_table.html", {"ev": None})
    rut, cname = str(match.iloc[0]["rut"]), str(match.iloc[0]["company_name"])
    periods = _select_periods(db.get_company_periods(rut), serie)
    ev = db.get_statement_evolution(rut, periods, estado)
    if estado.startswith("ESF"):
        _assign_balance_sections(ev["accounts"])
    return templates.TemplateResponse(request, "partials/evolucion_table.html", {
        "ev": ev, "company": cname, "rut": rut, "profile": get_profile(rut, cname),
        "estado_label": GROUP_LABELS.get(estado, estado), "estado_code": estado,
    })


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
    series = [{"period": str(int(r["period"])), "value": r["value"]}
              for _, r in df.iterrows()]
    return templates.TemplateResponse(request, "partials/eeff_serie.html", {
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

    def clean(g: str) -> str:
        g = str(g).strip()
        if g.lower().startswith("total "):
            g = g[6:].strip()
        return (g[:1].upper() + g[1:].lower()) if g else g

    rows = [{"glosa": clean(r["instrument_glosa"]), "pct": float(r["porcentaje"])}
            for _, r in df.iterrows()]
    return templates.TemplateResponse(request, "partials/afp_cartera.html", {
        "rows": rows, "period": period, "fund": fund,
    })
