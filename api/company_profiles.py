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

_PROFILES_PATH = Path(__file__).resolve().parent.parent / "config" / "company_profiles.yaml"
_curated = None  # cache

# Patrones nombre → reseña por tipo (orden importa: lo más específico primero).
_INFER = [
    (r"ADMINISTRADORA GENERAL DE FONDOS|\bAGF\b", "Administradora general de fondos (gestión de fondos mutuos y de inversión)."),
    (r"SECURITIZADORA", "Securitizadora: emisión de bonos respaldados por activos."),
    (r"\bAGUAS\b|SANITARI|ESVAL|ESSBIO|NUEVOSUR|AGUAS NUEVAS", "Empresa sanitaria: agua potable y tratamiento de aguas servidas."),
    (r"CONCESIONARIA|AUTOPISTA|\bRUTA\b|VESPUCIO|COSTANERA|TUNEL", "Sociedad concesionaria de infraestructura pública (autopistas, hospitales, etc.)."),
    (r"AEROPUERTO|TERMINAL INTERNACIONAL", "Concesionaria/operadora de infraestructura aeroportuaria."),
    (r"EMPRESA PORTUARIA|PORTUARIA|\bPUERTO\b", "Empresa portuaria: operación de terminales y servicios marítimos."),
    (r"CAJA DE COMPENSACION", "Caja de compensación de asignación familiar: prestaciones sociales y crédito social."),
    (r"CASINO", "Operador de casino de juegos."),
    (r"HIPODROMO|\bHIPICO\b|HIPICA", "Club hípico: organización de carreras."),
    (r"DEPORTIV|\bFUTBOL\b|S\.?A\.?D\.?P|AZUL AZUL|BLANCO Y NEGRO|CRUZADOS|COLO COLO", "Club/sociedad deportiva profesional."),
    (r"TRANSMISORA|TRANSMISI[ÓO]N", "Transmisión de energía eléctrica."),
    (r"GENERACI[ÓO]N|ELECTRICA|ENERGIA|ENERGÍA|EOLIC|SOLAR", "Generación y/o suministro de energía eléctrica."),
    (r"\bGAS\b", "Distribución/comercialización de gas."),
    (r"VI[ÑN]A|VI[ÑN]EDOS|\bVINOS\b", "Viña: producción y comercialización de vinos."),
    (r"INMOBILIARIA|RENTAS|BODENOR|REDMEGACENTRO", "Inmobiliaria: desarrollo y/o renta de proyectos inmobiliarios."),
    (r"\bLEASING\b", "Arrendamiento financiero (leasing)."),
    (r"FACTORING|FACTOTAL|FACTOR", "Factoring y financiamiento de cuentas por cobrar."),
    (r"BOLSA DE|CONTRAPARTE CENTRAL|DEPOSITO CENTRAL|DCV", "Infraestructura de mercado de valores."),
    (r"CLASIFICADORA DE RIESGO|FELLER|HUMPHREYS|FITCH|ICR", "Clasificadora de riesgo."),
    (r"\bBUSES\b|TRANSPORTE DE PASAJEROS|METRO", "Operador de transporte de pasajeros."),
    (r"HOTEL", "Operación hotelera."),
    (r"CLINICA|M[ÉE]DIC|\bSALUD\b|HOSPITAL|DIAGNOSTICO", "Prestador de servicios de salud."),
    (r"ISAPRE|SEGUROS|\bVIDA\b", "Seguros / salud previsional."),
    (r"SALMONES|SEAFOODS|PESQUER|ACUICOLA|CULTIVOS MARINOS", "Acuicultura y/o pesca."),
    (r"FORESTAL|CELULOSA|ASERRADERO", "Actividad forestal / celulosa."),
    (r"AGRICOLA|AGROPECUARIA|AGROINDUSTRIAL|FRUTICOLA|HORTI", "Agroindustria / actividad agropecuaria."),
    (r"BANCO", "Entidad bancaria."),
    (r"TELEFON|TELECOMUNICACION|\bTV\b|TELEVISION|CABLE", "Telecomunicaciones / medios."),
    (r"CONSTRUCTORA|CONSTRUCCION|INGENIERIA|MONTAJES", "Construcción e ingeniería."),
    (r"INVERSIONES|\bHOLDING\b|MATRIZ|RENTAS Y", "Sociedad de inversiones / holding."),
    (r"COMERCIAL|RETAIL|TIENDAS|SUPERMERCADO", "Comercio minorista (retail)."),
    (r"NAVIER|MARITIM|MAR[ÍI]TIM", "Transporte marítimo / negocio naviero."),
    (r"CEMENTO|HORMIG[ÓO]N", "Producción de cemento y materiales de construcción."),
    (r"MINER|\bORO\b|\bCOBRE\b|LITIO", "Minería / recursos naturales."),
    (r"UNIVERSIDAD|EDUCACION|EDUCACIONAL", "Institución de educación."),
    (r"QU[ÍI]MIC|OX[ÍI]GENO|GASES", "Química industrial."),
    (r"ENJOY|ENTRETENCION|RESORT", "Entretenimiento, casinos y resorts."),
    (r"ALIMENTOS|LACTEO|CONSERVA", "Industria de alimentos."),
    (r"ZONA FRANCA", "Operación de zona franca."),
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


def infer_profile(name: str) -> str:
    """Reseña por tipo, inferida del nombre."""
    n = (name or "").upper()
    for pat, desc in _INFER:
        if re.search(pat, n):
            return desc
    return _GENERIC


def get_profile(rut: str, name: str) -> dict:
    """Reseña de una empresa: {text, source}. source = 'curada' | 'inferida' | 'generica'."""
    rut = str(rut).strip().replace(".", "").replace("-", "")
    curated = _load().get(rut)
    if curated:
        return {"text": curated, "source": "curada"}
    inferred = infer_profile(name)
    if inferred == _GENERIC:
        # Sin reseña: se deja el nombre de la empresa como marca/placeholder.
        return {"text": name, "source": "generica"}
    return {"text": inferred, "source": "inferida"}
