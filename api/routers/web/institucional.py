"""
api/routers/web/institucional.py — Inversión institucional: hub y cartera de compañías de seguros.
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

@router.get("/inversion-institucional/emisores")
def inversion_institucional_emisores(request: Request, db: Database = Depends(get_db)):
    """Emisores en común entre la cartera de seguros (B.1) y la de fondos mutuos."""
    from db.cross import common_issuers
    return templates.TemplateResponse(request, "institucional_emisores.html", common_issuers(db))


_OPCIONES_EMISOR: dict = {}   # cache por proceso: el selector cuesta ~1,5 s y cambia al cargar datos


@router.get("/inversion-institucional/emisor")
def inversion_institucional_emisor(request: Request, rut: str = Query(""),
                                   db: Database = Depends(get_db)):
    """Inversión de los institucionales en un emisor: nivel y variación mes a mes."""
    from db.cross import issuer_options
    clave = db.conn.execute("SELECT MAX(period) FROM cmf_insurer_portfolio_fixed_income").fetchone()[0]
    if _OPCIONES_EMISOR.get("clave") != clave:
        _OPCIONES_EMISOR.update(clave=clave, opciones=issuer_options(db))
    return templates.TemplateResponse(request, "institucional_emisor.html", {
        "opciones": _OPCIONES_EMISOR["opciones"], "rut": rut.strip(),
    })


@router.get("/inversion-institucional/emisor/panel")
def inversion_institucional_emisor_panel(request: Request, rut: str = Query(""),
                                         db: Database = Depends(get_db)):
    """Fragmento HTMX: tenencias de seguros y fondos mutuos en el emisor elegido."""
    from db.cross import issuer_holdings
    # El selector manda "Nombre · RUT 12345678"; también se acepta el RUT solo, con o sin DV.
    import re
    m = re.findall(r"\d[\d.]*(?:-[\dkK])?", rut or "")
    if not m and (rut or "").strip():
        texto = rut.strip().lower()
        hit = next((o for o in _OPCIONES_EMISOR.get("opciones") or []
                    if o["nombre"] and texto in o["nombre"].lower()), None)
        m = [hit["rut"]] if hit else []
    if not m:
        return templates.TemplateResponse(request, "partials/institucional_emisor_panel.html",
                                          {"h": None, "q": rut})
    h = issuer_holdings(db, m[-1])
    return templates.TemplateResponse(request, "partials/institucional_emisor_panel.html",
                                      {"h": h, "q": rut})


@router.get("/inversion-institucional/derivados")
def inversion_institucional_derivados(request: Request, db: Database = Depends(get_db)):
    """Monitor de posiciones institucionales en derivados (docs/PLAN_DERIVADOS.md)."""
    import db.derivados as D
    from db.derivados_ffmm import resumen_ffmm
    from db.derivados_seguros import resumen_seguros
    from db.derivados_afp import forwards_usd_afp, swaps_afp
    return templates.TemplateResponse(request, "institucional_derivados.html", {
        "sw": swaps_afp(db), "fw": forwards_usd_afp(db),
        "mon": D.monitor_afp(db), "ident": D.identidades(db),
        "ret": D.retornos_afp(db, desde="2025-01-01"),
        "sec": D.sectores_usd(db), "det": D.detalle_afp(db),
        "ffmm": resumen_ffmm(db), "seg": resumen_seguros(db),
        "umbral": {"k": D.ALERTA_K, "hist": D.ALERTA_MIN_HIST, "signo": D.ALERTA_SIGNO_MIN},
    })


@router.get("/derivados")
def derivados_global(request: Request, db: Database = Depends(get_db)):
    """Derivados de toda la economía: mercado, industrias, riesgos y coberturas por entidad
    (db/derivados_global.py)."""
    from db.derivados_global import resumen
    return templates.TemplateResponse(request, "derivados.html", {"d": resumen(db)})


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


def _fnum(v):
    """float o None (el DataFrame trae NaN donde no hay dato)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


@router.get("/seguros/cartera/emisores")
def seguros_cartera_emisores(request: Request, ramo: str = Query("vida"),
                             period: Optional[int] = Query(None),
                             rut: str = Query(""), limit: int = Query(20, ge=1, le=500),
                             db: Database = Depends(get_db)):
    """
    Fragmento HTMX: concentración por emisor de la renta fija (archivo B.1), por MONTO expuesto.

    El monto es valor PAR en pesos (db/cross.monto_par_sql): saldo vigente cuando la compañía lo
    informa; si no, monto de origen, que sobrestima lo expuesto en leasing y letras hipotecarias.
    No es valor de mercado. Cada emisor se abre (`/seguros/cartera/emisores/detalle`) para ver
    quién lo tiene y por cuánto.
    """
    from api.insurer_glossary import (clean_company_name, tipo_instrumento_label,
                                      TIPOS_SIN_EMISOR_INSCRITO)

    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    if not periods:
        return templates.TemplateResponse(request, "partials/seguros_cartera_emisores.html",
                                          {"rows": [], "period": None})
    period = period if period in periods else periods[0]
    rut = (rut or "").strip()

    # A quién corresponde la cartera que se está mirando. Sin esto la tabla es ambigua:
    # los mismos emisores salen tanto para el agregado del mercado como para una compañía.
    companias = db.get_insurer_portfolio_companies(period, ramo)
    opciones = [{"rut": str(c.rut), "name": clean_company_name(c.company_name, str(c.rut))}
                for c in companias.itertuples()]
    opciones.sort(key=lambda o: o["name"])
    sel = next((o for o in opciones if o["rut"] == rut), None) if rut else None
    if rut and sel is None:
        rut = ""   # RUT que no existe en el período: se cae al mercado en vez de mostrar vacío

    df = db.get_insurer_issuer_exposure(period, ramo, rut or None, limit=limit)
    rows = []
    for r in df.itertuples():
        tipo_code = (r.tipo_principal or "")
        total = _fnum(r.total_mm)
        mm = _fnum(r.mm)
        rows.append({
            "rut": str(r.rut_emisor),
            "name": (r.nombre if isinstance(r.nombre, str) and r.nombre.strip() else ""),
            "mm": mm, "pct": (mm / total * 100) if (mm is not None and total) else None,
            "posiciones": int(r.posiciones or 0), "companias": int(r.companias or 0),
            "tipo": tipo_instrumento_label(tipo_code), "tipo_code": tipo_code,
            "solo_un_tipo": int(r.n_tipos or 0) <= 1,
            "sin_emisor_inscrito": tipo_code in TIPOS_SIN_EMISOR_INSCRITO,
            "pct_origen": _fnum(r.pct_origen), "sin_fx": int(r.sin_fx or 0),
            "leasing_propio": tipo_code == "CLEAS" and int(r.n_propio or 0) > 0,
        })
    total_emisores = db.count_insurer_issuers(period, ramo, rut or None)
    return templates.TemplateResponse(request, "partials/seguros_cartera_emisores.html", {
        "rows": rows, "period": period, "ramo_label": _RAMOS[ramo], "ramo": ramo,
        "opciones": opciones, "sel_rut": rut,
        "sel_nombre": sel["name"] if sel else None,
        "total_emisores": total_emisores, "limit": limit,
        "total_mm": _fnum(df.total_mm.iloc[0]) if len(df) else None,
        "n_companias": len(opciones),
    })


@router.get("/seguros/cartera/emisores/detalle")
def seguros_cartera_emisor_detalle(request: Request, ramo: str = Query("vida"),
                                   period: Optional[int] = Query(None),
                                   rut: str = Query(""), emisor: str = Query(...),
                                   db: Database = Depends(get_db)):
    """
    Fragmento HTMX: quién tiene la renta fija de un emisor y por cuánto.
    Sin `rut` (mercado): las compañías tenedoras. Con `rut`: sus instrumentos de ese emisor.
    """
    from api.insurer_glossary import clean_company_name, tipo_instrumento_label

    ramo = ramo if ramo in _RAMOS else "vida"
    periods = db.get_insurer_portfolio_periods(insurance_type=ramo)
    period = period if period in periods else (periods[0] if periods else None)
    rut = (rut or "").strip()
    emisor = emisor.strip().lstrip("0")
    if period is None or not emisor.isdigit():
        return templates.TemplateResponse(request, "partials/seguros_cartera_emisor_detalle.html",
                                          {"filas": [], "por_compania": not rut})
    df = db.get_insurer_issuer_detail(period, ramo, emisor, rut or None)
    # Nombre de la compañía: el del control del período; si no figura (p. ej. una compañía que dejó
    # de informar el control), el de la maestra de entidades.
    nombres = dict(db.conn.execute(
        "SELECT rut, coalesce(nombre_corto, nombre_canonico) FROM dim_entidad").fetchall())
    for c in db.get_insurer_portfolio_companies(period, ramo).itertuples():
        if (c.company_name or "").strip():       # con el nombre en blanco manda la maestra
            nombres[str(c.rut)] = clean_company_name(c.company_name, str(c.rut))
    total = float(df.mm.sum()) if len(df) and df.mm.notna().any() else 0.0
    filas = []
    for r in df.itertuples():
        mm = _fnum(r.mm)
        f = {"mm": mm, "posiciones": int(r.posiciones or 0), "pct_origen": _fnum(r.pct_origen),
             "pct_emisor": (mm / total * 100) if (mm is not None and total) else None}
        if rut:
            f.update(nemotecnico=r.nemotecnico, tipo=tipo_instrumento_label(r.tipo_instrumento),
                     tipo_code=r.tipo_instrumento, unidad=r.unidad_monetaria or "—",
                     nominal=_fnum(r.nominal))
        else:
            f.update(nombre=nombres.get(str(r.tenedor)) or f"RUT {r.tenedor}", tenedor=str(r.tenedor),
                     tipo=tipo_instrumento_label(r.tipo_principal),
                     propio=int(r.n_propio or 0) > 0, sin_fx=int(r.sin_fx or 0))
        filas.append(f)
    return templates.TemplateResponse(request, "partials/seguros_cartera_emisor_detalle.html", {
        "filas": filas, "por_compania": not rut, "total": total, "period": period,
        "emisor": emisor, "ramo": ramo,
    })
