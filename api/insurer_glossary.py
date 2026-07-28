"""
Glosario y utilidades de presentación de la cartera de seguros (Circular 1.835, archivo B.8).

Las glosas de los códigos TIPO_DE_INVERSION son OFICIALES (tabla "Codificación SVS | Tipos de
Inversión" de la CMF); la agrupación por clase de activo y geografía es propia del proyecto.
Todo vive en `config/insurer_investment_codes.yaml` — este módulo solo lo carga y lo indexa.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "insurer_investment_codes.yaml"


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def describe(code: str) -> dict:
    """
    Devuelve {code, label, glosa, clase, clase_label, geo} para un código de inversión.
    Un código que no esté en el glosario se devuelve con su propio código como etiqueta y
    clase 'otros': así una codificación nueva de la CMF aparece en pantalla en vez de
    desaparecer silenciosamente de los totales.
    """
    code = (code or "").strip().upper()
    d = _data()
    entry = d["codigos"].get(code)
    if not entry:
        return {"code": code, "label": code or "(sin código)",
                "glosa": "Código no presente en la codificación CMF cargada.",
                "clase": "otros", "clase_label": "Otros", "geo": "nacional"}
    clase = entry.get("clase", "otros")
    return {
        "code": code,
        "label": entry.get("label", code),
        "glosa": entry.get("glosa", ""),
        "clase": clase,
        "clase_label": d["clases"].get(clase, {}).get("label", clase),
        "geo": entry.get("geo", "nacional"),
    }


def clase_order() -> list[str]:
    """Claves de clase de activo en el orden de presentación definido en el YAML."""
    clases = _data()["clases"]
    return sorted(clases, key=lambda k: clases[k].get("orden", 99))


def clase_label(clase: str) -> str:
    return _data()["clases"].get(clase, {}).get("label", clase)


# La razón social viene tal cual la digita cada compañía en su archivo, así que llega con
# ruido: código interno al principio ("033 METLIFE…"), la Ñ reemplazada por '#'
# ("COMPA#IA"), espacios dobles o el campo en blanco. Se limpia solo para mostrar; en la BD
# se conserva el dato original.
#
# El prefijo a eliminar debe empezar en CERO ("033 "): es la firma de un código rellenado a la
# izquierda. Un dígito suelto puede ser parte del nombre —"4 LIFE SEGUROS DE VIDA" es una
# compañía real—, así que ante la duda se prefiere dejar el prefijo antes que mutilar la marca.
_PREFIJO_NUMERICO = re.compile(r"^0\d{1,3}\s+(?=\D)")
_ESPACIOS = re.compile(r"\s{2,}")


def clean_company_name(name: str, rut: str = "") -> str:
    """Razón social presentable. Si viene vacía, cae al RUT para no mostrar una fila anónima."""
    s = (name or "").strip()
    s = _PREFIJO_NUMERICO.sub("", s)
    # '#' solo aparece como sustituto de Ñ en estos archivos (COMPA#IA, ESPA#A).
    s = s.replace("#", "Ñ")
    s = _ESPACIOS.sub(" ", s).strip()
    return s or (f"RUT {rut}" if rut else "(sin identificar)")
