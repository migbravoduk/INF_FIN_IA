"""
collectors/cmf.py — Colector para los estados financieros de la CMF (Fase 3)

Descarga e ingesta los archivos planos (.txt) trimestrales de la CMF.
"""

import os
import logging
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


class CMFCollector:
    """Colector para descargar y procesar archivos planos de estados financieros trimestrales de la CMF."""

    BASE_URL = "https://www.cmfchile.cl/institucional/estadisticas/ver_archivo.php"

    def __init__(self, raw_dir: str = "data/cmf_raw"):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def fetch_period(self, period: int, force_download: bool = False) -> list[dict]:
        """
        Descarga (o lee de caché) el archivo plano de la CMF para el periodo YYYYMM.
        Normaliza y parsea el contenido en una lista de registros listos para insertar en DuckDB.
        """
        period_str = str(period).strip()
        if not period_str.isdigit() or len(period_str) not in (4, 6):
            raise ValueError(
                f"Periodo inválido: '{period}'. Debe ser YYYY (anual, ej. 2024) o YYYYMM (trimestral, ej. 202512)."
            )

        if len(period_str) == 4:
            # Archivo anual histórico (contiene los 4 trimestres agrupados)
            file_path = self.raw_dir / f"eifrs{period_str}_anual.txt"
            url_params = f"inicio={period_str}03&termino={period_str}12"
            logger_msg = f"Descargando archivo agrupado CMF para año {period_str}..."
        else:
            # Archivo trimestral individual
            file_path = self.raw_dir / f"eifrs{period_str}_{period_str}.txt"
            url_params = f"inicio={period_str}&termino={period_str}"
            logger_msg = f"Descargando archivo trimestral CMF para período {period_str}..."

        # 1. Gestión de Descarga / Caché local
        if not file_path.exists() or force_download:
            logger.info(logger_msg)
            url = f"{self.BASE_URL}?{url_params}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            try:
                # Estos archivos planos pueden pesar entre 4MB y 8MB, usamos timeout holgado de 60s
                r = httpx.get(url, headers=headers, timeout=60, follow_redirects=True)
                if r.status_code != 200:
                    raise Exception(f"La CMF respondió con código de estado HTTP {r.status_code}")

                # Guardar el contenido binario crudo en caché
                with open(file_path, "wb") as f:
                    f.write(r.content)
                logger.info(f"Archivo descargado exitosamente guardado en: {file_path}")
            except Exception as e:
                logger.error(f"Error descargando datos de la CMF del período {period_str}: {e}")
                raise e
        else:
            logger.info(f"Caché local encontrada. Cargando archivo CMF: {file_path}")

        # 2. Parseo y Descodificación Multi-Encoding
        records = []
        encodings = ["utf-8", "latin-1", "cp1252"]
        content = None

        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                logger.info(f"Descodificado exitosamente usando codificación {enc}")
                break
            except UnicodeDecodeError:
                continue

        if content is None:
            # Fallback final ignorando errores de codificación para evitar caídas catastróficas
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            logger.warning("Decodificación forzada con ignorado de errores utf-8 (pérdida potencial de caracteres).")

        lines = content.splitlines()
        logger.info(f"Procesando {len(lines)} líneas de datos financieros...")

        parsed_count = 0
        for i, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue

            # El archivo es delimitado por punto y coma (;)
            parts = line_str.split(";")
            if len(parts) < 7:
                continue

            try:
                row_period = int(parts[0])
                # RUT: remover puntos y guiones para estandarizar búsquedas indexadas
                row_rut = str(parts[1]).strip().replace(".", "").replace("-", "")
                row_company = str(parts[2]).strip()
                row_report_type = str(parts[3]).strip()
                row_currency = str(parts[4]).strip()
                row_account = str(parts[5]).strip()
                row_val_str = str(parts[6]).strip()

                if not row_val_str:
                    continue

                row_value = float(row_val_str)
                taxonomy = parts[7].strip() if len(parts) > 7 else None
                group = parts[8].strip() if len(parts) > 8 else None

                records.append({
                    "period": row_period,
                    "rut": row_rut,
                    "company_name": row_company,
                    "report_type": row_report_type,
                    "currency": row_currency,
                    "account_name": row_account,
                    "value": row_value,
                    "taxonomy_code": taxonomy,
                    "statement_group": group
                })
                parsed_count += 1
            except Exception as ex:
                # Omitir errores de conversión en filas rotas aisladas para robustez
                logger.debug(f"Fila {i+1} corrupta o con formato inválido omitida: {ex}. Fila: {line_str[:80]}")
                continue

        logger.info(f"Conversión completada: {parsed_count} registros CMF parseados correctamente.")
        return records
