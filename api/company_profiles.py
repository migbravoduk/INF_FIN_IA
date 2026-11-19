"""
api/company_profiles.py — Reseña breve del negocio de cada empresa.

Estrategia:
  1. Reseña curada (precisa) desde config/company_profiles.yaml, por RUT.
  2. Si no hay, se infiere una reseña por TIPO a partir del nombre (AGF, sanitaria,
     concesionaria, viña, transmisora, etc.) — cubre el grueso de la cola larga.
  3. Fallback genérico.
"""

import re
from pathlib import Path

import yaml

from config.settings import config_path

_PROFILES_PATH = config_path("company_profiles.yaml")
_SECTORS_PATH = config_path("company_sectors.yaml")
_curated = None  # cache
_sectors = None  # cache rut → macro-sector curado

# Patrones nombre → reseña por tipo (orden importa: lo más específico primero).
_INFER = [
    (r"ADMINISTRADORA GENERAL DE FONDOS|\bAGF\b", "Administradora general de fondos (gestión de fondos mutuos y de inversión).", "Servicios Financieros"),
    (r"SECURITIZADORA", "Securitizadora: emisión de bonos respaldados por activos.", "Servicios Financieros"),
    (r"SERVICIOS FINANCIEROS|\bFINANCIER[OA]\b", "Servicios financieros.", "Servicios Financieros"),
    (r"\bAGUAS\b|SANITARI|ESVAL|ESSBIO|NUEVOSUR|AGUAS NUEVAS", "Empresa sanitaria: agua potable y tratamiento de aguas servidas.", "Energía y Servicios Básicos"),
    (r"CONCESIONARIA|AUTOPISTA|\bRUTA\b|VESPUCIO|COSTANERA|TUNEL", "Sociedad concesionaria de infraestructura pública (autopistas, hospitales, etc.).", "Infraestructura y Construcción"),
    (r"AEROPUERTO|TERMINAL INTERNACIONAL", "Concesionaria/operadora de infraestructura aeroportuaria.", "Infraestructura y Construcción"),
    (r"EMPRESA PORTUARIA|PORTUARIA|\bPUERTO\b", "Empresa portuaria: operación de terminales y servicios marítimos.", "Transporte y Logística"),
    (r"CAJA DE COMPENSACION", "Caja de compensación de asignación familiar: prestaciones sociales y crédito social.", "Servicios Financieros"),
    (r"CASINO", "Operador de casino de juegos.", "Entretenimiento y Turismo"),
    (r"HIPODROMO|\bHIPICO\b|HIPICA", "Club hípico: organización de carreras.", "Entretenimiento y Turismo"),
    (r"DEPORTIV|\bFUTBOL\b|S\.?A\.?D\.?P|AZUL AZUL|BLANCO Y NEGRO|CRUZADOS|COLO COLO", "Club/sociedad deportiva profesional.", "Entretenimiento y Turismo"),
    (r"TRANSMISORA|TRANSMISI[ÓO]N", "Transmisión de energía eléctrica.", "Energía y Servicios Básicos"),
    (r"GENERACI[ÓO]N|ELECTRICA|ENERGIA|ENERGÍA|EOLIC|SOLAR", "Generación y/o suministro de energía eléctrica.", "Energía y Servicios Básicos"),
    (r"\bGAS\b", "Distribución/comercialización de gas.", "Energía y Servicios Básicos"),
    (r"VI[ÑN]A|VI[ÑN]EDOS|\bVINOS\b", "Viña: producción y comercialización de vinos.", "Alimentos y Agroindustria"),
    (r"INMOBILIARIA|RENTAS|BODENOR|REDMEGACENTRO", "Inmobiliaria: desarrollo y/o renta de proyectos inmobiliarios.", "Inmobiliaria y Construcción"),
    (r"\bLEASING\b", "Arrendamiento financiero (leasing).", "Servicios Financieros"),
    (r"FACTORING|FACTOTAL|FACTOR", "Factoring y financiamiento de cuentas por cobrar.", "Servicios Financieros"),
    (r"BOLSA DE|CONTRAPARTE CENTRAL|DEPOSITO CENTRAL|DCV", "Infraestructura de mercado de valores.", "Servicios Financieros"),
    (r"CLASIFICADORA DE RIESGO|FELLER|HUMPHREYS|FITCH|ICR", "Clasificadora de riesgo.", "Servicios Financieros"),
    (r"\bBUSES\b|TRANSPORTE DE PASAJEROS|METRO", "Operador de transporte de pasajeros.", "Transporte y Logística"),
    (r"HOTEL", "Operación hotelera.", "Entretenimiento y Turismo"),
    (r"CLINICA|M[ÉE]DIC|\bSALUD\b|HOSPITAL|DIAGNOSTICO", "Prestador de servicios de salud.", "Salud y Educación"),
    (r"ISAPRE|SEGUROS|\bVIDA\b", "Seguros / salud previsional.", "Servicios Financieros"),
    (r"SALMONES|SEAFOODS|PESQUER|ACUICOLA|CULTIVOS MARINOS", "Acuicultura y/o pesca.", "Alimentos y Agroindustria"),
    (r"FORESTAL|CELULOSA|ASERRADERO", "Actividad forestal / celulosa.", "Industria y Recursos Naturales"),
    (r"AGRICOLA|AGROPECUARIA|AGROINDUSTRIAL|FRUTICOLA|HORTI", "Agroindustria / actividad agropecuaria.", "Alimentos y Agroindustria"),
    (r"BANCO", "Entidad bancaria.", "Servicios Financieros"),
    (r"TELEFON|TELECOMUNICACION|\bTV\b|TELEVISION|CABLE", "Telecomunicaciones / medios.", "Tecnología y Telecomunicaciones"),
    (r"CONSTRUCTORA|CONSTRUCCION|INGENIERIA|MONTAJES", "Construcción e ingeniería.", "Infraestructura y Construcción"),
    (r"INVERSIONES|\bHOLDING\b|MATRIZ|RENTAS Y", "Sociedad de inversiones / holding.", "Holdings e Inversiones"),
    (r"COMERCIAL|RETAIL|TIENDAS|SUPERMERCADO", "Comercio minorista (retail).", "Comercio y Retail"),
    (r"NAVIER|MARITIM|MAR[ÍI]TIM", "Transporte marítimo / negocio naviero.", "Transporte y Logística"),
    (r"CEMENTO|HORMIG[ÓO]N", "Producción de cemento y materiales de construcción.", "Infraestructura y Construcción"),
    (r"MINER|\bORO\b|\bCOBRE\b|LITIO", "Minería / recursos naturales.", "Industria y Recursos Naturales"),
    (r"UNIVERSIDAD|EDUCACION|EDUCACIONAL", "Institución de educación.", "Salud y Educación"),
    (r"QU[ÍI]MIC|OX[ÍI]GENO|GASES", "Química industrial.", "Industria y Recursos Naturales"),
    (r"ENJOY|ENTRETENCION|RESORT", "Entretenimiento, casinos y resorts.", "Entretenimiento y Turismo"),
    (r"ALIMENTOS|LACTEO|CONSERVA", "Industria de alimentos.", "Alimentos y Agroindustria"),
    (r"ZONA FRANCA", "Operación de zona franca.", "Comercio y Retail"),
]

_GENERIC = "Empresa emisora registrada en la CMF; reseña no disponible (inferida por tipo no aplicable)."


def _load() -> dict:
    global _curated
    if _curated is None:
        try:
            _curated = yaml.safe_load(_PROFILES_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            _curated = {}
    return _curated


def _load_sectors() -> dict:
    """rut → macro-sector desde config/company_sectors.yaml ({sector: [rut, ...]})."""
    global _sectors
    if _sectors is None:
        try:
            data = yaml.safe_load(_SECTORS_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        _sectors = {str(r).lstrip("0"): sec for sec, ruts in data.items() for r in (ruts or [])}
    return _sectors


# Patrones agregados en nov-2026; la clasificación "nombre" (anterior) no los usa.
_INFER_NUEVOS = {r"SERVICIOS FINANCIEROS|\bFINANCIER[OA]\b"}


def infer_profile(name: str, anterior: bool = False) -> tuple[str, str]:
    """Reseña por tipo, inferida del nombre. Retorna (descripción, macro_sector)."""
    n = (name or "").upper()
    if not anterior:
        n = " ".join(n.split())   # "ADMINISTRADORA  GENERAL" (doble espacio) no calzaba
    for pat, desc, macro in _INFER:
        if anterior and pat in _INFER_NUEVOS:
            continue
        if re.search(pat, n):
            return desc, macro
    return _GENERIC, "Otros / No Clasificado"


def sector_of(rut: str, name: str, fuente: str = "curada") -> str:
    """
    Macro-sector de una empresa. fuente='curada' (default): la tabla por RUT manda y la
    inferencia por nombre cubre el resto. fuente='nombre': solo la inferencia por nombre
    (la clasificación anterior a nov-2026; queda para el A/B del modelo estructural).
    """
    if fuente == "curada":
        sec = _load_sectors().get(str(rut).strip().replace(".", "").replace("-", "").lstrip("0"))
        if sec:
            return sec
        return infer_profile(name)[1]
    return infer_profile(name, anterior=True)[1]


def get_profile(rut: str, name: str) -> dict:
    """Reseña de una empresa: {text, source, macro_sector}. source = 'curada' | 'inferida' | 'generica'."""
    rut = str(rut).strip().replace(".", "").replace("-", "")
    curated = _load().get(rut)
    inferred_desc, _ = infer_profile(name)
    macro = sector_of(rut, name)

    if curated:
        return {"text": curated, "source": "curada", "macro_sector": macro}

    if inferred_desc == _GENERIC:
        # Sin reseña: se deja el nombre de la empresa como marca/placeholder.
        return {"text": name, "source": "generica", "macro_sector": macro}

    return {"text": inferred_desc, "source": "inferida", "macro_sector": macro}

def get_all_sectors() -> list[str]:
    """Devuelve la lista de todos los macro-sectores únicos."""
    macros = set(macro for _, _, macro in _INFER)
    macros.add("Otros / No Clasificado")
    return sorted(list(macros))

