"""
api/routers/web/ffmm.py — Fondos mutuos (CMF).
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

@router.get("/fondos-mutuos")
def fondos_mutuos(request: Request, db: Database = Depends(get_db)):
    """Fondos mutuos: rendimientos por administradora/tipo y zoom al portafolio."""
    fechas = db.get_mutual_fund_quote_dates()
    periodos = db.get_mutual_fund_periods()
    return templates.TemplateResponse(request, "fondos_mutuos.html", {
        "fechas": fechas,
        "desde_default": fechas[-1] if fechas else "",
        "hasta_default": fechas[0] if fechas else "",
        "agfs": db.get_mutual_fund_agfs(),
        "tipos": db.get_mutual_fund_types_map(),
        "funds": db.get_mutual_funds_options(periodos[0]) if periodos else [],
        "period_label": _fm_periodo_label(periodos[0] if periodos else None),
        "n_fondos": len({f["fund_run"] for f in db.get_mutual_funds_options(periodos[0])}) if periodos else 0,
    })


@router.get("/fondos-mutuos/rendimientos")
def fondos_mutuos_rendimientos(request: Request, desde: str = Query(""), hasta: str = Query(""),
                               agf: str = Query(""), tipo: str = Query(""),
                               db: Database = Depends(get_db)):
    """Fragmento HTMX: variación del valor cuota entre dos fechas, filtrable."""
    fechas = db.get_mutual_fund_quote_dates()
    if not fechas:
        return templates.TemplateResponse(request, "partials/fondos_mutuos_rend.html",
                                          {"rows": [], "sin_datos": True})
    desde = desde if desde in fechas else fechas[-1]
    hasta = hasta if hasta in fechas else fechas[0]
    if desde >= hasta:                       # rango invertido o de un solo día
        desde = fechas[-1]

    tipo_int = int(tipo) if tipo.isdigit() else None
    rows = db.get_mutual_fund_returns(desde, hasta, agf=agf, fund_type=tipo_int, limit=40)
    tipos = db.get_mutual_fund_types_map()
    return templates.TemplateResponse(request, "partials/fondos_mutuos_rend.html", {
        "rows": rows, "desde": desde, "hasta": hasta,
        "agf": agf, "tipo_label": tipos.get(tipo_int) if tipo_int else "",
        "tipos": tipos,
        "n_total": len(db.get_mutual_fund_returns(desde, hasta, agf=agf,
                                                  fund_type=tipo_int, limit=99999)),
    })


@router.get("/fondos-mutuos/comparado")
def fondos_mutuos_comparado(request: Request, desde: str = Query(""), hasta: str = Query(""),
                            agf: str = Query(""), tipo: str = Query(""),
                            db: Database = Depends(get_db)):
    """Fragmento HTMX: trayectorias base 100 de mejores y peores, contra la mediana del grupo."""
    fechas = db.get_mutual_fund_quote_dates()
    if not fechas:
        return templates.TemplateResponse(request, "partials/fondos_mutuos_comparado.html",
                                          {"paths": []})
    desde = desde if desde in fechas else fechas[-1]
    hasta = hasta if hasta in fechas else fechas[0]
    if desde >= hasta:
        desde = fechas[-1]

    tipo_int = int(tipo) if tipo.isdigit() else None
    datos = db.get_mutual_fund_paths(desde, hasta, agf=agf, fund_type=tipo_int, top_n=6)
    tipos = db.get_mutual_fund_types_map()
    return templates.TemplateResponse(request, "partials/fondos_mutuos_comparado.html", {
        **datos, "desde": desde, "hasta": hasta, "agf": agf,
        "tipo_label": tipos.get(tipo_int) if tipo_int else "",
    })


@router.get("/fondos-mutuos/panel")
def fondos_mutuos_panel(request: Request, fund_run: str = Query(""),
                        series: str = Query(""), db: Database = Depends(get_db)):
    """Fragmento HTMX: zoom a un fondo — valor cuota en el tiempo + composición de cartera.

    `series` llega cuando se entra haciendo clic en una fila de la tabla o en una línea del
    gráfico, que son pares fondo-serie. Sin ella se cae a la serie de mayor patrimonio, que
    es la que representa al fondo cuando no se pidió una en particular.
    """
    if not fund_run:
        return templates.TemplateResponse(request, "partials/fondos_mutuos_panel.html",
                                          {"comp": None, "serie": []})

    identidad = db.get_mutual_fund_identity(fund_run)
    serie, series_sel = db.get_mutual_fund_quote_series(fund_run, series)
    periodos = db.get_mutual_fund_periods()
    comp = db.get_mutual_fund_composition(fund_run, periodos[0]) if periodos else {}

    var_pct = None
    if len(serie) >= 2 and serie[0]["quota_value"]:
        var_pct = (serie[-1]["quota_value"] / serie[0]["quota_value"] - 1) * 100

    tipos = db.get_mutual_fund_types_map()
    # Rentabilidades oficiales de la CMF para ESTA serie. El comparador no cubre todas las
    # series (67% de los pares), así que puede venir vacío y la vista lo dice.
    oficial = db.get_mutual_fund_official_returns(fund_run, series_sel)

    # Costos. La remuneración y el gasto operacional se devengan del patrimonio, así que YA
    # están dentro de `var_pct`: restarlos otra vez sería contarlos dos veces. Solo la
    # comisión de colocación se le cobra al partícipe y se descuenta aparte.
    costos = db.get_mutual_fund_fees(fund_run, series_sel)
    var_pct_neta = None
    if var_pct is not None and costos.get("com_colocacion"):
        var_pct_neta = ((1 + var_pct / 100) * (1 - costos["com_colocacion"] / 100) - 1) * 100
    return templates.TemplateResponse(request, "partials/fondos_mutuos_panel.html", {
        "comp": comp or None,
        "fund_run": fund_run,
        "oficial": oficial,
        "costos": costos,
        "var_pct_neta": var_pct_neta,
        "identidad": identidad,
        "tipo_label": tipos.get(identidad.get("fund_type")) if identidad else None,
        "serie": serie, "series_sel": series_sel, "var_pct": var_pct,
        # Si la serie vino en la URL (clic en fila o línea) NO es "la de mayor
        # patrimonio": es la que el usuario eligió, y el rótulo debe decirlo.
        "serie_elegida": bool(series),
        "carteras_label": _FM_CARTERAS,
        "posiciones": db.get_mutual_fund_positions(fund_run, periodos[0], limit=20) if periodos and comp else [],
        "period_label": _fm_periodo_label(periodos[0] if periodos else None),
    })
