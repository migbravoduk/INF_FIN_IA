"""
api/routers/web/panel.py — Panel multi-fuente y explorador del Banco Central.
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

@router.get("/")
def overview(request: Request, db: Database = Depends(get_db)):
    """Panel multi-fuente (página principal)."""
    ctx = build_overview_context(db)
    return templates.TemplateResponse(request, "overview.html", ctx)


@router.get("/banco-central")
def banco_central(request: Request, db: Database = Depends(get_db)):
    """Explorador de las series macro del Banco Central (catálogo, serie y comparación)."""
    catalogo = [_bc_decorar(r) for r in db.get_macro_catalog()]
    efectivas = [r for r in catalogo if r["category"] != db.EXPECTATIVAS_CATEGORY]
    expectativas = [r for r in catalogo if r["category"] == db.EXPECTATIVAS_CATEGORY]

    # Agrupadas por categoría para armar los <optgroup> del selector.
    grupos: dict[str, list] = {}
    for r in efectivas:
        grupos.setdefault(r["cat_label"], []).append(r)

    return templates.TemplateResponse(request, "banco_central.html", {
        "grupos": grupos,
        "n_efectivas": len(efectivas),
        "n_expectativas": len(expectativas),
        "rangos": list(BC_RANGOS),
        "default_serie": db.KPI_SERIES["ipc_v12"],
        "default_b": db.KPI_SERIES["tpm"],
    })


@router.get("/banco-central/catalogo")
def banco_central_catalogo(request: Request, tipo: str = Query("efectivas"),
                           db: Database = Depends(get_db)):
    """Fragmento HTMX: catálogo de series con cobertura y último dato."""
    filas = [_bc_decorar(r) for r in
             db.get_macro_catalog(expectativas=(tipo == "expectativas"))]
    return templates.TemplateResponse(request, "partials/bc_catalogo.html", {
        "filas": filas, "tipo": tipo,
    })


@router.get("/banco-central/serie")
def banco_central_serie(request: Request, series_id: str = Query(...),
                        rango: str = Query("5a"), db: Database = Depends(get_db)):
    """Fragmento HTMX: gráfico de una serie + estadísticas del rango."""
    meta = db.get_series_meta(series_id)
    if not meta:
        return templates.TemplateResponse(request, "partials/bc_serie.html",
                                          {"meta": None, "serie": [], "stats": None})
    desde = _bc_desde(rango)
    serie = records(db.get_series(series_id, from_date=desde))
    stats = db.get_series_stats(series_id, from_date=desde)
    return templates.TemplateResponse(request, "partials/bc_serie.html", {
        "meta": _bc_decorar({**meta, "n_obs": None, "first_date": None,
                             "last_date": None, "last_value": None}),
        "serie": serie, "stats": stats, "rango": rango,
        "es_expectativa": meta["category"] == db.EXPECTATIVAS_CATEGORY,
    })


@router.get("/banco-central/comparar")
def banco_central_comparar(request: Request, a: str = Query(...), b: str = Query(...),
                           rango: str = Query("5a"), db: Database = Depends(get_db)):
    """Fragmento HTMX: dos series superpuestas con eje Y independiente cada una.

    El doble eje no es decoración: la UF va en decenas de miles y la TPM en unidades,
    así que en un solo eje la segunda serie queda pegada al cero y no se lee.
    """
    desde = _bc_desde(rango)
    out = []
    for sid in (a, b):
        meta = db.get_series_meta(sid)
        if not meta:
            continue
        out.append({"meta": _bc_decorar({**meta, "n_obs": None, "first_date": None,
                                         "last_date": None, "last_value": None}),
                    "serie": records(db.get_series(sid, from_date=desde))})
    misma = (len(out) == 2 and
             _bc_dimension(out[0]["meta"]["unit"]) == _bc_dimension(out[1]["meta"]["unit"]))
    return templates.TemplateResponse(request, "partials/bc_comparar.html", {
        "series": out, "misma_unidad": misma,
        "ambas_tasas": misma and out and out[0]["meta"]["es_tasa"],
    })
