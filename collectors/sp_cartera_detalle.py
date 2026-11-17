"""
collectors/sp_cartera_detalle.py — Detalle de la "cartera desagregada" mensual de la SP.

El mismo ZIP que ya baja `SPPensionCollector.fetch_portfolio` (cuadro 1, composición) trae
otros ~28 cuadros. Este módulo los pasa a filas largas para `sp_cartera_detalle`:

  tabla      canónica, por TÍTULO (la numeración cambia entre años: 23 cuadros en 2010, 29 hoy)
  fondo      A–E
  afp        nombre de la AFP, o 'TOTAL' (columna total de los cuadros por AFP)
  grupo      agrupación: sector, "ESTATAL/FINANCIERO/EMPRESAS", moneda objeto (forwards) o el
             prefijo "TIPO DE SWAP:" de la glosa
  codigo     forwards: código del bloque (p. ej. WNMC); las filas siguientes son su desglose
  glosa      fila (emisor, contraparte, instrumento, moneda contraparte…)
  columna    cuadros por emisor: tipo de instrumento (BCU, BB, ACC…) o 'total'
  metrica    porcentaje | monto_pesos | monto_dolares | unidades | tasa_interes | plazo | …
  valor

Se omiten los cuadros transpuestos ("por tipo de fondo" de cada AFP: mismas celdas) y los
bloques "total AFP" / "total fondos" (sumas de lo que ya se guarda).

Unidades (subtítulos del XML): montos en MILLONES de pesos / dólares; porcentaje sobre el
activo del fondo; swaps = VALORIZACIÓN del contrato (con signo); forwards = unidades del
contrato en función del activo objeto, en millones de dólares.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET

# (patrón sobre el título sin tildes, tabla). El orden importa: lo específico primero.
_TABLAS = [
    (r"SWAPS? NACIONAL", "swap_nac"),
    (r"SWAPS? EXTRANJER", "swap_ext"),
    (r"FORWARD NACIONAL", "fwd_nac"),
    (r"FORWARD EXTRANJER", "fwd_ext"),
    (r"DERIVADOS NACIONAL", "deriv_nac"),       # formato antiguo (~2010): sin separar swaps
    (r"DERIVADOS EXTRANJER", "deriv_ext"),
    (r"INVERSION EN EL EXTRANJERO.*EMISOR", "emisor_ext"),
    (r"POR EMISOR E INSTRUMENTO", "emisor_nac"),
    (r"TASAS DE VALORACION", "tasas"),
    (r"CATEGORIA DE RIESGO.*LARGO PLAZO", "riesgo_lp"),
    (r"CATEGORIA DE RIESGO.*CORTO PLAZO", "riesgo_cp"),
    (r"PLAZO PROMEDIO", "plazo"),
    (r"EN ACCIONES", "acciones"),
    (r"BONOS NACIONALES", "bonos"),
    (r"CUOTAS DE FONDOS", "cuotas"),
    (r"DIVERSIFICACION POR INSTRUMENTOS FINANCIEROS", "activos"),
]
TABLAS_DERIVADOS = {"swap_nac", "swap_ext", "fwd_nac", "fwd_ext", "deriv_nac", "deriv_ext"}
TODAS = {t for _, t in _TABLAS}

# Wrappers que se procesan (los "*_fondo" / "listado_por_fondo" son transpuestos).
_WRAPPERS = {"listado_por_afp", "listado_por_instrumento", "listado_por_instrumento_monedaobjeto_afp"}
_METRICAS = {"porcentaje", "monto_pesos", "monto_dolares", "unidades", "tasa_interes", "plazo",
             "porcentaje_sobre_emisor"}
_META = ("nemotecnico", "moneda", "unidad_indexada", "emisor", "tipo_accion", "elegibilidad", "condicion")
_CODIGO = re.compile(r"^[A-Z]{4}$")

COLUMNAS = ["period", "tabla", "fondo", "afp", "grupo", "codigo", "fila", "glosa", "nemotecnico",
            "moneda", "unidad", "extra", "columna", "metrica", "valor"]


def _t(e) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _sin_tildes(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


def clasificar(titulo: str) -> str | None:
    t = _sin_tildes(titulo)
    return next((tabla for pat, tabla in _TABLAS if re.search(pat, t)), None)


def _num(e) -> float | None:
    txt = (e.text or "").strip()
    if not txt:
        return None
    try:
        return float(txt.replace(",", "."))
    except ValueError:
        return None


def _hijo(e, nombre):
    return next((c for c in e if _t(c) == nombre), None)


def _texto(e, nombre) -> str | None:
    c = _hijo(e, nombre)
    if c is None:
        return None
    return (c.text or "").strip() or None


def _titulo(listado) -> str:
    return next(((e.text or "") for e in listado.iter() if _t(e) == "titulo"), "")


def _meta(fila) -> dict:
    m = {k: _texto(fila, k) for k in _META}
    extra = ";".join(f"{k}={m[k]}" for k in ("emisor", "tipo_accion", "elegibilidad", "condicion") if m[k])
    return {"nemotecnico": m["nemotecnico"], "moneda": m["moneda"], "unidad": m["unidad_indexada"],
            "extra": extra or None}


def _celdas(col, afp, columna, base, out):
    for m in col:
        k = _t(m)
        if k in _METRICAS:
            v = _num(m)
            if v is not None:
                out.append((*base[:3], afp, *base[3:], columna, k, v))


def parse_listado(listado, period: int, tablas: set[str] | None = None) -> list[tuple]:
    """Filas largas (orden = COLUMNAS) de un elemento <listado>. [] si no interesa."""
    wrapper = next(iter(listado), None)
    if wrapper is None or _t(wrapper) not in _WRAPPERS:
        return []
    tabla = clasificar(_titulo(listado))
    if tabla is None or (tablas is not None and tabla not in tablas):
        return []
    out: list[tuple] = []
    for tf in wrapper:
        if _t(tf) != "tipofondo":          # total_fondos: suma de A–E
            continue
        fondo = (tf.get("codigo") or "").upper()
        if _t(wrapper) == "listado_por_instrumento":
            hdr = _hijo(tf, "instrumentos")
            instr = [(g.text or "").strip() for g in hdr] if hdr is not None else []
            for afp in tf:
                if _t(afp) != "afp":        # total_afps: suma de las AFP
                    continue
                nombre = (_texto(afp, "nombre") or "").upper()
                for ag in afp:
                    if _t(ag) != "agrupacion":
                        continue
                    for fila in ag:
                        if _t(fila) != "fila":
                            continue
                        md = _meta(fila)
                        base = (period, tabla, fondo, ag.get("nombre"), None, int(fila.get("numero") or 0),
                                _texto(fila, "glosa"), md["nemotecnico"], md["moneda"], md["unidad"], md["extra"])
                        cols = _hijo(fila, "columnas")
                        if cols is not None:
                            for i, c in enumerate(x for x in cols if _t(x) == "instrumento"):
                                _celdas(c, nombre, instr[i] if i < len(instr) else str(i), base, out)
                            tot = _hijo(cols, "total")
                            if tot is not None:
                                _celdas(tot, nombre, "total", base, out)
                        tot = _hijo(fila, "total")
                        if tot is not None:
                            _celdas(tot, nombre, "total", base, out)
            continue
        # listado_por_afp y forwards: columnas = AFP + total
        if _t(wrapper) == "listado_por_instrumento_monedaobjeto_afp":
            bloques = [(b.get("moneda_objeto"), a) for b in tf if _t(b) == "afp_por_moneda_objeto"
                       for a in b if _t(a) == "afp_por_agrupacion"]
        else:
            bloques = [(a.get("nombre"), a) for a in tf if _t(a) == "agrupacion"]
        for grupo_bloque, ag in bloques:
            grupo, codigo = grupo_bloque, None
            for fila in ag:
                if _t(fila) != "fila":
                    continue
                glosa = _texto(fila, "glosa") or ""
                if ":" in glosa and not grupo_bloque:      # "SWAP NACIONAL DE TASAS:BANCO X"
                    grupo, glosa = (s.strip() for s in glosa.split(":", 1))
                if _CODIGO.match(glosa):
                    codigo = glosa
                md = _meta(fila)
                base = (period, tabla, fondo, grupo, codigo, int(fila.get("numero") or 0), glosa,
                        md["nemotecnico"], md["moneda"], md["unidad"], md["extra"])
                cols = _hijo(fila, "columnas")
                for c in (cols if cols is not None else []):
                    if _t(c) == "afp":
                        _celdas(c, (_texto(c, "nombre") or "").upper(), None, base, out)
                    elif _t(c) == "total":
                        _celdas(c, "TOTAL", None, base, out)
    return out


def iter_listados(fileobj):
    """Recorre el XML por streaming: emite ('periodo', 'AAAA-MM') y luego (numero, <listado>)."""
    for _, el in ET.iterparse(fileobj, events=("end",)):
        t = _t(el)
        if t == "periodo":
            yield "periodo", (el.text or "").strip()
        elif t == "listado":
            yield el.get("numero"), el
            el.clear()
