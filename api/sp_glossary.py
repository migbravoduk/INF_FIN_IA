"""
Glosario de nemotécnicos de instrumentos de la cartera SP (fuente oficial).

Las descripciones OFICIALES fueron extraídas del glosario al pie de la vista de
cartera desagregada de la Superintendencia de Pensiones:
  https://www.spensiones.cl/apps/carteras/genera_desagregada_xsl.php (jul-2026)
Las etiquetas CORTAS son curadas para ejes de gráficos (la distinción deuda/
acciones en familias D/V sigue la convención SP de sus cuadros estadísticos).
Los códigos marcados [inferido] no están en el glosario oficial y se derivan
del patrón de nomenclatura; revisar si la SP publica su definición.

Sufijos entre paréntesis (notas oficiales del cuadro):
  (3) inversión indirecta en el extranjero vía fondos de inversión/mutuos
  (5) fondos cuyo subyacente son activos tradicionales
  (6) fondos cuyo subyacente son activos alternativos
"""

from __future__ import annotations

import re

# (etiqueta_corta, descripción_oficial_o_inferida)
GLOSSARY: dict[str, tuple[str, str]] = {
    # --- Estatales ---
    "BCU": ("Bonos BCCh en UF", "Bonos del Banco Central de Chile expresados en UF"),
    "PDC": ("Pagarés BCCh", "Pagarés descontables del Banco Central"),
    "BEC": ("Bonos BCCh en pesos", "Bonos del Banco Central de Chile en pesos [inferido]"),
    "BTP": ("Bonos Tesorería en pesos", "Bonos de la Tesorería General de la República, en pesos"),
    "BTU": ("Bonos Tesorería en UF", "Bonos de la Tesorería General de la República en UF"),
    "BVL": ("Bonos de reconocimiento", "Bonos de reconocimiento previsional (I.N.P. u otros) [inferido]"),
    # --- Financieras / empresas nacionales ---
    "ACC": ("Acciones", "Acciones de Sociedades Anónimas Abiertas"),
    "BEF": ("Bonos bancarios", "Bonos Bancarios emitidos por Instituciones Financieras"),
    "BSF": ("Bonos subord. bancarios", "Bonos subordinados emitidos por Instituciones Financieras"),
    "LHF": ("Letras hipotecarias", "Letras de crédito emitidas por Instituciones Financieras"),
    "SNM": ("Depósitos a plazo (M)", "Depósitos y títulos bancarios nacionales [inferido]"),
    "SNT": ("Depósitos a plazo (T)", "Depósitos y títulos bancarios nacionales [inferido]"),
    "BCA": ("Bonos canjeables", "Bonos canjeables en Acciones"),
    "BCS": ("Bonos securitizados", "Bonos respaldados por títulos de créditos transferibles"),
    "DEB": ("Bonos de empresas", "Bonos de Empresas Públicas y Privadas"),
    "BFI": ("Bonos de F. de inversión", "Bonos emitidos por fondos de inversión"),
    "CFID": ("F. inversión nac. (deuda)", "Cuotas de Fondos de Inversión nacionales"),
    "CFIV": ("F. inversión nac. (acciones)", "Cuotas de Fondos de Inversión nacionales"),
    "CFMD": ("F. mutuos nac. (deuda)", "Cuotas de Fondos mutuos nacionales"),
    "CFMV": ("F. mutuos nac. (acciones)", "Cuotas de Fondos mutuos nacionales"),
    "CSIN": ("Créditos sindicados", "Participaciones en convenios de crédito sindicados"),
    "EPA": ("Acciones en comandita", "Acciones de sociedades en comanditas por acciones"),
    "SPA": ("Acciones SpA", "Acciones de sociedades por acciones"),
    "DPF": ("Depósitos a plazo", "Depósitos a plazo fijo [inferido]"),
    # --- Extranjeros ---
    "ADR": ("ADR", "Certificados negociables representativos de títulos accionarios de entidades "
                   "extranjeras, emitidos por bancos depositarios en el extranjero"),
    "AEE": ("Acciones extranjeras", "Acciones de empresas y entidades bancarias extranjeras"),
    "ACPE": ("Capital privado ext. (acciones)", "Acciones/participaciones de capital privado extranjero [inferido]"),
    "BEE": ("Bonos de empresas ext.", "Bonos emitidos por empresas extranjeras"),
    "BCE": ("Bonos canjeables ext.", "Bonos canjeables/convertibles extranjeros [inferido]"),
    "BSE": ("Bonos subordinados ext.", "Bonos subordinados de entidades extranjeras [inferido]"),
    "CCPE": ("Coinversión capital privado ext.", "Operaciones de coinversión en capital privado extranjero"),
    "CDPE": ("Coinversión deuda privada ext.", "Operaciones de coinversión en deuda privada extranjera"),
    "CIED": ("F. inversión ext. (deuda)", "Cuotas de participación emitidas por Fondos de Inversión extranjeros"),
    "CIEV": ("F. inversión ext. (acciones)", "Cuotas de participación emitidas por Fondos de Inversión extranjeros"),
    "CMED": ("F. mutuos ext. (deuda)", "Cuotas de participación emitidas por Fondos mutuos extranjeros"),
    "CMEV": ("F. mutuos ext. (acciones)", "Cuotas de participación emitidas por Fondos mutuos extranjeros"),
    "EBC": ("Bonos soberanos ext.", "Títulos de crédito emitidos por Estados extranjeros y Bancos Centrales extranjeros"),
    "ELN": ("Notas estructuradas (ELN)", "Títulos de crédito indexados al retorno de otros activos"),
    "ETFA": ("ETF de acciones", "Títulos representativos de índices accionarios extranjeros"),
    "ETFB": ("ETF de renta fija", "Títulos representativos de índices de renta fija"),
    "TBE": ("Títulos de bancos ext.", "Títulos de crédito de renta fija emitidos por entidades bancarias extranjeras"),
    "TBI": ("Títulos bancarios internac.", "Títulos de crédito de entidades bancarias internacionales [inferido]"),
    "VCPE": ("Vehículos capital privado ext.", "Vehículos para llevar a cabo inversión en activos de capital privado extranjeros"),
    "VDPE": ("Vehículos deuda privada ext.", "Vehículos para llevar a cabo inversión en deuda privada extranjera"),
    "VIPE": ("Vehículos infraestructura ext.", "Vehículo para llevar a cabo inversión en infraestructura extranjera"),
    "VRPE": ("Vehículos inmobiliarios ext.", "Vehículo para llevar a cabo inversión en inmobiliario extranjero"),
    # --- Derivados (W = forwards, Y = con garantía bilateral, S = swaps) ---
    "WEM": ("Forwards de moneda ext.", "Forwards en monedas (cobertura de riesgo financiero en el extranjero)"),
    "WEN": ("Forwards ext. con CLP", "Forwards en el extranjero en que una de las monedas es nacional"),
    "WNM": ("Forwards nac. de moneda", "Forwards en monedas (cobertura de riesgo en el mercado nacional)"),
    "WNN": ("Forwards nac. monedas ext.", "Forwards nacionales en que ambas monedas son extranjeras"),
    "YEM": ("Forwards moneda ext. (gtía.)", "Forwards de monedas extranjeras con garantías bilaterales"),
    "YEMC": ("Forwards compra ext. (gtía.)", "Forwards de compra de monedas (ambas extranjeras) con garantías bilaterales"),
    "YEMV": ("Forwards venta ext. (gtía.)", "Forwards de venta de monedas (ambas extranjeras) con garantías bilaterales"),
    "YENC": ("Forwards compra ext./CLP (gtía.)", "Forwards de compra en el extranjero con una moneda nacional, con garantías bilaterales"),
    "YENV": ("Forwards venta ext./CLP (gtía.)", "Forwards de venta en el extranjero con una moneda nacional, con garantías bilaterales"),
    "YNMC": ("Forwards compra nac. (gtía.)", "Forwards de compra de monedas con una moneda nacional, con garantías bilaterales"),
    "YNMV": ("Forwards venta nac. (gtía.)", "Forwards de venta de monedas con una moneda nacional, con garantías bilaterales"),
    "SEM": ("Swap ext. de moneda", "Swap extranjero de moneda [inferido del patrón YSEM]"),
    "SET": ("Swap ext. de tasas", "Swap extranjero de tasas de interés [inferido del patrón YSET]"),
    "YSEM": ("Swap ext. moneda (gtía.)", "Swap extranjero de moneda que emplea garantías bilaterales"),
    "YSET": ("Swap ext. tasas (gtía.)", "Swap extranjero de tasas de interés que emplea garantías bilaterales"),
    "YSNM": ("Swap nac. moneda (gtía.)", "Swap nacional de moneda que emplea garantías bilaterales"),
    "YSNT": ("Swap nac. tasas (gtía.)", "Swap nacional de tasas de interés que emplea garantías bilaterales"),
    # --- Disponible ---
    "CC2": ("Caja inversiones nac.", "Banco Inversiones nacionales"),
    "CC3": ("Caja inversiones ext.", "Banco Inversiones extranjeras"),
}

_SUFFIX_NOTES = {
    "3": "indirecto vía fondos",
    "5": "subyacente tradicional",
    "6": "subyacente alternativo",
}


def describe(glosa: str) -> dict:
    """
    Traduce un nemotécnico SP (posiblemente con sufijos '(3)(5)') a etiqueta corta
    y descripción. Devuelve {code, label, desc}; si no se conoce, label = código.
    """
    glosa = str(glosa).strip()
    base = re.sub(r"\(\d\)", "", glosa).strip()
    suffixes = re.findall(r"\((\d)\)", glosa)
    notes = [_SUFFIX_NOTES[s] for s in suffixes if s in _SUFFIX_NOTES]

    if base in GLOSSARY:
        label, desc = GLOSSARY[base]
    else:
        label, desc = glosa, "Nemotécnico SP sin glosa registrada"

    if notes:
        label = f"{label} ({', '.join(notes)})"
        desc = f"{desc}. Notas: {'; '.join(notes)}."
    return {"code": glosa, "label": label, "desc": desc}
