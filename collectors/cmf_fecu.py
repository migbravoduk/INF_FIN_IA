"""
collectors/cmf_fecu.py — Parser compartido de los Excel "FECU tabla" de la CMF.

Varias estadísticas de la CMF (seguros de vida, seguros generales, intermediarios de
valores) exponen la consulta "TODOS" como un Excel con el MISMO layout, así que el
parseo vive aquí una sola vez:

  - fila de secciones: rótulo del bloque (ej. 'Activos', 'Estado de flujos de efectivo')
    puesto SOLO en la primera columna del bloque → se arrastra (forward-fill).
  - fila de encabezado: Fecha | RUT | Razón social | <tipo de entidad> | <cuentas...>
    donde cada cuenta es "<código FECU><glosa>" concatenados (ej. '5.10.00.00Totalactivo'
    o '11.01.00Efectivoyefectivoequivalente').
  - una fila por entidad; filas sin RUT o que abren con 'Total' (agregado de mercado) se omiten.
  - unidades: MILES de pesos (nota al pie del propio reporte).

`parse_fecu_workbook` devuelve filas genéricas; cada colector les pone su propia forma
(período, tipo de ramo, etc.).
"""

import re
import logging
from pathlib import Path

import openpyxl

logger = logging.getLogger(__name__)

# Índices de las columnas de metadatos (comunes a todas las variantes).
COL_FECHA, COL_RUT, COL_NAME, COL_TYPE = 0, 1, 2, 3
FIRST_ACCOUNT_COL = 4

_CODE_RE = re.compile(r"^([0-9.]+)(.*)$")


def normalize_group(section_label: str) -> str:
    """
    Normaliza el rótulo de sección al grupo de estado: 'ESF' | 'ERI' | 'EFE' | 'OTRO'.

    El orden importa: primero flujos, luego resultado (que incluye ingresos/gastos y el
    bloque de OCI 'Ingresos (gastos) registrados con abono a patrimonio' — pese a decir
    "patrimonio", es resultado integral), y al final el balance.
    """
    low = (section_label or "").lower()
    if "flujo" in low:
        return "EFE"
    if "resultado" in low or "ingreso" in low or "gasto" in low:
        return "ERI"
    if "situaci" in low or "activo" in low or "pasivo" in low or "patrimonio" in low:
        return "ESF"
    return "OTRO"


def fix_mojibake(s: str) -> str:
    """
    Repara texto doble-codificado ('IntermediaciÃ³n' → 'Intermediación').

    El Excel de intermediarios viene con los bytes UTF-8 interpretados como latin-1; el de
    seguros viene correcto. La conversión solo se aplica cuando los bytes latin-1 del texto
    SON UTF-8 válido — que es justamente la firma del mojibake. Un texto sano como
    'Préstamos' falla al decodificar y se devuelve intacto.
    """
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def split_code_name(header: str) -> tuple[str, str]:
    """'5.10.00.00Totalactivo' → ('5.10.00.00', 'Totalactivo')."""
    m = _CODE_RE.match((header or "").strip())
    if not m:
        return "", (header or "").strip()
    return m.group(1).rstrip("."), fix_mojibake(m.group(2).strip())


def clean_rut(raw) -> str:
    """'96.573.600-K' → '96573600' (sin puntos ni dígito verificador)."""
    return str(raw or "").split("-")[0].replace(".", "").strip()


def parse_amount(val):
    """Monto a float; None si viene vacío o no numérico."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "")
    if s in ("", "-", "NaN", "N/A", "nd", "ND"):
        return None
    # Por si viniera como texto con formato chileno (12.345,67).
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_fecu_workbook(path: Path) -> list[dict]:
    """
    Parsea un Excel "FECU tabla" de la CMF.

    Devuelve una fila por (entidad × cuenta) con las claves:
      rut, company_name, entity_type (col 'Tipo de …'), section (rótulo original),
      statement_group ('ESF'|'ERI'|'EFE'|'OTRO'), account_code, account_name, value.
    Lista vacía si el archivo no tiene la estructura esperada.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # 1) Fila de encabezado = la que empieza con 'Fecha'.
    hdr_idx = next((i for i, r in enumerate(rows)
                    if r and str(r[COL_FECHA]).strip().lower() == "fecha"), None)
    if hdr_idx is None:
        logger.warning(f"No se halló la fila de encabezado en {path.name}.")
        return []
    header = rows[hdr_idx]
    section_row = rows[hdr_idx - 1] if hdr_idx > 0 else ()

    # 2) Columna de cuenta → (código, glosa, sección, grupo). El rótulo de sección solo
    #    aparece al inicio de su bloque, así que se arrastra hacia la derecha.
    col_meta: dict[int, tuple[str, str, str, str]] = {}
    current_section = ""
    for j in range(FIRST_ACCOUNT_COL, len(header)):
        label = section_row[j] if j < len(section_row) else None
        if label not in (None, ""):
            current_section = fix_mojibake(str(label).strip())
        head = header[j]
        if head in (None, ""):
            continue
        code, name = split_code_name(str(head))
        if not code:
            continue
        col_meta[j] = (code, name, current_section, normalize_group(current_section))

    # 3) Filas de entidades.
    out: list[dict] = []
    for r in rows[hdr_idx + 1:]:
        if not r:
            continue
        c0 = str(r[COL_FECHA]).strip() if r[COL_FECHA] is not None else ""
        if c0.lower().startswith("total"):
            break  # agregado de mercado: fin de los datos por entidad
        rut = clean_rut(r[COL_RUT] if len(r) > COL_RUT else "")
        name = (fix_mojibake(str(r[COL_NAME]).strip())
                if len(r) > COL_NAME and r[COL_NAME] is not None else "")
        if not rut or not name:
            continue
        etype = (fix_mojibake(str(r[COL_TYPE]).strip())
                 if len(r) > COL_TYPE and r[COL_TYPE] is not None else "")
        for j, (code, acc_name, section, group) in col_meta.items():
            if j >= len(r):
                continue
            out.append({
                "rut": rut, "company_name": name, "entity_type": etype,
                "section": section, "statement_group": group,
                "account_code": code, "account_name": acc_name,
                "value": parse_amount(r[j]),
            })
    return out
