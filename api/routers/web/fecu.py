"""
api/routers/web/fecu.py — EEFF con plan FECU propio: seguros e intermediarios de valores.
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

@router.get("/eeff/seguros")
def eeff_seguros(request: Request, db: Database = Depends(get_db)):
    """Vista EEFF de compañías de seguros (vida y generales)."""
    df = db.get_insurer_list()
    entities = [{"rut": r["rut"], "company_name": r["company_name"],
                 "kind": r["insurance_type"]} for _, r in df.iterrows()]
    return templates.TemplateResponse(request, "fecu_entidad.html", {
        "page_title": "Estados financieros de compañías de seguros",
        "page_subtitle": "CMF · FECU de seguros (Circulares N°2022 y N°2050) — plan de cuentas "
                         "propio del negocio asegurador. Cifras en miles de CLP.",
        "endpoint": "/eeff/seguros/table",
        "kind_label": "Ramo",
        "kinds": [{"value": "vida", "label": "Vida"},
                  {"value": "generales", "label": "Generales"}],
        "entities": entities,
        "groups": FECU_GROUPS,
    })


@router.get("/eeff/seguros/table")
def eeff_seguros_table(
    request: Request,
    rut: str = Query(""),
    kind: str = Query("vida"),
    serie: str = Query("trim8"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: matrices evolutivas de una compañía de seguros."""
    empty = templates.TemplateResponse(request, "partials/fecu_statements.html", {"meta": None})
    if not rut.strip():
        return empty
    periods = _select_quarter_periods(
        db.get_insurer_periods(insurance_type=kind), serie)
    if not periods:
        return empty
    statements = []
    for g in FECU_GROUPS:
        ev = db.get_insurer_evolution(rut, periods, g["code"], insurance_type=kind)
        if ev["accounts"]:
            statements.append({"label": g["label"], "code": g["code"], "ev": ev})
    if not statements:
        return empty
    name = next((e["company_name"] for _, e in db.get_insurer_list(insurance_type=kind).iterrows()
                 if e["rut"] == rut), rut)
    meta = {"company_name": name, "rut": rut, "kind": f"seguros {kind}",
            "periods": periods, "unit": _FECU_UNIT}
    return templates.TemplateResponse(request, "partials/fecu_statements.html",
                                      {"meta": meta, "statements": statements})


@router.get("/eeff/intermediarios")
def eeff_intermediarios(request: Request, db: Database = Depends(get_db)):
    """Vista EEFF de intermediarios de valores (corredores de bolsa y agentes)."""
    df = db.get_broker_list()
    entities = [{"rut": r["rut"], "company_name": r["company_name"],
                 "kind": r["broker_type"]} for _, r in df.iterrows()]
    return templates.TemplateResponse(request, "fecu_entidad.html", {
        "page_title": "Estados financieros de intermediarios de valores",
        "page_subtitle": "CMF · FECU IFRS de corredores de bolsa y agentes de valores — plan de "
                         "cuentas propio de intermediarios. Cifras en miles de CLP.",
        "endpoint": "/eeff/intermediarios/table",
        "kind_label": "Tipo",
        "kinds": [{"value": "CORREDORES", "label": "Corredores de bolsa"},
                  {"value": "AGENTES", "label": "Agentes de valores"}],
        "entities": entities,
        "groups": FECU_GROUPS,
    })


@router.get("/eeff/intermediarios/table")
def eeff_intermediarios_table(
    request: Request,
    rut: str = Query(""),
    kind: str = Query(""),
    serie: str = Query("trim8"),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: matrices evolutivas de un intermediario de valores."""
    empty = templates.TemplateResponse(request, "partials/fecu_statements.html", {"meta": None})
    if not rut.strip():
        return empty
    periods = _select_quarter_periods(db.get_broker_periods(), serie)
    if not periods:
        return empty
    statements = []
    for g in FECU_GROUPS:
        ev = db.get_broker_evolution(rut, periods, g["code"])
        if ev["accounts"]:
            statements.append({"label": g["label"], "code": g["code"], "ev": ev})
    if not statements:
        return empty
    row = next((e for _, e in db.get_broker_list().iterrows() if e["rut"] == rut), None)
    meta = {"company_name": row["company_name"] if row is not None else rut, "rut": rut,
            "kind": (row["broker_type"].capitalize() if row is not None else kind),
            "periods": periods, "unit": _FECU_UNIT}
    return templates.TemplateResponse(request, "partials/fecu_statements.html",
                                      {"meta": meta, "statements": statements})
