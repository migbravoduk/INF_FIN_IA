"""
api/routers/web/banca.py — Estados financieros bancarios con desglose por moneda.
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
        report_type=report_type, limit=50000, en_pesos=True,
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


@router.get("/banca/evolucion")
def banca_evolucion(
    request: Request,
    bank: Optional[str] = Query(None),
    report_type: str = Query("balance", alias="type"),
    serie: str = Query("cons12"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: matriz evolutiva del TOTAL bancario (cuentas × períodos)."""
    empty = templates.TemplateResponse(request, "partials/statement_matrix.html", {"st": {"ev": {"accounts": []}}})
    if not bank:
        return empty
    periods = _select_bank_periods(db.get_bank_periods(bank_code=bank), serie)
    if not periods:
        return empty
    ev = db.get_bank_statement_evolution(bank, periods, report_type)
    label = "Balance" if report_type == "balance" else "Estado de resultados"
    st = {"label": f"{label} — evolución del total (pesos)",
          "code": "TOTAL", "ev": ev}
    return templates.TemplateResponse(request, "partials/statement_matrix.html", {"st": st})


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
