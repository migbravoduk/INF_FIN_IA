"""
processors/normalizer.py — Normalización de datos entre fuentes

Responsabilidades:
  - Validar que las observaciones tienen fecha y valor válidos
  - Remover duplicados dentro de un batch
  - Formatear fechas al estándar ISO 8601
  - Detectar outliers extremos (opcional)
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def normalize_observations(
    records: list[dict],
    series_id: str,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> list[dict]:
    """
    Normaliza y valida una lista de observaciones.

    Args:
        records:    Lista de dicts con 'date' y 'value'
        series_id:  ID de la serie (para logging)
        min_value:  Valor mínimo aceptable (None = sin límite)
        max_value:  Valor máximo aceptable (None = sin límite)

    Returns:
        Lista de dicts normalizados, sin duplicados por fecha
    """
    seen_dates = set()
    normalized = []
    skipped = 0

    for rec in records:
        date_str = _normalize_date(rec.get("date"))
        value = _normalize_value(rec.get("value"))

        # Validar fecha
        if not date_str:
            logger.debug(f"[{series_id}] Fecha inválida: {rec.get('date')} — omitida")
            skipped += 1
            continue

        # Validar valor
        if value is None:
            logger.debug(f"[{series_id}] Valor nulo en {date_str} — omitido")
            skipped += 1
            continue

        # Validar rango
        if min_value is not None and value < min_value:
            logger.warning(f"[{series_id}] Valor {value} en {date_str} < mínimo {min_value}")
        if max_value is not None and value > max_value:
            logger.warning(f"[{series_id}] Valor {value} en {date_str} > máximo {max_value}")

        # Deduplicación: mantener el último valor visto para la misma fecha
        if date_str in seen_dates:
            # Reemplazar el anterior
            normalized = [r for r in normalized if r["date"] != date_str]
        seen_dates.add(date_str)

        normalized.append({"date": date_str, "value": value})

    if skipped:
        logger.info(f"[{series_id}] {skipped} registros omitidos en normalización")

    # Ordenar por fecha
    normalized.sort(key=lambda x: x["date"])
    return normalized


def _normalize_date(raw) -> Optional[str]:
    """Convierte varios formatos de fecha a ISO 8601 'YYYY-MM-DD'."""
    if raw is None:
        return None

    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%d")

    raw_str = str(raw).strip()

    # Intentar múltiples formatos
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y", "%Y-%m"):
        try:
            dt = datetime.strptime(raw_str, fmt)
            if fmt == "%Y-%m":
                # Para fechas mensuales, usar el primer día del mes
                return dt.strftime("%Y-%m-01")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _normalize_value(raw) -> Optional[float]:
    """Convierte a float desde string o número."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = str(raw).strip().replace(" ", "")
    if cleaned in ("", "NaN", "N/A", "-", "nd", "ND"):
        return None
    # Manejar separadores de miles y decimales
    # Formato chileno: 1.234,56 → 1234.56
    if "," in cleaned and "." in cleaned:
        if cleaned.index(".") < cleaned.index(","):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
