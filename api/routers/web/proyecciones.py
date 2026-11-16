"""
api/routers/web/proyecciones.py — Proyecciones de EEFF (híbrido y SARIMAX).
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

@router.get("/proyecciones")
def proyecciones_hub(request: Request):
    """Hub de proyecciones: menú de clasificación de entidad."""
    return templates.TemplateResponse(request, "proyecciones_hub.html",
                                      {"classes": PROY_CLASSES})


@router.get("/proyecciones/corporativos")
@router.get("/proyecciones/agf")
def proyecciones(request: Request, db: Database = Depends(get_db)):
    """Vista de proyecciones con el modelo híbrido (corporativos o AGF)."""
    scope = "agf" if request.url.path.endswith("/agf") else "corporativos"
    cfg = _PROY_HYBRID[scope]
    return templates.TemplateResponse(request, "proyecciones.html", {
        "companies": records(db.get_cmf_companies(agf=cfg["agf"])),
        "page_title": cfg["title"],
        "page_subtitle": cfg["subtitle"],
    })


# Rutas explícitas (NO un catch-all `/{clase}`: capturaría también `/proyecciones/chart`).
@router.get("/proyecciones/bancos")
@router.get("/proyecciones/seguros")
@router.get("/proyecciones/intermediarios")
def proyecciones_simple(request: Request, db: Database = Depends(get_db)):
    """Vista de proyecciones SARIMAX para bancos, seguros e intermediarios."""
    clase = request.url.path.rsplit("/", 1)[-1]
    cfg = _PROY_SIMPLE[clase]

    if clase == "bancos":
        entities = [{"id": r["bank_code"], "name": r["bank_name"], "kind": ""}
                    for _, r in db.get_bank_list().iterrows()]
    elif clase == "seguros":
        entities = [{"id": r["rut"], "name": r["company_name"], "kind": r["insurance_type"]}
                    for _, r in db.get_insurer_list().iterrows()]
    else:
        entities = [{"id": r["rut"], "name": r["company_name"], "kind": r["broker_type"]}
                    for _, r in db.get_broker_list().iterrows()]

    return templates.TemplateResponse(request, "proyecciones_simple.html", {
        "page_title": cfg["title"], "page_subtitle": cfg["subtitle"],
        "endpoint": f"/proyecciones/{clase}/chart",
        "kind_label": cfg["kind_label"], "kinds": cfg["kinds"],
        "entities": entities, "horizons": cfg["horizons"],
    })


@router.get("/proyecciones/bancos/chart")
@router.get("/proyecciones/seguros/chart")
@router.get("/proyecciones/intermediarios/chart")
def proyecciones_simple_chart(
    request: Request,
    entity: str = Query(""),
    kind: str = Query(""),
    steps: int = Query(4),
    db: Database = Depends(get_db),
):
    """Fragmento HTMX: fan charts SARIMAX de una entidad no corporativa."""
    from models.simple_forecast import deaccumulate_ytd, forecast_series

    clase = request.url.path.split("/")[2]
    cfg = _PROY_SIMPLE.get(clase)
    empty = templates.TemplateResponse(request, "partials/proyecciones_simple.html",
                                       {"results": [], "meta": None})
    if not cfg or not entity.strip():
        return empty

    steps = min(max(int(steps), 2), 24)
    monthly = cfg["monthly"]

    # Nombre y subtipo de la entidad para el encabezado
    if clase == "bancos":
        row = next((r for _, r in db.get_bank_list().iterrows()
                    if str(r["bank_code"]) == entity), None)
        ent_name = row["bank_name"] if row is not None else entity
        ent_kind, ent_id = "", f"Ficha {entity}"
    elif clase == "seguros":
        row = next((r for _, r in db.get_insurer_list().iterrows() if r["rut"] == entity), None)
        ent_name = row["company_name"] if row is not None else entity
        ent_kind = f"seguros {row['insurance_type']}" if row is not None else kind
        ent_id = f"RUT {entity}"
    else:
        row = next((r for _, r in db.get_broker_list().iterrows() if r["rut"] == entity), None)
        ent_name = row["company_name"] if row is not None else entity
        ent_kind = row["broker_type"].capitalize() if row is not None else kind
        ent_id = f"RUT {entity}"

    results = []
    for acc in cfg["accounts"]:
        if clase == "bancos":
            serie = db.get_bank_total_series(entity, acc["key"], acc["report"])
        elif clase == "seguros":
            serie = db.get_insurer_account_series(entity, acc["key"],
                                                  insurance_type=(kind or "vida"))
        else:
            serie = db.get_broker_account_series(entity, acc["key"])

        if serie is None or serie.empty:
            continue
        if acc["accumulated"]:
            serie = deaccumulate_ytd(serie)

        fc = forecast_series(serie, steps, monthly=monthly, is_stock=acc["stock"])
        if fc is None:
            continue
        note = ("nivel (stock)" if acc["stock"]
                else ("flujo del período — desacumulado del acumulado anual"))
        results.append({
            "account": acc["label"], "unit_note": note,
            # El spec es POR SERIE: stocks y flujos usan specs distintos, así que no puede
            # ir en `meta` (se pisarían entre sí).
            "spec": fc["spec"], "n_obs": fc["n_obs"],
            "history": [{"period": _proy_period_label(p, monthly), "value": float(v)}
                        for p, v in fc["history"].items()],
            "forecast": [{"period": _proy_period_label(p, monthly),
                          "mean": float(fc["mean"][p]),
                          "lo80": float(fc["ci80"].loc[p, "low"]),
                          "hi80": float(fc["ci80"].loc[p, "high"]),
                          "lo95": float(fc["ci95"].loc[p, "low"]),
                          "hi95": float(fc["ci95"].loc[p, "high"])}
                         for p in fc["mean"].index],
        })

    meta = {"entity_name": ent_name, "rut": ent_id, "kind": ent_kind,
            "unit": cfg["unit"], "cadence": cfg["cadence"]}
    return templates.TemplateResponse(request, "partials/proyecciones_simple.html",
                                      {"results": results, "meta": meta})


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

    from models.hybrid import forecast_company_hybrid
    hybrid = forecast_company_hybrid(db, rut, steps=min(max(steps, 4), 12))
    forecasts, structural = hybrid["results"], hybrid["structural"]
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

    # Narrativa estructural: activos, rotación y margen proyectados
    structural_rows = None
    if structural is not None:
        structural_rows = [{
            "period": _period_label(p),
            "activos": float(structural["activos"][p]),
            "rotacion": float(structural["rotacion"][p]),
            "margen": (float(structural["margen"][p])
                       if structural["margen"] is not None else None),
        } for p in structural["periods"]]

    return templates.TemplateResponse(request, "partials/proyecciones_chart.html", {
        "results": results, "assumptions": assumptions,
        "structural": structural_rows,
        "meta": {
            "company_name": cname, "rut": rut, "currency": currency,
            "survey": survey_month.strftime("%B %Y") if survey_month is not None else "",
            "model": model_info,
        },
    })
