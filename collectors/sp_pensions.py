"""
collectors/sp_pensions.py — Cliente colector para la Superintendencia de Pensiones (SP) de Chile

Descarga e ingesta:
  1. Valores cuota diarios de los multifondos (A, B, C, D, E) por AFP.
  2. Carteras mensuales desagregadas de los fondos de pensiones (XML).
  3. Cinta de precios diaria de instrumentos financieros (TXT dentro de ZIP).
"""

import os
import zipfile
import logging
import json
from pathlib import Path
from datetime import datetime, date
from typing import Optional
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from config.settings import settings

logger = logging.getLogger(__name__)


class SPPensionCollector:
    """Colector para descargar y parsear estadísticas de la Superintendencia de Pensiones."""

    BASE_URL = "https://www.spensiones.cl"

    def __init__(self, raw_dir: str = "data/sp_raw"):
        self.raw_dir = Path(raw_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

    # ============================================================
    # 1. Valores Cuota
    # ============================================================

    def fetch_fecconf(self, fund_type: str = "A") -> str:
        """
        Consulta el portal HTML de valores cuota para obtener la fecha de
        valores confirmados más reciente ('fecconf' YYYYMMDD).
        """
        url = f"{self.BASE_URL}/apps/valoresCuotaFondo/vcfAFP.php"
        params = {"tf": fund_type.upper()}

        logger.info(f"Consultando fecha de confirmación en la SP para fondo {fund_type}...")
        try:
            r = httpx.get(url, params=params, headers=self.headers, timeout=20)
            r.raise_for_status()
            
            soup = BeautifulSoup(r.content, "html.parser")
            
            # Buscar el elemento TH que contiene "Confirmados hasta"
            th_confirmados = soup.find(lambda tag: tag.name == "th" and "Confirmados hasta" in tag.text)
            if not th_confirmados:
                # Fallback: intentar buscar por texto general
                logger.warning("No se encontró 'Confirmados hasta' con th. Buscando texto general...")
                text_soup = soup.get_text()
                if "Confirmados hasta" in text_soup:
                    # Intentar buscar un patrón de fecha YYYY-MM-DD o DD-MMM-YYYY
                    import re
                    match = re.search(r'\d{2}-[A-Z]{3}-\d{4}', text_soup)
                    if match:
                        date_text = match.group(0)
                        return self._parse_sp_date_to_yyyymmdd(date_text)
                raise ValueError("No se pudo determinar la fecha de confirmación en la página de la SP.")

            # El valor de la fecha se encuentra en la fila siguiente, misma columna
            tr_parent = th_confirmados.find_parent("tr")
            tr_next = tr_parent.find_next_sibling("tr")
            if not tr_next:
                raise ValueError("Estructura de tabla de confirmación de fecha no coincide con la esperada.")
            
            th_cells = tr_next.find_all("th")
            if not th_cells:
                # Podría estar en un TD
                th_cells = tr_next.find_all("td")

            if not th_cells:
                raise ValueError("Fila de fecha de confirmación está vacía.")

            date_text = th_cells[0].get_text().strip()
            return self._parse_sp_date_to_yyyymmdd(date_text)

        except Exception as e:
            logger.error(f"Error consultando fecha de confirmación (fecconf) en la SP: {e}")
            raise e

    def fetch_quota_values(
        self, year_start: int, year_end: int, fund_type: str = "A", fecconf: Optional[str] = None
    ) -> list[dict]:
        """
        Descarga los valores cuota y patrimonio anualizados como CSV y los parsea.
        """
        fund_type = fund_type.upper()
        if fund_type not in ("A", "B", "C", "D", "E"):
            raise ValueError(f"Fondo inválido: {fund_type}. Debe ser A, B, C, D o E.")

        # Si no se provee fecconf, obtener la más reciente en vivo
        if not fecconf:
            fecconf = self.fetch_fecconf(fund_type)

        url = f"{self.BASE_URL}/apps/valoresCuotaFondo/vcfAFPxls.php"
        params = {
            "aaaaini": str(year_start),
            "aaaafin": str(year_end),
            "tf": fund_type,
            "fecconf": fecconf
        }
        
        # Referer es obligatorio para no obtener un 404
        req_headers = {
            **self.headers,
            "Referer": f"{self.BASE_URL}/apps/valoresCuotaFondo/vcfAFP.php?tf={fund_type}"
        }

        # Caché local del CSV para evitar sobrecarga y descargas redundantes
        cache_file = self.raw_dir / f"cuotas_{fund_type}_{year_start}_{year_end}_{fecconf}.csv"

        if not cache_file.exists():
            logger.info(f"Descargando valores cuota fondo {fund_type} ({year_start}-{year_end}) desde la SP...")
            try:
                r = httpx.get(url, params=params, headers=req_headers, timeout=45)
                r.raise_for_status()

                # Validación de integridad del archivo descargado
                if len(r.content) < 100:
                    raise Exception("El archivo descargado es demasiado pequeño o está corrupto.")
                
                content_sample = r.content.decode("latin-1", errors="ignore")
                if "Valores Confirmados" not in content_sample and "Fecha" not in content_sample:
                    raise Exception("El archivo descargado no parece contener la tabla de valores cuota.")

                # Guardar en caché
                with open(cache_file, "wb") as f:
                    f.write(r.content)
                logger.info(f"Valores cuota guardados en caché local: {cache_file}")

            except Exception as e:
                logger.error(f"Error descargando valores cuota {fund_type} desde la SP: {e}")
                # Si falla y hay una caché antigua del mismo rango de años (aunque difiera fecconf),
                # se podría intentar leer. Por ahora propagamos el error para robustez.
                raise e
        else:
            logger.info(f"Caché local encontrada. Cargando valores cuota: {cache_file}")

        # Parsear el CSV
        try:
            with open(cache_file, "r", encoding="latin-1", errors="ignore") as f:
                csv_content = f.read()
            return self._parse_quota_csv(csv_content, fund_type)
        except Exception as e:
            logger.error(f"Error parseando el archivo CSV de valores cuotas {cache_file}: {e}")
            raise e

    def _parse_quota_csv(self, content_text: str, fund_type: str) -> list[dict]:
        """Parsea el contenido de texto CSV delimitado por punto y coma."""
        lines = content_text.splitlines()
        records = []
        afp_cols = {}

        for line in lines:
            parts = [p.strip() for p in line.split(";")]
            if not parts or len(parts) < 2:
                continue

            # Identificar cabecera de las AFP
            # Ejemplo: Fecha;CAPITAL;;CUPRUM;;HABITAT;;MODELO;;PLANVITAL;;PROVIDA;;UNO
            if parts[0] == "Fecha" and "CAPITAL" in parts:
                for idx, part in enumerate(parts):
                    if part and part != "Fecha":
                        afp_cols[idx] = part.upper()
                continue

            # Identificar líneas de datos (Ej: 2025-01-01)
            date_str = parts[0]
            if len(date_str) == 10 and date_str[4] == "-" and date_str[7] == "-":
                for idx, afp_name in afp_cols.items():
                    if idx < len(parts) and (idx + 1) < len(parts):
                        raw_cuota = parts[idx]
                        raw_patrimonio = parts[idx + 1]
                        
                        cuota = self._parse_amount(raw_cuota)
                        patrimonio = self._parse_amount(raw_patrimonio)
                        
                        if cuota is not None and patrimonio is not None:
                            records.append({
                                "date": date_str,
                                "afp_name": afp_name,
                                "fund_type": fund_type,
                                "quota_value": cuota,
                                "equity_value": patrimonio
                            })
        
        logger.info(f"Parseados {len(records)} registros de valor cuota para el fondo {fund_type}")
        return records

    # ============================================================
    # 2. Cartera de Inversiones (Mensual)
    # ============================================================

    def fetch_portfolio(self, period: int) -> list[dict]:
        """
        Descarga la cartera de inversión mensual desagregada para el periodo YYYYMM.
        Obtiene el archivo ZIP, extrae el XML y parsea la distribución de activos.
        """
        period_str = str(period).strip()
        if not period_str.isdigit() or len(period_str) != 6:
            raise ValueError(f"Período inválido: {period}. Debe ser YYYYMM (ej. 202601).")

        cache_zip = self.raw_dir / f"cartera_{period_str}.zip"
        xml_name = f"cartera_desagregada{period_str}.xml"
        extracted_xml = self.raw_dir / xml_name

        # 1. Descarga del ZIP si no está en caché
        if not cache_zip.exists() and not extracted_xml.exists():
            # Obtener primero la página HTML del periodo que tiene el enlace de descarga cifrado
            html_url = f"{self.BASE_URL}/apps/loadCarteras/loadCarInv.php"
            html_params = {
                "menu": "sci",
                "menuN1": "estfinfp",
                "menuN2": "NOID",
                "orden": "10",
                "periodo": period_str,
                "ext": ".php"
            }
            
            logger.info(f"Consultando página de carteras para período {period_str}...")
            r_html = httpx.get(html_url, params=html_params, headers=self.headers, timeout=30)
            r_html.raise_for_status()

            soup = BeautifulSoup(r_html.content, "html.parser")
            
            # Buscar el enlace que contiene "GetFile_v2.0.php"
            zip_link_el = soup.find(lambda tag: tag.name == "a" and "GetFile_v2.0.php" in str(tag.get("href", "")))
            if not zip_link_el:
                # Comprobación adicional de si no está disponible aún
                if "no se encuentra disponible" in r_html.text:
                    logger.warning(f"La cartera de inversión para el período {period_str} aún no está disponible en la SP.")
                    return []
                raise ValueError(f"No se encontró el enlace de descarga ZIP en la página para el período {period_str}.")

            zip_href = zip_link_el.get("href")
            zip_url = f"{self.BASE_URL}{zip_href}" if zip_href.startswith("/") else zip_href

            # Descargar el ZIP
            logger.info(f"Descargando archivo ZIP de la cartera desde {zip_url}...")
            req_headers = {
                **self.headers,
                "Referer": f"{html_url}?menu=sci&menuN1=estfinfp&menuN2=NOID&orden=10&periodo={period_str}&ext=.php"
            }
            
            r_zip = httpx.get(zip_url, headers=req_headers, timeout=90)
            r_zip.raise_for_status()

            # Validación de integridad del ZIP
            if len(r_zip.content) < 5000:
                raise ValueError("El archivo ZIP de la cartera descargado es demasiado pequeño, puede estar corrupto.")

            with open(cache_zip, "wb") as f:
                f.write(r_zip.content)
            logger.info(f"Archivo ZIP de cartera guardado en: {cache_zip}")

        # 2. Descomprimir el XML
        if not extracted_xml.exists() and cache_zip.exists():
            logger.info(f"Descomprimiendo {xml_name} desde el archivo ZIP...")
            try:
                with zipfile.ZipFile(cache_zip, "r") as z:
                    # Encontrar el nombre real del archivo XML en el zip
                    real_xml_name = next((name for name in z.namelist() if name.endswith(".xml")), None)
                    if not real_xml_name:
                        raise ValueError("No se encontró ningún archivo XML dentro del ZIP descargado.")
                    
                    z.extract(real_xml_name, path=self.raw_dir)
                    # Renombrar si es necesario para mantener consistencia
                    if real_xml_name != xml_name:
                        os.rename(self.raw_dir / real_xml_name, extracted_xml)
                
                # Eliminar el archivo ZIP para ahorrar espacio (el XML es suficiente y evita superar 1GB de descargas)
                os.remove(cache_zip)
            except Exception as e:
                logger.error(f"Error al extraer archivo XML del ZIP: {e}")
                if cache_zip.exists():
                    os.remove(cache_zip)
                raise e

        # 3. Parsear el XML
        if extracted_xml.exists():
            logger.info(f"Parseando archivo XML de la cartera: {extracted_xml}")
            try:
                with open(extracted_xml, "r", encoding="utf-8", errors="ignore") as f:
                    xml_content = f.read()
                
                # Comprobación de integridad del XML
                if "<constitucion_cartera_desagregada" not in xml_content:
                    raise ValueError("El XML no tiene la estructura correcta de constitucion_cartera_desagregada.")

                return self._parse_portfolio_xml(xml_content, period_str)
            except Exception as e:
                logger.error(f"Error al parsear el XML de la cartera {extracted_xml}: {e}")
                raise e
        else:
            logger.warning(f"No se encontró el XML extraído de la cartera para {period_str}")
            return []

    def _parse_portfolio_xml(self, xml_content: str, period_fallback: str) -> list[dict]:
        """Parsea la distribución de activos desde el XML desagregado mensual (Listado 1)."""
        root = ET.fromstring(xml_content.encode("utf-8"))
        
        # Manejo de namespaces de XML de la SP
        ns = {}
        if '}' in root.tag:
            ns['sp'] = root.tag.split('}')[0].strip('{')

        def find_tag(element, tag_name):
            if 'sp' in ns:
                return element.find(f".//sp:{tag_name}", namespaces=ns)
            return element.find(f".//{tag_name}")
            
        def find_all_tags(element, tag_name):
            if 'sp' in ns:
                return element.findall(f".//sp:{tag_name}", namespaces=ns)
            return element.findall(f".//{tag_name}")

        # Intentar extraer el período real del encabezado
        period_val = f"{period_fallback[:4]}-{period_fallback[4:]}"
        encabezado = find_tag(root, "encabezado")
        if encabezado is not None:
            periodo_el = find_tag(encabezado, "periodo")
            if periodo_el is not None and periodo_el.text:
                period_val = periodo_el.text.strip()

        # Obtener el listado número 1 (Diversificación de Activos)
        listados = find_all_tags(root, "listado")
        listado_1 = next((l for l in listados if l.get("numero") == "1"), None)
        
        if listado_1 is None:
            logger.warning("No se encontró el 'listado numero=1' en el XML de la cartera. Retornando vacío.")
            return []

        records = []
        tipofondos = find_all_tags(listado_1, "tipofondo")
        
        for tf in tipofondos:
            fund_type = tf.get("codigo", "A").upper()
            filas = find_all_tags(tf, "fila")
            
            for fila in filas:
                glosa_el = find_tag(fila, "glosa")
                if glosa_el is None or not glosa_el.text:
                    continue
                glosa = glosa_el.text.strip()
                
                columnas = find_tag(fila, "columnas")
                if columnas is None:
                    continue

                # Procesar AFPs individuales
                afps = find_all_tags(columnas, "afp")
                for afp in afps:
                    afp_name_el = find_tag(afp, "nombre")
                    if afp_name_el is None or not afp_name_el.text:
                        continue
                    afp_name = afp_name_el.text.upper().strip()
                    
                    pct_el = find_tag(afp, "porcentaje")
                    porcentaje = self._parse_amount(pct_el.text) if pct_el is not None else None
                    
                    records.append({
                        "period": period_val,
                        "afp_name": afp_name,
                        "fund_type": fund_type,
                        "instrument_glosa": glosa,
                        "monto_pesos": None,
                        "monto_dolares": None,
                        "porcentaje": porcentaje
                    })

                # Procesar Total Consolidado de la fila
                total = find_tag(columnas, "total")
                if total is not None:
                    monto_pesos_el = find_tag(total, "monto_pesos")
                    monto_dolares_el = find_tag(total, "monto_dolares")
                    pct_el = find_tag(total, "porcentaje")
                    
                    monto_pesos = self._parse_amount(monto_pesos_el.text) if monto_pesos_el is not None else None
                    monto_dolares = self._parse_amount(monto_dolares_el.text) if monto_dolares_el is not None else None
                    porcentaje = self._parse_amount(pct_el.text) if pct_el is not None else None

                    records.append({
                        "period": period_val,
                        "afp_name": "TOTAL",
                        "fund_type": fund_type,
                        "instrument_glosa": glosa,
                        "monto_pesos": monto_pesos,
                        "monto_dolares": monto_dolares,
                        "porcentaje": porcentaje
                    })

        logger.info(f"Parseados {len(records)} registros de cartera para el período {period_val}")
        return records

    # ============================================================
    # 3. Cinta de Precios Diaria
    # ============================================================

    def fetch_daily_prices(self, date_val: str) -> list[dict]:
        """
        Descarga y parsea la cinta de precios diaria para la fecha YYYY-MM-DD.
        Devuelve el listado de precios de cierre de instrumentos.
        """
        # Validar y formatear fecha
        try:
            dt = datetime.strptime(date_val, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Fecha inválida: {date_val}. Formato requerido: YYYY-MM-DD")

        year = dt.strftime("%Y")
        month_num = int(dt.strftime("%m"))
        day = dt.strftime("%d")
        yyyymmdd = f"{year}{dt.strftime('%m')}{day}"

        months_abbr = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
        month_abbr = months_abbr[month_num - 1]

        namefile = f"{year}/{month_abbr}/p{yyyymmdd}.zip"
        
        # Ruta del ZIP en caché local
        cache_zip = self.raw_dir / f"precios_{yyyymmdd}.zip"
        txt_name = f"p{yyyymmdd}.txt"
        extracted_txt = self.raw_dir / txt_name

        # 1. Descarga del ZIP si no está en caché ni extraído
        if not cache_zip.exists() and not extracted_txt.exists():
            url = f"{self.BASE_URL}/apps/GetFile.php"
            params = {"id": "006", "namefile": namefile}
            req_headers = {
                **self.headers,
                "Referer": f"{self.BASE_URL}/apps/preciosIF.php"
            }

            logger.info(f"Descargando cinta de precios diaria para {date_val}...")
            try:
                r = httpx.get(url, params=params, headers=req_headers, timeout=30)
                if r.status_code != 200:
                    # En días no hábiles la SP no publica cinta de precios
                    logger.warning(f"No hay cinta de precios disponible para la fecha {date_val} (HTTP {r.status_code}).")
                    return []
                
                # Comprobación de integridad mínima
                if len(r.content) < 100:
                    logger.warning(f"Cinta de precios para {date_val} vacía o corrupta. Ignorando.")
                    return []

                with open(cache_zip, "wb") as f:
                    f.write(r.content)
                logger.info(f"Cinta de precios diaria guardada en: {cache_zip}")

            except Exception as e:
                logger.error(f"Error descargando precios diarios para {date_val}: {e}")
                raise e

        # 2. Descomprimir el archivo plano .txt
        if not extracted_txt.exists() and cache_zip.exists():
            logger.info(f"Extrayendo {txt_name} desde el archivo ZIP...")
            try:
                with zipfile.ZipFile(cache_zip, "r") as z:
                    real_txt_name = next((name for name in z.namelist() if name.endswith(".txt")), None)
                    if not real_txt_name:
                        raise ValueError("No se encontró ningún archivo plano .txt dentro del ZIP de precios.")
                    
                    z.extract(real_txt_name, path=self.raw_dir)
                    if real_txt_name != txt_name:
                        os.rename(self.raw_dir / real_txt_name, extracted_txt)
                
                # Eliminar el ZIP para mantener el peso bajo en disco
                os.remove(cache_zip)
            except Exception as e:
                logger.error(f"Error al descomprimir cinta de precios: {e}")
                if cache_zip.exists():
                    os.remove(cache_zip)
                raise e

        # 3. Parsear el archivo de texto
        if extracted_txt.exists():
            logger.info(f"Parseando cinta de precios: {extracted_txt}")
            try:
                records = []
                with open(extracted_txt, "r", encoding="latin-1", errors="ignore") as f:
                    lines = f.readlines()
                
                for idx, line in enumerate(lines):
                    line_str = line.strip()
                    if not line_str:
                        continue
                    
                    parts = line_str.split(";")
                    if len(parts) < 4:
                        continue

                    instrument_id = parts[0].strip()
                    instrument_type = parts[1].strip()
                    currency = parts[2].strip()
                    raw_price = parts[3].strip()

                    price = self._parse_amount(raw_price)
                    if instrument_id and price is not None:
                        records.append({
                            "date": date_val,
                            "instrument_id": instrument_id,
                            "instrument_type": instrument_type,
                            "currency": currency,
                            "price": price
                        })
                
                logger.info(f"Parseados {len(records)} instrumentos financieros de la cinta de precios de {date_val}")
                return records

            except Exception as e:
                logger.error(f"Error parseando archivo de precios {extracted_txt}: {e}")
                raise e
        else:
            return []

    # ============================================================
    # Auxiliares / Helpers de Limpieza
    # ============================================================

    def _parse_amount(self, val) -> Optional[float]:
        """Parsea saldos en string (formato chileno con comas decimales) a float."""
        if val is None:
            return None
        val_str = str(val).strip().replace(" ", "")
        if val_str in ("", "-", "NaN", "N/A", "nd", "ND"):
            return None
        
        # Eliminar puntos de miles y reemplazar comas decimales por puntos
        if "," in val_str and "." in val_str:
            if val_str.index(".") < val_str.index(","):
                val_clean = val_str.replace(".", "").replace(",", ".")
            else:
                val_clean = val_str.replace(",", "")
        elif "," in val_str:
            val_clean = val_str.replace(",", ".")
        else:
            val_clean = val_str
            
        try:
            return float(val_clean)
        except ValueError:
            return None

    def _parse_sp_date_to_yyyymmdd(self, date_text: str) -> str:
        """Convierte fechas del formato SP (Ej. '31-MAY-2026') a '20260531'."""
        parts = date_text.strip().split("-")
        if len(parts) != 3:
            raise ValueError(f"Formato de fecha de la SP no compatible: {date_text}")
        
        day = parts[0].zfill(2)
        month_abbr = parts[1].upper()
        year = parts[2]
        
        months_map = {
            "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04", "MAY": "05", "JUN": "06",
            "JUL": "07", "AGO": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12"
        }
        month = months_map.get(month_abbr)
        if not month:
            # Intentar inglés por si acaso
            eng_map = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
                       "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}
            month = eng_map.get(month_abbr)
            
        if not month:
            raise ValueError(f"Mes abreviado de la SP no reconocido: {month_abbr}")
            
        return f"{year}{month}{day}"
