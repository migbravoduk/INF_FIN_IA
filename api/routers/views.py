"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/"      → panel multi-fuente (overview.html), KPIs reales + gráfico UF.
"/eeff"  → estados financieros corporativos (CMF): selector empresa/período + tabla.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from db.database import Database

router = APIRouter()


# ----------------------------------------------------------
# Panel multi-fuente
# ----------------------------------------------------------

def build_overview_context(db: Database) -> dict:
    """Contexto del panel: KPIs + serie UF (90 días) para el gráfico. Reusable por el export estático."""
    kpi = db.get_overview_kpis()
    since = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    uf_series = records(db.get_series(db.KPI_SERIES["uf"], from_date=since))
    return {"kpi": kpi, "uf_series": uf_series}


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
    period: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: estados financieros de una empresa/período, agrupados por estado."""
    df = db.query_cmf_statements(
        rut=rut, period=int(period) if period else None, limit=2000,
    )

    meta, groups = None, []
    if not df.empty:
        first = df.iloc[0]
        meta = {
            "company_name": str(first["company_name"]),
            "rut": str(first["rut"]),
            "period": int(first["period"]),
            "report_type": str(first["report_type"]),
            "currency": str(first["currency"]),
        }
        # Agrupa por estado (ESF, ERFG, ...) preservando el orden de la consulta.
        for group_name, sub in df.groupby("statement_group", sort=False):
            groups.append({
                "group": group_name or "—",
                "rows": [{"account_name": str(r["account_name"]), "value": r["value"]}
                         for _, r in sub.iterrows()],
            })

    return templates.TemplateResponse(request, "partials/eeff_table.html", {
        "meta": meta, "groups": groups,
    })
