"""
api/routers/web/afp.py — Fondos de pensiones (SP): evolución, cartera, rentabilidad neta y fondos.
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
    # Nemotécnicos extranjeros traducidos con el glosario oficial SP
    from api.sp_glossary import describe
    foreign_rows = []
    for _, r in df_foreign.iterrows():
        g = describe(r["instrument_glosa"])
        foreign_rows.append({"glosa": g["label"], "code": g["code"],
                             "desc": g["desc"], "pct": float(r["porcentaje"])})

    return templates.TemplateResponse(request, "partials/afp_cartera.html", {
        "rows": rows, "foreign_rows": foreign_rows, "period": period, "fund": fund,
    })


@router.get("/fondos")
def fondos(request: Request, db: Database = Depends(get_db)):
    """Vista de fondos de pensiones: retornos por horizonte y composición por AFP."""
    return templates.TemplateResponse(request, "fondos.html", {
        "funds": db.get_sp_fund_types(),
    })


@router.get("/fondos/panel")
def fondos_panel(request: Request, fund: str = Query("A"), db: Database = Depends(get_db)):
    """Fragmento HTMX: retornos multi-horizonte + composición vigente de un fondo."""
    returns = db.get_fund_horizon_returns(fund)
    composition = db.get_fund_composition_by_afp(fund)
    return templates.TemplateResponse(request, "partials/fondos_panel.html", {
        "fund": fund, "ret": returns, "comp": composition,
    })


@router.get("/afp/neta")
def afp_neta(
    request: Request,
    fund: str = Query("C"),
    salary: int = Query(1_000_000),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: ranking de AFP por rentabilidad neta de comisiones (12m corridos)."""
    salary = min(max(salary, 100_000), 10_000_000)
    sim = db.get_afp_net_simulation(fund=fund, salary=float(salary), months=12)
    return templates.TemplateResponse(request, "partials/afp_neta.html", {
        "sim": sim, "fund": fund, "salary": salary,
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
