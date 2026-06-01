"""
collectors/cmf_banks.py — Colector para los estados financieros mensuales de bancos (Fase 4)

Descarga los datos directamente desde la API REST pública de la CMF/SBIF.
"""

import os
import logging
import json
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


class CMFBankCollector:
    """Colector para descargar y procesar balances y estados de resultados de bancos en Chile."""

    BASE_URL = "https://api.sbif.cl/api-sbifv3/recursos_api"
    # API Key pública y oficial proveída en el frontend de la CMF
    PUBLIC_API_KEY = "3a440ec14ceec35463beaf361631829c0ed9dc8d"

    # Catálogo de los bancos principales en Chile y sus códigos SBIF estándar de 3 dígitos
    BANKS_CATALOG = {
        "001": "BANCO DE CHILE",
        "009": "BANCO INTERNACIONAL",
        "012": "BANCO DEL ESTADO DE CHILE",
        "014": "SCOTIABANK CHILE",
        "016": "BANCO DE CREDITO E INVERSIONES",
        "028": "BANCO BICE",
        "031": "HSBC BANK (CHILE)",
        "037": "BANCO SANTANDER-CHILE",
        "039": "BANCO ITAU CHILE",
        "049": "BANCO SECURITY",
        "051": "BANCO FALABELLA",
        "053": "BANCO RIPLEY",
        "055": "BANCO CONSORCIO",
        "059": "BANCO BTG PACTUAL CHILE",
        "060": "CHINA CONSTRUCTION BANK, AGENCIA CHILE",
        "061": "BANK OF CHINA, AGENCIA EN CHILE",
    }

    def __init__(self, raw_dir: str = "data/bank_raw"):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def fetch_bank_report(
        self, year: int, month: int, bank_code: str, report_type: str = "balance", force_download: bool = False
    ) -> list[dict]:
        """
        Descarga (o lee de caché) el balance o estado de resultados de un banco específico.
        Normaliza los registros en un formato compatible con el esquema DuckDB.
        """
        # Validaciones de entrada
        clean_bank_code = str(bank_code).strip().zfill(3)
        if clean_bank_code not in self.BANKS_CATALOG:
            logger.warning(f"Código de banco '{bank_code}' no reconocido en el catálogo principal.")

        if report_type not in ("balance", "resultado"):
            raise ValueError("report_type debe ser 'balance' (MB1) o 'resultado' (MR1).")

        month_str = f"{month:02d}"
        period = int(f"{year}{month_str}")

        # Definición de archivos locales de caché
        cache_file = self.raw_dir / f"{report_type}_{year}_{month_str}_{clean_bank_code}.json"

        # 1. Descarga o Lectura de caché JSON
        if not cache_file.exists() or force_download:
            # Endpoints dinámicos de la API CMF Bancos
            if report_type == "balance":
                endpoint = f"balances/{year}/{month_str}/instituciones/{clean_bank_code}"
                response_key = "CodigosBalances"
            else:
                endpoint = f"resultados/{year}/{month_str}/instituciones/{clean_bank_code}"
                response_key = "CodigosEstadosDeResultado"

            url = f"{self.BASE_URL}/{endpoint}?apikey={self.PUBLIC_API_KEY}&formato=json"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            logger.info(f"Llamando a CMF Bank API ({report_type}) para el banco {clean_bank_code} en {year}-{month_str}...")
            try:
                r = httpx.get(url, headers=headers, timeout=45, follow_redirects=True)
                if r.status_code != 200:
                    # Si no hay datos, puede retornar un código HTTP de error
                    logger.warning(f"La API de Bancos no retornó datos (HTTP {r.status_code}) para el período {year}-{month_str} y banco {clean_bank_code}.")
                    return []

                # Guardar en caché local
                # Decodificar usando utf-8-sig por si contiene BOM de bytes invisibles
                content_text = r.content.decode("utf-8-sig", errors="ignore")
                
                # Validar que sea un JSON válido
                parsed_json = json.loads(content_text)
                
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=2, ensure_ascii=False)
                
                logger.info(f"Datos bancarios guardados en caché local: {cache_file}")
            except Exception as e:
                logger.error(f"Error descargando reporte bancario desde la API: {e}")
                # Si falla la descarga y el archivo de caché no existe, retornar vacío
                return []
        else:
            logger.info(f"Caché local encontrada. Cargando reporte bancario: {cache_file}")
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    parsed_json = json.load(f)
            except Exception as e:
                logger.error(f"Error leyendo archivo de caché bancaria {cache_file}: {e}")
                return []

        # 2. Parseo y Normalización del JSON estructurado
        if report_type == "balance":
            response_key = "CodigosBalances"
        else:
            response_key = "CodigosEstadosDeResultado"

        api_records = parsed_json.get(response_key, [])
        if not api_records:
            logger.warning(f"No se encontraron cuentas financieras bajo la clave '{response_key}' en el JSON.")
            return []

        normalized_records = []
        for rec in api_records:
            try:
                account_code = str(rec.get("CodigoCuenta", "")).strip()
                account_name = str(rec.get("DescripcionCuenta", "")).strip()
                bank_name = str(rec.get("NombreInstitucion", "")).strip()

                if not account_code or not account_name:
                    continue

                # Normalización de los desgloses de moneda (strings con decimales esp. ',00')
                val_clp_no_reaj = self._parse_amount(rec.get("MonedaChilenaNoReajustable"))
                val_clp_reaj_ipc = self._parse_amount(rec.get("MonedaReajustablePorIPC"))
                val_clp_reaj_tc = self._parse_amount(rec.get("MonedaReajustablePorTipoDeCambio")) # Solo balances
                val_extranjera = self._parse_amount(rec.get("MonedaExtranjera"))
                val_total = self._parse_amount(rec.get("MonedaTotal"))

                normalized_records.append({
                    "year": year,
                    "month": month,
                    "period": period,
                    "bank_code": clean_bank_code,
                    "bank_name": bank_name,
                    "report_type": report_type,
                    "account_code": account_code,
                    "account_name": account_name,
                    "val_clp_no_reaj": val_clp_no_reaj,
                    "val_clp_reaj_ipc": val_clp_reaj_ipc,
                    "val_clp_reaj_tc": val_clp_reaj_tc if report_type == "balance" else None,
                    "val_extranjera": val_extranjera,
                    "val_total": val_total
                })
            except Exception as ex:
                logger.debug(f"Error procesando registro bancario: {ex}. Registro: {rec}")
                continue

        return normalized_records

    def _parse_amount(self, val) -> float:
        """Parsea de forma segura a float los saldos numéricos con coma decimal y puntos de miles."""
        if val is None:
            return 0.0
        val_str = str(val).strip().replace(" ", "")
        if val_str in ("", "-", "NaN", "N/A", "nd", "ND"):
            return 0.0
        # Formato chileno de la API: 12.345,67 → 12345.67
        # Reemplazar puntos de miles por vacío y comas decimales por puntos
        val_clean = val_str.replace(".", "").replace(",", ".")
        try:
            return float(val_clean)
        except ValueError:
            return 0.0
