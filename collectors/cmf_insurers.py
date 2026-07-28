"""
collectors/cmf_insurers.py — Colector de EEFF de Compañías de Seguros (FECU CMF).

Cubre ambos ramos, que comparten formato y plan de cuentas FECU (mismo parser):
  - 'vida'      → seg_vida_fecu1.php
  - 'generales' → seg_gen_fecu1.php
Fuente: https://www.cmfchile.cl/institucional/estadisticas/seg_{vida,gen}_fecu_index.php
La consulta con "TODOS" (society=0) devuelve, en un solo archivo Excel, todas las
compañías del ramo para un período, en formato tabla (compañías × cuentas). Descargamos ese
Excel (mismo esquema robusto que la API JSON usada para bancos) y lo normalizamos al esquema
`cmf_insurer_statements`.

Layout del Excel "TODOS":
  - fila de secciones: rótulos de estado (Situación financiera / Resultado integral / Flujos)
    al inicio de cada bloque de columnas.
  - fila de encabezado: Fecha | RUT | Razón social | Tipo de compañía | <cuentas...>
    donde cada cuenta es "<código FECU><glosa>" concatenados (ej. '5.10.00.00Totalactivo').
  - una fila por compañía; la última fila 'Total Cias...' es el agregado de mercado (se omite).
  - unidades: MILES de pesos (nota al pie del reporte).

Solo compañías de seguros directas (tiposociedad='A'); reaseguradoras ('R') y crédito ('CR')
se ignoran.
"""

import logging
from pathlib import Path

import httpx

from collectors.cmf_fecu import parse_fecu_workbook

logger = logging.getLogger(__name__)


class CMFInsurerCollector:
    """Descarga y normaliza los EEFF de compañías de seguros (vida o generales) desde la CMF."""

    # Endpoint de la consulta según ramo.
    RAMO_ENDPOINTS = {
        "vida": "https://www.cmfchile.cl/institucional/estadisticas/seg_vida_fecu1.php",
        "generales": "https://www.cmfchile.cl/institucional/estadisticas/seg_gen_fecu1.php",
    }
    # Token de control fijo que la CMF exige en la consulta (validado empíricamente).
    CONTROL = "Berlin39"
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    }

    # Trimestres válidos de la FECU de seguros.
    VALID_MONTHS = (3, 6, 9, 12)

    def __init__(self, insurance_type: str = "vida", raw_dir: str = "data/insurer_raw"):
        if insurance_type not in self.RAMO_ENDPOINTS:
            raise ValueError(f"insurance_type debe ser uno de {list(self.RAMO_ENDPOINTS)}.")
        self.insurance_type = insurance_type
        self.base_url = self.RAMO_ENDPOINTS[insurance_type]
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------------
    def _build_url(self, year: int, month: int, freq: str) -> str:
        anual = "y" if freq == "anual" else "n"
        mm = f"{month:02d}"
        return (
            f"{self.base_url}?auth=&send=&control={self.CONTROL}&lang=es&porc=0&vigente="
            f"&anual={anual}&tiposociedad=A&society=0&ag=&cuenta="
            f"&mes1={mm}&anno1={year}&mes2={mm}&anno2={year}&ifrs=1&xls=y&vsn=2"
        )

    def _download(self, year: int, month: int, freq: str, force: bool) -> Path | None:
        cache = self.raw_dir / f"seguros_{self.insurance_type}_{freq}_{year}_{month:02d}.xlsx"
        if cache.exists() and not force:
            logger.info(f"Caché de seguros encontrada: {cache}")
            return cache
        url = self._build_url(year, month, freq)
        logger.info(f"Descargando FECU seguros ({freq}) {year}-{month:02d}...")
        try:
            r = httpx.get(url, headers=self.HEADERS, timeout=90, follow_redirects=True)
        except Exception as e:
            logger.error(f"Error de red descargando seguros {year}-{month:02d}: {e}")
            return None
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or "spreadsheet" not in ct and not r.content[:2] == b"PK":
            logger.warning(f"Sin Excel válido para seguros {year}-{month:02d} "
                           f"(HTTP {r.status_code}, ct={ct}).")
            return None
        cache.write_bytes(r.content)
        logger.info(f"Excel de seguros guardado: {cache} ({len(r.content)} bytes)")
        return cache

    # ------------------------------------------------------------------
    # Parseo (layout común → collectors/cmf_fecu.py)
    # ------------------------------------------------------------------
    def _parse_workbook(self, path: Path, year: int, month: int, freq: str) -> list[dict]:
        period = int(f"{year}{month:02d}")
        return [
            {
                "insurance_type": self.insurance_type,
                "year": year, "month": month, "period": period,
                "report_freq": freq, "rut": row["rut"], "company_name": row["company_name"],
                "statement_group": row["statement_group"], "account_code": row["account_code"],
                "account_name": row["account_name"], "value": row["value"],
            }
            for row in parse_fecu_workbook(path)
        ]

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def fetch_period(self, year: int, month: int, freq: str = "trimestral",
                     force_download: bool = False) -> list[dict]:
        """
        Descarga (o lee de caché) y normaliza TODAS las compañías de seguros de vida de un
        período. `freq` = 'trimestral' | 'anual'. Devuelve registros listos para insertar.
        """
        if month not in self.VALID_MONTHS:
            raise ValueError(f"Mes {month} inválido; la FECU de seguros es trimestral {self.VALID_MONTHS}.")
        if freq not in ("trimestral", "anual"):
            raise ValueError("freq debe ser 'trimestral' o 'anual'.")
        path = self._download(year, month, freq, force_download)
        if not path:
            return []
        try:
            return self._parse_workbook(path, year, month, freq)
        except Exception as e:
            logger.error(f"Error parseando Excel de seguros {year}-{month:02d}: {e}")
            return []
