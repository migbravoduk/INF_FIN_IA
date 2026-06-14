"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/"      → panel multi-fuente (overview.html), KPIs reales + gráfico UF.
"/eeff"  → estados financieros corporativos (CMF): selector empresa/período + tabla.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import build_statement_groups
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

    meta, groups, graph_accounts = None, [], []
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

    return templates.TemplateResponse(request, "partials/eeff_table.html", {
        "meta": meta, "groups": groups, "graph_accounts": graph_accounts,
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


@router.get("/comparar/chart")
def comparar_chart(
    request: Request,
    account: str = Query(""),
    c1: str = Query(""), c2: str = Query(""), c3: str = Query(""),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: overlay de una misma partida en varias empresas, base 100."""
    if not account.strip():
        return templates.TemplateResponse(request, "partials/compare_chart.html",
                                          {"multi": None, "account": None})
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
        pts = [{"period": str(int(r["period"])), "value": r["value"]} for _, r in s.iterrows()]
        if not pts:
            continue
        base = pts[0]["value"] or None
        multi.append({
            "name": cname,
            "points": [{"x": p["period"], "value": (p["value"] / base * 100.0) if base else None}
                       for p in pts],
        })
    return templates.TemplateResponse(request, "partials/compare_chart.html", {
        "multi": multi, "account": account,
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
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: valor cuota (2 años). fund='TODOS' compara los 5 fondos (base 100)."""
    since = (dt.date.today() - dt.timedelta(days=730)).isoformat()

    if fund == "TODOS":
        multi = []
        for f in ("A", "B", "C", "D", "E"):
            df = db.query_sp_quota_values(afp=afp, fund=f, from_date=since, limit=5000)
            if df.empty:
                continue
            pts = records(df.sort_values("date"))
            base = pts[0]["quota_value"] or None
            multi.append({
                "name": "Fondo " + f,
                "points": [{"date": p["date"],
                            "value": (p["quota_value"] / base * 100.0) if base else None}
                           for p in pts],
            })
        return templates.TemplateResponse(request, "partials/afp_chart.html", {
            "multi": multi, "series": None, "latest": None, "afp": afp, "fund": fund,
        })

    df = db.query_sp_quota_values(afp=afp, fund=fund, from_date=since, limit=5000)
    if not df.empty:
        df = df.sort_values("date")
    series = records(df)
    return templates.TemplateResponse(request, "partials/afp_chart.html", {
        "series": series, "latest": series[-1] if series else None,
        "multi": None, "afp": afp, "fund": fund,
    })
