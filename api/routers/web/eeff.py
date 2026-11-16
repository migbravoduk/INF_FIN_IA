"""
api/routers/web/eeff.py — EEFF corporativos y AGF: vista, comparación, ranking, salud y evolución.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import (build_statement_groups, GROUP_LABELS, GROUP_ORDER,
                             GROUP_SLOTS, _assign_balance_sections)
from api.company_profiles import get_profile
from db.database import Database
from api.routers.web._shared import *  # noqa: F401,F403
from api.routers.web._shared import (build_overview_context, BC_CATEGORIAS, BC_UNIDADES, BC_UNIDADES_TASA, _bc_dimension, BC_RANGOS, _bc_desde, _bc_decorar, EEFF_CLASSES, _EEFF_SCOPES, _FLOW_PARTIDAS, _deaccumulate, _select_bank_periods, _RATIO_LABELS, _select_periods, FECU_GROUPS, _FECU_UNIT, _proy_period_label, _select_quarter_periods, _FM_CARTERAS, _fm_periodo_label, _period_label, PROY_CLASSES, _PROY_SIMPLE, _PROY_HYBRID, INST_CLASSES, _RAMOS, _pct, _build_allocation)  # noqa: F401

router = APIRouter()

@router.get("/eeff")
def eeff_hub(request: Request):
    """Hub de estados financieros: menú de clasificación de entidad."""
    return templates.TemplateResponse(request, "eeff_hub.html", {"classes": EEFF_CLASSES})


@router.get("/eeff/corporativos")
@router.get("/eeff/agf")
def eeff(request: Request, db: Database = Depends(get_db)):
    """Vista EEFF del archivo plano CMF, según la clasificación de la ruta."""
    scope = "agf" if request.url.path.endswith("/agf") else "corporativos"
    cfg = _EEFF_SCOPES[scope]
    return templates.TemplateResponse(request, "eeff.html", {
        "companies": records(db.get_cmf_companies(agf=cfg["agf"])),
        "periods": db.get_cmf_periods(),
        "scope": scope,
        "page_title": cfg["title"],
        "page_subtitle": cfg["subtitle"],
    })


@router.get("/eeff/search")
def eeff_search(request: Request, q: str = Query(""), scope: str = Query("corporativos"),
                db: Database = Depends(get_db)):
    """Fragmento HTMX: resultados del autocompletar de empresas (razón social · RUT · sector).
    `scope` acota el universo: 'corporativos' (sin AGF) o 'agf' (solo AGF)."""
    q = q.strip()
    agf_filter = _EEFF_SCOPES.get(scope, _EEFF_SCOPES["corporativos"])["agf"]
    results = []
    if len(q) >= 2:
        df = db.search_cmf_companies(q, limit=15, agf=agf_filter)
        for _, row in df.iterrows():
            rut, name = str(row["rut"]), str(row["company_name"])
            results.append({"rut": rut, "name": name,
                            "sector": get_profile(rut, name)["macro_sector"]})
    return templates.TemplateResponse(request, "partials/eeff_company_results.html",
                                      {"results": results, "q": q})


@router.get("/eeff/table")
def eeff_table(
    request: Request,
    company: str = Query(""),
    rut: str = Query(""),
    serie: str = Query("anual5"),
    db: Database = Depends(get_db),
):
    """
    Fragmento HTMX: vista de empresa fusionada (EEFF + Evolución). Muestra reseña, ratios
    (último período), selectores de gráfico y la MATRIZ evolutiva (cuentas × períodos) de
    cada estado estándar, según el tipo de serie elegido.

    La empresa se resuelve por `rut` (exacto, sin ambigüedad, vía autocompletar). Se admite
    `company` (nombre difuso) por compatibilidad hacia atrás / enlaces antiguos.
    """
    empty = templates.TemplateResponse(request, "partials/eeff_table.html", {"meta": None})
    if rut.strip():
        match = db.query_cmf_statements(rut=rut, limit=1)
    elif company.strip():
        match = db.query_cmf_statements(company=company, limit=1)
    else:
        return empty
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
    is_pct = metric in ("roe", "roa", "margen_neto", "margen_bruto", "pasivo_activo", "margen_ebit", "margen_ebitda")
    return templates.TemplateResponse(request, "partials/ranking_table.html", {
        "rows": rows, "metric_label": _RATIO_LABELS.get(metric, metric),
        "period": period_int, "is_pct": is_pct,
    })


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
    is_pct = metric in ("roe", "roa", "margen_neto", "margen_bruto", "pasivo_activo", "margen_ebit", "margen_ebitda")
    return templates.TemplateResponse(request, "partials/ratios_serie.html", {
        "series": series, "label": _RATIO_LABELS.get(metric, metric),
        "is_pct": is_pct,
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
