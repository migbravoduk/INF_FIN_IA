"""
api/routers/views.py — Rutas HTML del dashboard (Jinja2 + HTMX + Plotly).

"/"      → panel multi-fuente (overview.html), KPIs reales + gráfico UF.
"/eeff"  → estados financieros corporativos (CMF): selector empresa/período + tabla.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import (build_statement_groups, GROUP_LABELS, GROUP_ORDER,
                             GROUP_SLOTS, _assign_balance_sections)
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

# Clasificaciones de entidad del hub de EEFF. Cada una tiene su plan de cuentas y fuente;
# bancos conserva su propia vista/lógica (desglose por moneda), el resto comparte el patrón.
EEFF_CLASSES = [
    {"key": "corporativos", "url": "/eeff/corporativos", "icon": "🏢", "title": "Corporativos",
     "desc": "Sociedades anónimas y demás emisores que no caen en otra clasificación.",
     "meta": "CMF · archivo plano IFRS · trimestral"},
    {"key": "bancos", "url": "/banca", "icon": "🏦", "title": "Bancos",
     "desc": "Balances y resultados con desglose por tipo de moneda (lógica propia).",
     "meta": "CMF/SBIF · mensual"},
    {"key": "seguros", "url": "/eeff/seguros", "icon": "🛡️", "title": "Compañías de seguros",
     "desc": "Aseguradoras de vida y generales, con su plan de cuentas FECU.",
     "meta": "CMF · FECU seguros · trimestral"},
    {"key": "intermediarios", "url": "/eeff/intermediarios", "icon": "📈",
     "title": "Intermediarios de valores",
     "desc": "Corredores de bolsa y agentes de valores.",
     "meta": "CMF · FECU IFRS · trimestral"},
    {"key": "agf", "url": "/eeff/agf", "icon": "💼", "title": "AGF",
     "desc": "Administradoras generales de fondos y administradoras de fondos afines.",
     "meta": "CMF · archivo plano IFRS · trimestral"},
]

# Configuración de las vistas que reusan la lógica EEFF corporativa (archivo plano).
_EEFF_SCOPES = {
    "corporativos": {
        "agf": False,
        "title": "Estados financieros corporativos",
        "subtitle": "CMF · Empresas y Mercados — balance, resultados y flujo de caja, en serie "
                    "(vista evolutiva). Excluye bancos, seguros, intermediarios y AGF.",
    },
    "agf": {
        "agf": True,
        "title": "Estados financieros de AGF",
        "subtitle": "CMF · Administradoras generales de fondos — mismo formato IFRS que los "
                    "corporativos, separadas para analizarlas como grupo.",
    },
}


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
        report_type=report_type, limit=50000,
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


def _select_bank_periods(allp: list, serie: str) -> list:
    """Selecciona períodos mensuales bancarios según el tipo de serie evolutiva."""
    allp = sorted(int(p) for p in allp)
    if not allp:
        return []
    if serie.startswith("anual"):          # cierres anuales (diciembre)
        n = 10 if "10" in serie else 5
        return [p for p in allp if p % 100 == 12][-n:]
    if serie.startswith("mismomes"):       # mismo mes del año, varios años atrás
        n = 10 if "10" in serie else 5
        m = max(allp) % 100
        return [p for p in allp if p % 100 == m][-n:]
    if serie.startswith("cons"):           # meses consecutivos
        n = 24 if "24" in serie else 12
        return allp[-n:]
    return allp[-12:]


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


_RATIO_LABELS = {
    "roe": "ROE",
    "roa": "ROA",
    "margen_neto": "Margen neto",
    "margen_bruto": "Margen bruto",
    "liquidez": "Liquidez corriente",
    "endeudamiento": "Deuda / Patrimonio",
    "margen_ebit": "Margen EBIT",
    "margen_ebitda": "Margen EBITDA",
    "test_acido": "Test Ácido",
    "cobertura_intereses": "Cobertura de Intereses",
    "capital_trabajo": "Capital de Trabajo",
}


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


# ----------------------------------------------------------
# EEFF de entidades con plan de cuentas FECU propio (seguros e intermediarios)
# ----------------------------------------------------------

# Estados de la FECU, en orden de presentación.
FECU_GROUPS = [
    {"code": "ESF", "label": "Estado de situación financiera"},
    {"code": "ERI", "label": "Estado del resultado integral"},
    {"code": "EFE", "label": "Estado de flujos de efectivo"},
]

_FECU_UNIT = "miles de CLP"


def _proy_period_label(period: int, monthly: bool) -> str:
    """Etiqueta de un período YYYYMM (int) para los ejes: mensual '2026-06', trimestral '2026 T2'.
    (`_period_label` no sirve aquí: espera str y siempre rotula trimestres.)"""
    p = int(period)
    y, m = p // 100, p % 100
    return f"{y}-{m:02d}" if monthly else f"{y} T{(m + 2) // 3}"


def _select_quarter_periods(allp: list, serie: str) -> list:
    """Períodos para las series trimestrales de las FECU (seguros / intermediarios)."""
    allp = sorted(int(p) for p in allp)
    if not allp:
        return []
    if serie.startswith("anual"):
        n = 10 if "10" in serie else 5
        return [p for p in allp if p % 100 == 12][-n:]
    if serie.startswith("mismotrim"):
        q = max(allp) % 100
        return [p for p in allp if p % 100 == q][-5:]
    if serie == "trim12":
        return allp[-12:]
    return allp[-8:]


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


# ----------------------------------------------------------
# Proyecciones macrofundadas (Fase 6 — SARIMAX + senda EEE)
# ----------------------------------------------------------

def _period_label(period: str) -> str:
    """'202606' → '2026 T2'."""
    return f"{period[:4]} T{int(period[4:]) // 3}"


# Hub de proyecciones: mismas clasificaciones que EEFF. Corporativos y AGF usan el modelo
# híbrido (macro + estructural + bandas empíricas calibradas); el resto usa SARIMAX
# univariante, porque ni la cadena estructural ni las bandas del backtest corporativo
# aplican a entidades financieras (ver models/simple_forecast.py).
PROY_CLASSES = [
    {"url": "/proyecciones/corporativos", "icon": "🏢", "title": "Corporativos",
     "desc": "Ingresos y resultado con modelo híbrido anclado en las encuestas del BCCh.",
     "meta": "Híbrido · bandas calibradas por backtest"},
    {"url": "/proyecciones/bancos", "icon": "🏦", "title": "Bancos",
     "desc": "Serie de EEFF total (consolidada, sin desglose por moneda), mensual.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/seguros", "icon": "🛡️", "title": "Compañías de seguros",
     "desc": "Total activo y resultado del período, vida y generales.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/intermediarios", "icon": "📈", "title": "Intermediarios de valores",
     "desc": "Total activos y utilidad del ejercicio de corredores y agentes.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/agf", "icon": "💼", "title": "AGF",
     "desc": "Administradoras generales de fondos (mismo formato IFRS que corporativos).",
     "meta": "Híbrido · bandas calibradas por backtest"},
]

# Config de las clasificaciones que van con SARIMAX univariante.
# `accumulated` marca los estados de resultado, que la CMF publica acumulados en el año.
_PROY_SIMPLE = {
    "bancos": {
        "title": "Proyecciones de bancos",
        "subtitle": "Serie de EEFF TOTAL (consolidada, sin desglose por moneda). Mensual, con "
                    "los dos planes de cuentas empalmados y homologados a pesos.",
        "monthly": True, "unit": "CLP", "cadence": "mensual",
        "kind_label": None, "kinds": None,
        "horizons": [{"value": 6, "label": "6 meses", "default": True},
                     {"value": 12, "label": "12 meses"}, {"value": 24, "label": "24 meses"}],
        "accounts": [
            {"label": "Total activos", "key": "TOTAL ACTIVOS", "report": "balance",
             "stock": True, "accumulated": False},
            {"label": "Resultado de los propietarios", "key": "RESULTADO DE LOS PROPIETARIOS",
             "report": "resultado", "stock": False, "accumulated": True},
        ],
    },
    "seguros": {
        "title": "Proyecciones de compañías de seguros",
        "subtitle": "FECU de seguros, trimestral. Total activo y resultado del período.",
        "monthly": False, "unit": "miles de CLP", "cadence": "trimestral",
        "kind_label": "Ramo",
        "kinds": [{"value": "vida", "label": "Vida"}, {"value": "generales", "label": "Generales"}],
        "horizons": [{"value": 4, "label": "4 trimestres (1 año)", "default": True},
                     {"value": 8, "label": "8 trimestres (2 años)"}],
        "accounts": [
            {"label": "Total activo", "key": "5.10.00.00", "stock": True, "accumulated": False},
            {"label": "Total resultado del período", "key": "5.31.00.00",
             "stock": False, "accumulated": True},
        ],
    },
    "intermediarios": {
        "title": "Proyecciones de intermediarios de valores",
        "subtitle": "FECU IFRS de corredores de bolsa y agentes, trimestral. "
                    "Total activos y utilidad del ejercicio.",
        "monthly": False, "unit": "miles de CLP", "cadence": "trimestral",
        "kind_label": "Tipo",
        "kinds": [{"value": "CORREDORES", "label": "Corredores de bolsa"},
                  {"value": "AGENTES", "label": "Agentes de valores"}],
        "horizons": [{"value": 4, "label": "4 trimestres (1 año)", "default": True},
                     {"value": 8, "label": "8 trimestres (2 años)"}],
        "accounts": [
            {"label": "Total activos", "key": "10.00.00", "stock": True, "accumulated": False},
            {"label": "Utilidad (pérdida) del ejercicio", "key": "30.00.00",
             "stock": False, "accumulated": True},
        ],
    },
}

# Config de las que reusan el modelo híbrido corporativo.
_PROY_HYBRID = {
    "corporativos": {"agf": False, "title": "Proyecciones corporativas",
                     "subtitle": "SARIMAX + modelo estructural anclado en la Encuesta de "
                                 "Expectativas Económicas (EEE, BCCh). Bandas empíricas "
                                 "calibradas con backtest rolling-origin."},
    "agf": {"agf": True, "title": "Proyecciones de AGF",
            "subtitle": "Administradoras generales de fondos. Mismo motor híbrido que "
                        "corporativos: comparten formato IFRS y plan de cuentas."},
}


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


# ----------------------------------------------------------
# Inversión institucional — cartera de compañías de seguros
# ----------------------------------------------------------

# Tarjetas del hub que reúne a los inversionistas institucionales (AFP + seguros).
INST_CLASSES = [
    {"url": "/seguros/cartera", "icon": "🛡️", "title": "Cartera de seguros",
     "desc": "Asset allocation de las compañías de seguros: clase de activo, exposición "
             "extranjera, valorización y deriva histórica.",
     "meta": "CMF · Circular 1.835 · mensual"},
    {"url": "/afp", "icon": "🏛️", "title": "AFP",
     "desc": "Rentabilidad por fondo, cartera del sistema y simulación neta de comisiones.",
     "meta": "Superintendencia de Pensiones · mensual"},
    {"url": "/fondos", "icon": "📊", "title": "Fondos de pensiones",
     "desc": "Retornos multi-horizonte y composición de cada fondo por AFP.",
     "meta": "Superintendencia de Pensiones · mensual"},
]

# Ramos con cartera publicada bajo la Circular 1.835.
_RAMOS = {"vida": "Seguros de vida", "generales": "Seguros generales"}


def _pct(part: float, whole: float) -> Optional[float]:
    """Porcentaje protegido: None cuando no hay base sobre la cual calcular."""
    return (part / whole * 100.0) if whole else None


def _build_allocation(df, top_codes: int = 14) -> dict:
    """
    Traduce el DataFrame de cartera (por código de inversión) a la estructura que consumen
    las plantillas: totales, apertura por clase de activo, por geografía, mix de valorización
    y el detalle por código.

    Los montos entran en miles de pesos (M$) y salen en MMM$ (miles de millones de CLP).
    """
    from api.insurer_glossary import clase_label, clase_order, describe

    def num(v) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if f != f else f  # descarta NaN

    total = sum(num(r.valor_final) for r in df.itertuples())
    por_clase: dict[str, float] = {}
    por_geo = {"nacional": 0.0, "extranjero": 0.0}
    codigos = []
    repr_rt = no_repr = cui = 0.0
    ca = vr = ee = otras = 0.0

    for r in df.itertuples():
        info = describe(r.investment_code)
        monto = num(r.valor_final)
        por_clase[info["clase"]] = por_clase.get(info["clase"], 0.0) + monto
        por_geo[info["geo"]] = por_geo.get(info["geo"], 0.0) + monto
        repr_rt += num(r.repr_rt_pr)
        no_repr += num(r.no_repr_rt_pr)
        cui += num(r.cui_apv)
        ca += num(r.costo_amortizado)
        vr += num(r.valor_razonable)
        ee += num(r.efectivo_equiv)
        otras += num(r.otras_clasif)
        codigos.append({
            "code": info["code"], "label": info["label"], "glosa": info["glosa"],
            "clase": clase_label(info["clase"]), "geo": info["geo"],
            "monto": monto / 1e6, "pct": _pct(monto, total),
        })

    codigos.sort(key=lambda c: c["monto"], reverse=True)
    clases = [{
        "clase": clase_label(k), "monto": por_clase[k] / 1e6, "pct": _pct(por_clase[k], total),
    } for k in clase_order() if por_clase.get(k)]
    clases.sort(key=lambda c: c["monto"], reverse=True)

    # El mix de valorización se mide sobre lo CLASIFICADO: créditos, siniestros por cobrar y
    # avances a tenedores no llevan método de valorización, así que no forman parte de la base.
    clasificado = ca + vr + ee + otras
    valorizacion = [
        {"label": "Costo amortizado", "monto": ca / 1e6, "pct": _pct(ca, clasificado)},
        {"label": "Valor razonable", "monto": vr / 1e6, "pct": _pct(vr, clasificado)},
        {"label": "Efectivo equivalente", "monto": ee / 1e6, "pct": _pct(ee, clasificado)},
        {"label": "Otra clasificación", "monto": otras / 1e6, "pct": _pct(otras, clasificado)},
    ]

    return {
        "total": total / 1e6,
        "clases": clases,
        "codigos": codigos[:top_codes],
        "codigos_todos": codigos,
        "geo": {
            "nacional": por_geo.get("nacional", 0.0) / 1e6,
            "extranjero": por_geo.get("extranjero", 0.0) / 1e6,
            "pct_extranjero": _pct(por_geo.get("extranjero", 0.0), total),
        },
        "reservas": {
            "repr": repr_rt / 1e6, "no_repr": no_repr / 1e6,
            "pct_repr": _pct(repr_rt, total),
        },
        "cui": {"monto": cui / 1e6, "pct": _pct(cui, total)},
        "valorizacion": valorizacion,
        "clasificado": clasificado / 1e6,
        "pct_clasificado": _pct(clasificado, total),
    }


@router.get("/inversion-institucional")
def inversion_institucional(request: Request):
    """Hub de inversionistas institucionales: AFP y compañías de seguros."""
    return templates.TemplateResponse(request, "institucional_hub.html", {
        "classes": INST_CLASSES,
    })


@router.get("/seguros/cartera")
def seguros_cartera(request: Request, ramo: str = Query("vida"),
                    db: Database = Depends(get_db)):
    """Vista de asset allocation de las compañías de seguros (Circular 1.835)."""
    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    companies = db.get_insurer_portfolio_companies(periods[0], ramo) if periods else None

    from api.insurer_glossary import clean_company_name
    company_options = []
    if companies is not None:
        company_options = [{"rut": str(r.rut),
                            "name": clean_company_name(r.company_name, str(r.rut))}
                           for r in companies.itertuples()]

    return templates.TemplateResponse(request, "seguros_cartera.html", {
        "ramo": ramo, "ramos": _RAMOS, "periods": periods,
        "companies": company_options,
    })


@router.get("/seguros/cartera/panel")
def seguros_cartera_panel(request: Request, ramo: str = Query("vida"),
                          period: Optional[int] = Query(None),
                          rut: str = Query(""), db: Database = Depends(get_db)):
    """Fragmento HTMX: asset allocation del período (mercado del ramo o una compañía)."""
    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    if not periods:
        return templates.TemplateResponse(request, "partials/seguros_cartera_panel.html",
                                          {"alloc": None, "meta": {}})
    period = period if period in periods else periods[0]
    rut = (rut or "").strip()

    df = db.get_insurer_allocation(period, ramo, rut or None)
    alloc = _build_allocation(df) if len(df) else None

    from api.insurer_glossary import clean_company_name
    companies = db.get_insurer_portfolio_companies(period, ramo)
    name = ""
    if rut:
        match = [r for r in companies.itertuples() if str(r.rut) == rut]
        name = clean_company_name(match[0].company_name, rut) if match else f"RUT {rut}"

    return templates.TemplateResponse(request, "partials/seguros_cartera_panel.html", {
        "alloc": alloc,
        "meta": {
            "period": period, "ramo": ramo, "ramo_label": _RAMOS[ramo],
            "scope": name or f"Mercado — {_RAMOS[ramo].lower()}",
            "n_companies": (0 if rut else int(companies.rut.nunique()) if len(companies) else 0),
            "is_market": not rut,
        },
    })


@router.get("/seguros/cartera/evolucion")
def seguros_cartera_evolucion(request: Request, ramo: str = Query("vida"),
                              rut: str = Query(""), since: int = Query(202001),
                              db: Database = Depends(get_db)):
    """
    Fragmento HTMX: deriva del asset allocation en el tiempo. Devuelve, por período, el peso
    (%) de cada clase de activo — que es como se lee un cambio de política de inversión.
    """
    from api.insurer_glossary import clase_label, clase_order, describe

    ramo = ramo if ramo in _RAMOS else "vida"
    rut = (rut or "").strip()
    df = db.get_insurer_allocation_series(ramo, rut or None, since=since)
    if not len(df):
        return templates.TemplateResponse(request, "partials/seguros_cartera_evolucion.html",
                                          {"periods": [], "series": []})

    # period → clase → monto
    acc: dict[int, dict[str, float]] = {}
    for r in df.itertuples():
        try:
            monto = float(r.valor_final)
        except (TypeError, ValueError):
            continue
        if monto != monto:  # NaN
            continue
        clase = describe(r.investment_code)["clase"]
        acc.setdefault(int(r.period), {}).setdefault(clase, 0.0)
        acc[int(r.period)][clase] += monto

    periods = sorted(acc)
    orden = [k for k in clase_order() if any(acc[p].get(k) for p in periods)]
    series = []
    for k in orden:
        pts = []
        for p in periods:
            total = sum(acc[p].values())
            pts.append(_pct(acc[p].get(k, 0.0), total))
        series.append({"clase": clase_label(k), "puntos": pts})

    # Deriva: cuánto se movió cada clase entre el primer y el último período cargado.
    deriva = []
    if len(periods) >= 2:
        for s in series:
            ini, fin = s["puntos"][0], s["puntos"][-1]
            if ini is not None and fin is not None:
                deriva.append({"clase": s["clase"], "inicio": ini, "fin": fin,
                               "delta": fin - ini})
        deriva.sort(key=lambda d: abs(d["delta"]), reverse=True)

    return templates.TemplateResponse(request, "partials/seguros_cartera_evolucion.html", {
        "periods": [str(p) for p in periods], "series": series,
        "deriva": deriva[:8],
        "desde": periods[0] if periods else None, "hasta": periods[-1] if periods else None,
    })


@router.get("/seguros/cartera/companias")
def seguros_cartera_companias(request: Request, ramo: str = Query("vida"),
                              period: Optional[int] = Query(None),
                              db: Database = Depends(get_db)):
    """
    Fragmento HTMX: comparación entre compañías del ramo — tamaño de cartera, peso relativo
    y perfil de inversión (extranjero, renta fija, CUI/APV) para leer quién invierte distinto.
    """
    from api.insurer_glossary import clean_company_name, describe

    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    if not periods:
        return templates.TemplateResponse(request, "partials/seguros_cartera_companias.html",
                                          {"rows": [], "period": None})
    period = period if period in periods else periods[0]

    companies = db.get_insurer_portfolio_companies(period, ramo)
    mercado = sum(float(r.total) for r in companies.itertuples()
                  if r.total == r.total and r.total is not None)

    _RF = {"rf_estatal", "rf_bancaria", "rf_corporativa", "rf_hipotecaria"}
    rows = []
    for c in companies.itertuples():
        df = db.get_insurer_allocation(period, ramo, str(c.rut))
        if not len(df):
            continue
        a = _build_allocation(df)
        rf = sum(x["monto"] for x in a["codigos_todos"]
                 if describe(x["code"])["clase"] in _RF)
        rows.append({
            "rut": str(c.rut),
            "name": clean_company_name(c.company_name, str(c.rut)),
            "total": a["total"],
            "share": _pct(a["total"] * 1e6, mercado),
            "pct_extranjero": a["geo"]["pct_extranjero"],
            "pct_rf": _pct(rf, a["total"]),
            "pct_cui": a["cui"]["pct"],
            "pct_repr": a["reservas"]["pct_repr"],
        })
    rows.sort(key=lambda r: r["total"], reverse=True)

    # HHI sobre las participaciones de mercado (0-10.000): concentración de la industria.
    hhi = sum((r["share"] or 0.0) ** 2 for r in rows)

    return templates.TemplateResponse(request, "partials/seguros_cartera_companias.html", {
        "rows": rows, "period": period, "ramo_label": _RAMOS[ramo],
        "total_mercado": mercado / 1e6, "hhi": hhi,
    })


@router.get("/seguros/cartera/emisores")
def seguros_cartera_emisores(request: Request, ramo: str = Query("vida"),
                             period: Optional[int] = Query(None),
                             rut: str = Query(""), db: Database = Depends(get_db)):
    """
    Fragmento HTMX: concentración por emisor de la renta fija (archivo B.1).

    Se muestra el NÚMERO de instrumentos y el nominal ABIERTO POR MONEDA: el valor nominal
    viene en la unidad de cada instrumento (UF, $, USD…) y sumarlo entre monedas no
    significaría nada. Tampoco es valor de mercado — sirve para ver concentración de emisores,
    no para ponderar la cartera.
    """
    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    if not periods:
        return templates.TemplateResponse(request, "partials/seguros_cartera_emisores.html",
                                          {"rows": [], "period": None})
    period = period if period in periods else periods[0]
    rut = (rut or "").strip()

    df = db.get_insurer_issuer_exposure(period, ramo, rut or None, limit=15)
    agrupado: dict[str, dict] = {}
    for r in df.itertuples():
        key = str(r.rut_emisor)
        e = agrupado.setdefault(key, {
            "rut": key,
            "name": (r.nombre if isinstance(r.nombre, str) and r.nombre.strip() else ""),
            "total": int(r.total_instr or 0), "monedas": [],
        })
        try:
            nominal = float(r.nominal)
        except (TypeError, ValueError):
            nominal = 0.0
        e["monedas"].append({"unidad": str(r.unidad_monetaria or "—"),
                             "n": int(r.n_instrumentos or 0),
                             "nominal": 0.0 if nominal != nominal else nominal})
    rows = sorted(agrupado.values(), key=lambda x: x["total"], reverse=True)
    for e in rows:
        e["monedas"].sort(key=lambda m: m["n"], reverse=True)

    return templates.TemplateResponse(request, "partials/seguros_cartera_emisores.html", {
        "rows": rows, "period": period, "ramo_label": _RAMOS[ramo],
    })
