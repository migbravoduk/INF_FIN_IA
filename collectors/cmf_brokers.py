"""
collectors/cmf_brokers.py — Colector de EEFF de intermediarios de valores (FECU IFRS CMF).

Cubre **corredores de bolsa y agentes de valores** en una sola descarga: la consulta con
`tiposociedad=0` ("Todos") y `sociedad[]=0` devuelve ambos tipos, y el propio Excel trae la
columna "Tipo de intermediario" (CORREDORES / AGENTES) que se persiste en `broker_type`.

Fuente: https://www.cmfchile.cl/institucional/estadisticas/merc_valores/
        intermediarios_fecu_ifrs/intermediarios_ifrs_index.php

Comparte layout con las FECU de seguros → el parseo vive en `collectors/cmf_fecu.py`.
Particularidades frente a seguros:
  - Plan de cuentas propio de intermediarios (códigos tipo '11.01.00'), con 14 secciones
    (Activos, Pasivos, Patrimonio, Resultado por intermediación, … , Flujo neto …).
    Se guarda la sección original y además el grupo normalizado ESF/ERI/EFE.
  - Solo frecuencia TRIMESTRAL (no hay versión anual en esta consulta).
  - `estimado=2` = estándar IFRS (desde diciembre 2010); `estimado=1` sería la norma
    chilena antigua, que NO se carga (plan de cuentas distinto, no comparable).
  - Unidades: MILES de pesos.
"""

import logging
from pathlib import Path

import httpx

from collectors.cmf_fecu import parse_fecu_workbook

logger = logging.getLogger(__name__)


class CMFBrokerCollector:
    """Descarga y normaliza los EEFF de corredores de bolsa y agentes de valores."""

    BASE_URL = ("https://www.cmfchile.cl/institucional/estadisticas/merc_valores/"
                "intermediarios_fecu_ifrs/intermediarios_ifrs1.php")
    HEADERS = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    }

    # Trimestres válidos y estándar contable (2 = IFRS).
    VALID_MONTHS = (3, 6, 9, 12)
    ESTANDAR_IFRS = "2"

    def __init__(self, raw_dir: str = "data/broker_raw"):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Descarga
    # ------------------------------------------------------------------
    def _build_url(self, year: int, month: int) -> str:
        mm = f"{month:02d}"
        return (
            f"{self.BASE_URL}?lang=es&sociedad%5B%5D=0&ag=0&xls=y&tiposociedad=0"
            f"&mes1={mm}&mes2={mm}&anno1={year}&anno2={year}"
            f"&cuenta=0&estimado={self.ESTANDAR_IFRS}&vsn=2"
        )

    def _download(self, year: int, month: int, force: bool) -> Path | None:
        cache = self.raw_dir / f"intermediarios_{year}_{month:02d}.xlsx"
        if cache.exists() and not force:
            logger.info(f"Caché de intermediarios encontrada: {cache}")
            return cache
        logger.info(f"Descargando FECU intermediarios {year}-{month:02d}...")
        try:
            r = httpx.get(self._build_url(year, month), headers=self.HEADERS,
                          timeout=90, follow_redirects=True)
        except Exception as e:
            logger.error(f"Error de red descargando intermediarios {year}-{month:02d}: {e}")
            return None
        ct = r.headers.get("content-type", "")
        if r.status_code != 200 or ("spreadsheet" not in ct and r.content[:2] != b"PK"):
            logger.warning(f"Sin Excel válido para intermediarios {year}-{month:02d} "
                           f"(HTTP {r.status_code}, ct={ct}).")
            return None
        cache.write_bytes(r.content)
        logger.info(f"Excel de intermediarios guardado: {cache} ({len(r.content)} bytes)")
        return cache

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def fetch_period(self, year: int, month: int, force_download: bool = False) -> list[dict]:
        """
        Descarga (o lee de caché) y normaliza TODOS los intermediarios de valores
        (corredores + agentes) de un trimestre. Devuelve registros listos para insertar.
        """
        if month not in self.VALID_MONTHS:
            raise ValueError(f"Mes {month} inválido; la FECU de intermediarios es trimestral "
                             f"{self.VALID_MONTHS}.")
        path = self._download(year, month, force_download)
        if not path:
            return []
        period = int(f"{year}{month:02d}")
        try:
            rows = parse_fecu_workbook(path)
        except Exception as e:
            logger.error(f"Error parseando Excel de intermediarios {year}-{month:02d}: {e}")
            return []
        return [
            {
                # 'entity_type' viene del Excel: 'CORREDORES' | 'AGENTES'.
                "broker_type": row["entity_type"] or "SIN CLASIFICAR",
                "year": year, "month": month, "period": period,
                "rut": row["rut"], "company_name": row["company_name"],
                "statement_group": row["statement_group"], "section": row["section"],
                "account_code": row["account_code"], "account_name": row["account_name"],
                "value": row["value"],
            }
            for row in rows
        ]
