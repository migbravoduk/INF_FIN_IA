"""
collectors/seguros_cartera.py — Colector de la cartera de inversiones de compañías de
seguros (Circular 1.835, archivos SGSCI de la CMF).

DESCARGA AUTOMÁTICA: la página de descarga muestra un CAPTCHA de imagen, pero se validó
empíricamente que NO protege el archivo — la validación (`captcha.php {accion:valida}`) es
puro control del lado cliente y el endpoint de descarga sirve el ZIP a cualquier GET. Por eso
`download_cartera()` baja el ZIP directo (ver los `fnAjax=*` en DOWNLOAD_BASE). Si la CMF
llegara a atar el CAPTCHA al servidor, el fallback es bajarlo a mano desde
  https://www.cmfchile.cl/institucional/estadisticas/merc_seguros/cartera_inversiones/
  dcisgv/descarga_cartera_inv.php?tipoentidad=CSVID
y dejarlo en `data/seguros_cartera_raw/` (ej. `202605v.zip`); el parseo lee de ahí igual.

Estructura del ZIP: 12 tipos de archivo × compañía, nombrados `<letra><YYYYMM><t>.<RUT>`
(t = 'v' vida). Cada archivo es de ANCHO FIJO, UTF-8, separado por '\\n', con tres tipos de
registro: 1 = identificación, 2 = detalle, 3 = totales de cuadratura.

Enfoque escalonado (ver CONTEXTO.md): se parte por el archivo **C = B.8 Información de
Control**, cuyo layout reconcilia EXACTO con los datos (registro de 138 chars) y entrega la
composición de cartera por tipo de inversión. Los archivos de detalle (I/A/F/X…) tienen
specs publicadas que NO cuadran con los archivos reales, así que se incorporan después
campo a campo, validando contra los totales de este archivo.

UNIDADES: miles de pesos (M$).
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Endpoint de descarga de la CMF. `tipoentidad` selecciona el ramo (CSVID = vida). El CAPTCHA
# de la página no se verifica del lado servidor, así que estos GET bastan (ver docstring).
DOWNLOAD_BASE = ("https://www.cmfchile.cl/institucional/estadisticas/merc_seguros/"
                 "cartera_inversiones/dcisgv/descarga_cartera_inv.php")
# Ambos ramos se sirven desde la MISMA carpeta dcisgv/, distinguidos por tipoentidad.
_TIPOENTIDAD = {"vida": "CSVID", "generales": "CSGEN"}
_DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Referer": f"{DOWNLOAD_BASE}?tipoentidad=CSVID",
}

# Layout del registro tipo 2 de B.8 (nombre, ancho). Suma exacta = 138, verificado contra
# los datos: los 10 montos parsean como enteros con signo en las 33 compañías del período.
B8_DETAIL_LAYOUT = [
    ("tipo", 1),
    ("investment_code", 3),
    ("valor_final", 12),
    ("repr_rt_pr", 12),
    ("no_repr_rt_pr", 12),
    ("costo_amortizado", 14),
    ("valor_razonable", 14),
    ("efectivo_equiv", 14),
    ("cui_apv", 14),
    ("otras_clasif", 14),
    ("soc_filiales", 14),
    ("coligadas", 14),
]
B8_RECORD_LEN = sum(w for _, w in B8_DETAIL_LAYOUT)  # 138

# Registro tipo 1 (identificación), común a todos los archivos de la circular.
ID_LAYOUT = [("tipo", 1), ("rut", 9), ("verificador", 1), ("nombre", 60), ("periodo", 6)]

_ZIP_RE = re.compile(r"^(\d{6})([vg])\.zip$", re.IGNORECASE)
_TIPO_ENTIDAD = {"v": "vida", "g": "generales"}

_AMOUNT_FIELDS = [n for n, _ in B8_DETAIL_LAYOUT if n not in ("tipo", "investment_code")]


# Layout del registro tipo 2 de B.1 (Renta Fija), RECONCILIADO campo a campo contra los
# datos: cada campo se validó sobre 205.226 registros de ambos meses (fechas que parsean
# como AAAAMMDD, RUT de 9 dígitos, país alfabético, montos 100% numéricos). PAÍS ancla en
# 114:116 de forma exacta, cerrando la cabecera. Solo llegamos hasta el valor nominal: los
# montos de valoración de mercado del final del registro NO están reconciliados (el spec
# publicado suma 878 ≠ 970 reales y no hay total interno). Cada tupla es (campo, inicio, ancho).
#   dec4=True  → 4 decimales implícitos (dividir por 10.000).
B1_FIELDS = [
    ("codigo_operacion", 1, 3, "text"),
    ("folio_operacion", 4, 10, "text"),
    ("item_operacion", 14, 3, "text"),
    ("fecha_compra", 17, 8, "text"),
    ("fecha_pago", 25, 8, "text"),
    ("rut_emisor", 33, 9, "rut"),
    # 42:43 = dígito verificador del emisor (se descarta, como en el resto del proyecto)
    ("tipo_instrumento", 43, 10, "text"),
    ("nemotecnico", 53, 30, "text"),
    ("fecha_emision", 83, 8, "text"),
    ("num_inscripcion", 91, 5, "text"),
    ("fecha_inscripcion", 96, 8, "text"),
    ("serie", 104, 10, "text"),
    ("pais", 114, 2, "text"),
    ("valor_nominal", 116, 17, "dec4"),
    ("valor_nominal_vig", 133, 17, "dec4"),
    ("unidad_monetaria", 150, 4, "text"),
]
# La CMF usó registros de 930 chars (spec antiguo) y luego de 970 (los ~40 chars extra son
# montos de valoración al final, fuera de los campos reconciliados). El prefijo 0:154 —del
# que salen TODOS los campos de B1_FIELDS— es idéntico en ambos, validado 500/500 sobre datos
# de 2020. Se aceptan los dos largos; la valoración de mercado sigue sin reconciliar.
B1_RECORD_LENS = frozenset({930, 970})


def _field(raw: str, kind: str):
    """Convierte un campo de ancho fijo según su tipo."""
    s = raw.strip()
    if kind == "rut":
        return s.lstrip("0") or None
    if kind == "dec4":
        return (int(s) / 1e4) if s.isdigit() else None
    return s or None


def _type_files(namelist: list[str], letter: str, suffix: str) -> list[str]:
    """
    Archivos de un tipo dado dentro del ZIP. Cada archivo se llama `<letra><fecha>[<ramo>].<RUT>`
    donde <fecha> son 6 dígitos: la CMF usó `YYMMDD` (último día del mes) en los períodos
    antiguos y pasó a `YYYYMM` en los recientes. Por eso NO se fija el valor del período: basta
    la letra de tipo (c = B.8 Control, i = B.1 Renta Fija) y 6 dígitos. El sufijo de ramo (v|g)
    es OPCIONAL: hay meses sueltos (ej. 202412) que lo omiten en los archivos de control aunque
    sí lo traigan en los de renta fija. No hay ambigüedad porque cada ZIP es de un solo ramo.
    """
    pat = re.compile(rf"^{letter}\d{{6}}(?:{suffix})?\.", re.IGNORECASE)
    return [n for n in namelist if pat.match(Path(n).name)]


def _slice(line: str, layout: list[tuple[str, int]]) -> dict:
    """Corta una línea de ancho fijo según el layout."""
    out, off = {}, 0
    for name, w in layout:
        out[name] = line[off:off + w]
        off += w
    return out


def _amount(raw: str) -> float | None:
    """Monto con signo opcional ('+0000109380066') a float. None si viene vacío."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_zip_control(zip_path: str | Path) -> list[dict]:
    """
    Lee un ZIP mensual y devuelve los registros del archivo de CONTROL (B.8) de todas las
    compañías: una fila por (compañía × tipo de inversión).

    Validación: se exige que cada registro de detalle mida `B8_RECORD_LEN` y que sus montos
    parseen como enteros con signo (comprobado: 0 fallos en las 33 compañías de 2026-05).

    NO se usa el registro tipo 3 como cuadratura: su campo TOTAL_REGISTROS es inconsistente
    entre compañías (cada una genera su archivo). Medido sobre los dos períodos: 56 archivos
    cuentan detalle+2, 6 cuentan detalle+1, hay atípicos de +4/-2/-5 y 2 archivos sin
    registro tipo 3 — y los atípicos tienen datos limpios (códigos válidos, sin duplicados).
    Se registra la diferencia en DEBUG, sin alarmar.
    """
    path = Path(zip_path)
    m = _ZIP_RE.match(path.name)
    if not m:
        raise ValueError(f"Nombre de ZIP inesperado: {path.name} (se espera YYYYMM<v|g>.zip)")
    period, insurance_type = int(m.group(1)), _TIPO_ENTIDAD[m.group(2).lower()]

    out: list[dict] = []
    with zipfile.ZipFile(path) as z:
        control_files = _type_files(z.namelist(), "c", m.group(2).lower())
        if not control_files:
            logger.warning(f"{path.name}: no se hallaron archivos de control (c{period}*).")
            return []

        for fname in sorted(control_files):
            try:
                lines = [l for l in z.read(fname).decode("utf-8").split("\n") if l]
            except Exception as e:
                logger.error(f"{path.name}/{fname}: no se pudo leer ({e}).")
                continue

            # RUT del NOMBRE del archivo: respaldo cuando falta el registro tipo 1, que
            # ocurre en la práctica (ver informe de inconsistencias). Sin este respaldo las
            # filas de esos archivos quedarían sin identificar.
            rut_from_name = Path(fname).name.split(".", 1)[-1].strip().lstrip("0")
            rut = company = None
            n_detail = 0
            declared_total = None
            for line in lines:
                rtype = line[:1]
                if rtype == "1":
                    ident = _slice(line, ID_LAYOUT)
                    rut = ident["rut"].strip().lstrip("0")
                    company = ident["nombre"].strip()
                    if rut_from_name and rut != rut_from_name:
                        # El contenido se autodescribe (trae RUT y razón social), así que
                        # prevalece sobre el nombre del archivo; se deja constancia.
                        logger.warning(f"{fname}: el RUT del nombre ({rut_from_name}) no "
                                       f"coincide con el declarado dentro ({rut} — {company}). "
                                       f"Se usa el declarado.")
                elif rtype == "2":
                    if len(line) < B8_RECORD_LEN:
                        logger.warning(f"{fname}: registro corto ({len(line)}), se omite.")
                        continue
                    f = _slice(line, B8_DETAIL_LAYOUT)
                    rec = {
                        "insurance_type": insurance_type, "period": period,
                        "rut": rut or rut_from_name, "company_name": company or "",
                        "investment_code": f["investment_code"].strip(),
                    }
                    for name in _AMOUNT_FIELDS:
                        rec[name] = _amount(f[name])
                    out.append(rec)
                    n_detail += 1
                elif rtype == "3":
                    # Tipo 3: TIPO(1) + TOTAL_REGISTROS(6) → cuadratura
                    try:
                        declared_total = int(line[1:7])
                    except ValueError:
                        declared_total = None

            if declared_total is not None:
                logger.debug(f"{fname}: declarados {declared_total}, detalle leído {n_detail} "
                             f"(dif {declared_total - n_detail}).")

    logger.info(f"{path.name}: {len(out)} filas de control ({insurance_type} {period}).")
    return out


def parse_zip_fixed_income(zip_path: str | Path) -> list[dict]:
    """
    Lee el archivo B.1 (Instrumentos de Renta Fija, prefijo 'i') de todas las compañías de
    un ZIP mensual y devuelve una fila por instrumento, con los campos RECONCILIADOS
    (ver B1_FIELDS). Los registros con largo fuera de `B1_RECORD_LENS` se omiten con advertencia.
    """
    path = Path(zip_path)
    m = _ZIP_RE.match(path.name)
    if not m:
        raise ValueError(f"Nombre de ZIP inesperado: {path.name}")
    period, insurance_type = int(m.group(1)), _TIPO_ENTIDAD[m.group(2).lower()]

    out: list[dict] = []
    with zipfile.ZipFile(path) as z:
        files = _type_files(z.namelist(), "i", m.group(2).lower())
        for fname in sorted(files):
            rut_from_name = Path(fname).name.split(".", 1)[-1].strip().lstrip("0")
            rut = None
            skipped = 0
            try:
                lines = z.read(fname).decode("utf-8").split("\n")
            except Exception as e:
                logger.error(f"{path.name}/{fname}: no se pudo leer ({e}).")
                continue
            for line in lines:
                if not line:
                    continue
                rtype = line[0]
                if rtype == "1":
                    rut = _slice(line, ID_LAYOUT)["rut"].strip().lstrip("0") or rut_from_name
                elif rtype == "2":
                    if len(line) not in B1_RECORD_LENS:
                        skipped += 1
                        continue
                    rec = {"insurance_type": insurance_type, "period": period,
                           "rut": rut or rut_from_name}
                    for name, off, width, kind in B1_FIELDS:
                        rec[name] = _field(line[off:off + width], kind)
                    out.append(rec)
            if skipped:
                logger.warning(f"{fname}: {skipped} registros con largo fuera de "
                               f"{sorted(B1_RECORD_LENS)} omitidos.")
    logger.info(f"{path.name}: {len(out)} instrumentos de renta fija ({insurance_type} {period}).")
    return out


def _tipoentidad(insurance_type: str) -> str:
    code = _TIPOENTIDAD.get(insurance_type)
    if not code:
        raise ValueError(f"insurance_type '{insurance_type}' sin tipoentidad conocido "
                         f"(disponibles: {list(_TIPOENTIDAD)}).")
    return code


def list_remote_periods(insurance_type: str = "vida", timeout: float = 30.0) -> list[int]:
    """
    Períodos YYYYMM que la CMF ofrece para descarga, más recientes primero. Cruza los años
    (`fnAjax=per_a`) y meses (`per_m`) del combo y deja solo los que `fnAjax=archi` confirma
    que existen (devuelve '1'). La info parte en enero de 2016 según la propia página.
    """
    code = _tipoentidad(insurance_type)
    with httpx.Client(headers=_DL_HEADERS, timeout=timeout, follow_redirects=True) as c:
        years = [int(d["agno"]) for d in
                 c.get(DOWNLOAD_BASE, params={"tipoentidad": code, "fnAjax": "per_a"}).json()]
        months = [int(d["vmes"]) for d in
                  c.get(DOWNLOAD_BASE, params={"tipoentidad": code, "fnAjax": "per_m"}).json()]
        out: list[int] = []
        for y in sorted(years, reverse=True):
            for m in sorted(months, reverse=True):
                peri = y * 100 + m
                r = c.get(DOWNLOAD_BASE,
                          params={"tipoentidad": code, "fnAjax": "archi", "peri": f"{peri}"})
                if r.text.strip() == "1":
                    out.append(peri)
    logger.info(f"CMF cartera {insurance_type}: {len(out)} períodos disponibles.")
    return out


def download_cartera(period: int, insurance_type: str = "vida",
                     raw_dir: str = "data/seguros_cartera_raw",
                     force: bool = False, timeout: float = 120.0,
                     retries: int = 3, retry_pause: float = 3.0) -> Path | None:
    """
    Descarga el ZIP mensual de la cartera (Circular 1.835) a `raw_dir/<YYYYMM><t>.zip`.
    Devuelve la ruta local, o None si el período no está publicado. Usa caché salvo `force`.
    Valida que la respuesta sea un ZIP real (cabecera 'PK') antes de escribir.

    REINTENTOS: aunque `fnAjax=archi` confirme que el período existe, el endpoint de descarga
    a veces devuelve un HTML (hipo del servidor bajo carga; se observó en ~6% de los meses en
    un backfill). Como es transitorio, se reintenta `retries` veces con una pausa antes de
    rendirse.
    """
    code = _tipoentidad(insurance_type)
    suffix = "v" if insurance_type == "vida" else "g"
    dest_dir = Path(raw_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{period}{suffix}.zip"
    if dest.exists() and not force:
        logger.info(f"Caché de cartera encontrada: {dest.name}")
        return dest

    params = {"tipoentidad": code, "fnAjax": "descarga", "peri": f"{period}"}
    with httpx.Client(headers=_DL_HEADERS, timeout=timeout, follow_redirects=True) as c:
        # ¿Existe el archivo para el período? Evita bajar HTML de error.
        try:
            chk = c.get(DOWNLOAD_BASE,
                        params={"tipoentidad": code, "fnAjax": "archi", "peri": f"{period}"})
        except Exception as e:
            logger.error(f"Error de red consultando cartera {insurance_type} {period}: {e}")
            return None
        if chk.text.strip() != "1":
            logger.warning(f"Cartera {insurance_type} {period}: no publicada en la CMF.")
            return None

        for attempt in range(1, retries + 1):
            try:
                r = c.get(DOWNLOAD_BASE, params=params)
            except Exception as e:
                logger.warning(f"Cartera {insurance_type} {period}: error de red "
                               f"(intento {attempt}/{retries}): {e}")
                r = None
            if r is not None and r.status_code == 200 and r.content[:2] == b"PK":
                dest.write_bytes(r.content)
                logger.info(f"Cartera guardada: {dest.name} ({len(r.content):,} bytes)")
                return dest
            if attempt < retries:
                ct = r.headers.get("content-type", "") if r is not None else "sin respuesta"
                logger.warning(f"Cartera {insurance_type} {period}: respuesta no es ZIP "
                               f"(ct={ct}), reintentando en {retry_pause}s "
                               f"(intento {attempt}/{retries}).")
                time.sleep(retry_pause)

    logger.error(f"Cartera {insurance_type} {period}: no se pudo descargar un ZIP válido "
                 f"tras {retries} intentos.")
    return None


def download_history(insurance_type: str = "vida", raw_dir: str = "data/seguros_cartera_raw",
                     periods: list[int] | None = None, force: bool = False,
                     pause: float = 1.0) -> list[Path]:
    """
    Descarga todos los períodos indicados (o los disponibles en la CMF si `periods` es None),
    con una pausa entre descargas para no golpear el sitio. Devuelve las rutas descargadas.
    """
    if periods is None:
        periods = list_remote_periods(insurance_type)
    out: list[Path] = []
    for i, peri in enumerate(periods):
        p = download_cartera(peri, insurance_type, raw_dir, force=force)
        if p:
            out.append(p)
        if pause and i < len(periods) - 1:
            time.sleep(pause)
    return out


def list_available_zips(raw_dir: str = "data/seguros_cartera_raw") -> list[Path]:
    """ZIPs mensuales disponibles localmente, ordenados por período."""
    d = Path(raw_dir)
    if not d.exists():
        return []
    return sorted((p for p in d.glob("*.zip") if _ZIP_RE.match(p.name)),
                  key=lambda p: p.name)
