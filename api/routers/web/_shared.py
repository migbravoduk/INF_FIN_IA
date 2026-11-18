"""
api/routers/web/_shared.py — Constantes y helpers compartidos por las vistas HTML.
"""

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_db, records, templates
from api.eeff_format import (build_statement_groups, GROUP_LABELS, GROUP_ORDER,
                             GROUP_SLOTS, _assign_balance_sections)
from api.company_profiles import get_profile
from db.database import Database

# ----------------------------------------------------------
# Panel multi-fuente
# ----------------------------------------------------------

def build_overview_context(db: Database) -> dict:
    """Contexto del panel: KPIs + series UF y TPM (12 meses). Reusable por el export estático."""
    kpi = db.get_overview_kpis()
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    uf_series = records(db.get_series(db.KPI_SERIES["uf"], from_date=since))
    tpm_series = records(db.get_series(db.KPI_SERIES["tpm"], from_date=since))
    try:
        from db.quality import last_summary
        dq = last_summary(db)
    except Exception:
        dq = None
    return {"kpi": kpi, "uf_series": uf_series, "tpm_series": tpm_series, "dq": dq}


# ----------------------------------------------------------
# Datos Banco Central (macro)
# ----------------------------------------------------------

# Etiquetas legibles de las categorías del catálogo (config/series_catalog.yaml).
BC_CATEGORIAS = {
    "actividad": "Actividad",
    "actividad_mensual": "Actividad mensual",
    "precios": "Precios",
    "precios_uf": "UF",
    "precios_ivp": "IVP",
    "precios_utm": "UTM",
    "politica_monetaria": "Política monetaria",
    "curva_soberana": "Curva soberana",
    "colocaciones": "Tasas de colocación",
    "tipo_de_cambio": "Tipo de cambio",
    "laboral": "Laboral",
    "sector_externo": "Sector externo",
    "fiscal": "Fiscal",
    "materias_primas": "Materias primas",
    "expectativas": "Expectativas (EEE/EOF)",
}

# Unidades del catálogo → símbolo corto para ejes y tarjetas.
BC_UNIDADES = {
    "porcentaje": "%",
    "porcentaje_anual": "% anual",
    "indice": "índice",
    "CLP": "$",
    "CLP_por_USD": "$/US$",
    "USD_por_libra": "US$/lb",
    "millones_USD": "MM US$",
    "millones_CLP_2018": "MM$ de 2018",
}

# Unidades que YA son una tasa: su variación se lee en puntos porcentuales, no en %.
BC_UNIDADES_TASA = {"porcentaje", "porcentaje_anual"}


def _bc_dimension(unit: Optional[str]) -> str:
    """
    Dimensión comparable de una unidad, para decidir si dos series comparten eje.

    No basta con igualar el string: `porcentaje` y `porcentaje_anual` son etiquetas
    distintas de la MISMA magnitud, y separarlas en dos ejes arruina justo la
    comparación más útil del macro chileno —TPM contra inflación—, donde la distancia
    vertical entre ambas es la tasa real y por tanto sí tiene significado.
    """
    if unit in BC_UNIDADES_TASA:
        return "pct"
    return unit or "?"

BC_RANGOS = {"1a": 1, "3a": 3, "5a": 5, "10a": 10, "todo": None}


def _bc_desde(rango: str) -> Optional[str]:
    """Fecha de corte del rango pedido; None = toda la historia."""
    anos = BC_RANGOS.get(rango, 5)
    if anos is None:
        return None
    hoy = dt.date.today()
    try:
        return hoy.replace(year=hoy.year - anos).isoformat()
    except ValueError:      # 29 de febrero
        return (hoy - dt.timedelta(days=365 * anos)).isoformat()


def _bc_decorar(fila: dict) -> dict:
    """Agrega a una fila del catálogo sus etiquetas legibles."""
    return {**fila,
            "cat_label": BC_CATEGORIAS.get(fila["category"], fila["category"] or "—"),
            "unit_label": BC_UNIDADES.get(fila["unit"], fila["unit"] or ""),
            "es_tasa": fila["unit"] in BC_UNIDADES_TASA}


# ----------------------------------------------------------
# Estados financieros corporativos (CMF)
# ----------------------------------------------------------

# Clasificaciones de entidad del hub de EEFF. Cada una tiene su plan de cuentas y fuente;
# bancos conserva su propia vista/lógica (desglose por moneda), el resto comparte el patrón.
EEFF_CLASSES = [
    {"key": "corporativos", "url": "/eeff/corporativos", "icon": "building-2", "title": "Corporativos",
     "desc": "Sociedades anónimas y demás emisores que no caen en otra clasificación.",
     "meta": "CMF · archivo plano IFRS · trimestral"},
    {"key": "bancos", "url": "/banca", "icon": "landmark", "title": "Bancos",
     "desc": "Balances y resultados con desglose por tipo de moneda (lógica propia).",
     "meta": "CMF/SBIF · mensual"},
    {"key": "seguros", "url": "/eeff/seguros", "icon": "shield", "title": "Compañías de seguros",
     "desc": "Aseguradoras de vida y generales, con su plan de cuentas FECU.",
     "meta": "CMF · FECU seguros · trimestral"},
    {"key": "intermediarios", "url": "/eeff/intermediarios", "icon": "trending-up",
     "title": "Intermediarios de valores",
     "desc": "Corredores de bolsa y agentes de valores.",
     "meta": "CMF · FECU IFRS · trimestral"},
    {"key": "agf", "url": "/eeff/agf", "icon": "briefcase", "title": "AGF",
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


# ----------------------------------------------------------
# Fondos mutuos (CMF)
# ----------------------------------------------------------
#
# Dos fuentes distintas y complementarias:
#   · estadística DIARIA (`cmf_mutual_fund_quota_values`): valor cuota, patrimonio,
#     partícipes, y el enlace del fondo con su ADMINISTRADORA y su TIPO.
#   · cartera MENSUAL (`cmf_mutual_fund_portfolio`): en qué está invertido cada fondo.
# El RUN es la llave entre ambas, siempre SIN dígito verificador (ver CONTEXTO.md).

_FM_CARTERAS = {
    "NACI": "Inversión nacional",
    "EXTR": "Inversión extranjera",
    "OPCI": "Opciones",
    "FUTU": "Futuros",
    "OPLA": "Opciones lanzador",
}


def _fm_periodo_label(period) -> str:
    return f"{period[0]}-{period[1]:02d}" if period else "—"


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
    {"url": "/proyecciones/corporativos", "icon": "building-2", "title": "Corporativos",
     "desc": "Ingresos y resultado con modelo híbrido anclado en las encuestas del BCCh.",
     "meta": "Híbrido · bandas calibradas por backtest"},
    {"url": "/proyecciones/bancos", "icon": "landmark", "title": "Bancos",
     "desc": "Serie de EEFF total (consolidada, sin desglose por moneda), mensual.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/seguros", "icon": "shield", "title": "Compañías de seguros",
     "desc": "Total activo y resultado del período, vida y generales.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/intermediarios", "icon": "trending-up", "title": "Intermediarios de valores",
     "desc": "Total activos y utilidad del ejercicio de corredores y agentes.",
     "meta": "SARIMAX · bandas no calibradas"},
    {"url": "/proyecciones/agf", "icon": "briefcase", "title": "AGF",
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
             "report": "resultado", "stock": False, "accumulated": True, "uf": True},
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


# ----------------------------------------------------------
# Inversión institucional — cartera de compañías de seguros
# ----------------------------------------------------------

# Tarjetas del hub que reúne a los inversionistas institucionales (AFP + seguros).
INST_CLASSES = [
    {"url": "/seguros/cartera", "icon": "chart-pie", "title": "Cartera de seguros",
     "desc": "Asset allocation de las compañías de seguros: clase de activo, exposición "
             "extranjera, valorización y deriva histórica.",
     "meta": "CMF · Circular 1.835 · mensual"},
    {"url": "/afp", "icon": "piggy-bank", "title": "AFP",
     "desc": "Rentabilidad por fondo, cartera del sistema y simulación neta de comisiones.",
     "meta": "Superintendencia de Pensiones · mensual"},
    {"url": "/fondos", "icon": "chart-column", "title": "Fondos de pensiones",
     "desc": "Retornos multi-horizonte y composición de cada fondo por AFP.",
     "meta": "Superintendencia de Pensiones · mensual"},
    {"url": "/inversion-institucional/derivados", "icon": "scale", "title": "Derivados",
     "desc": "Cómo están posicionados en derivados las AFP, las aseguradoras, los fondos y "
             "otros sectores: dirección, instrumento y tamaño. Incluye el monitor de los "
             "swaps de tasa de las AFP.",
     "meta": "Banco Central (F099.DER) + CMF · mensual"},
    {"url": "/inversion-institucional/emisor", "icon": "search", "title": "Inversión por emisor",
     "desc": "Elige un emisor (empresa, banco, Fisco…) y ve cuánto tienen invertido en él las "
             "compañías de seguros y los fondos mutuos, y cómo cambió mes a mes.",
     "meta": "CMF · Circular 1.835 + cartera FFMM · mensual"},
    {"url": "/inversion-institucional/emisores", "icon": "link-2", "title": "Emisores en común",
     "desc": "Emisores que están a la vez en la cartera de las aseguradoras y de los fondos "
             "mutuos, cruzando instrumento por instrumento.",
     "meta": "CMF · Circular 1.835 + cartera FFMM · mensual"},
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
