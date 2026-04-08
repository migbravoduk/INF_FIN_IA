"""
collectors/bcentral.py — Cliente para la BDE API del Banco Central de Chile

Documentación oficial:
https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/index.htm

Métodos disponibles:
  - GetSeries(timeseries, firstdate, lastdate) → datos de una serie
  - SearchSeries(term)                         → buscar series por nombre

Uso:
    from collectors.bcentral import BCentralCollector
    client = BCentralCollector()
    data = client.fetch_series('F073.IPC.IND.N.DIC.Z.Z.2023100',
                               from_date='2020-01-01', to_date='2024-12-31')
"""

import logging
from datetime import date, timedelta
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"


class BCentralCollector:
    """
    Cliente para la API REST BDE del Banco Central de Chile.
    Requiere credenciales: BCENTRAL_USER y BCENTRAL_PASS en .env
    """

    def __init__(self):
        self.user = settings.BCENTRAL_USER
        self.password = settings.BCENTRAL_PASS
        if not settings.has_bcentral_credentials:
            logger.warning(
                "Credenciales del Banco Central no configuradas. "
                "Define BCENTRAL_USER y BCENTRAL_PASS en tu .env"
            )

    def _build_params(self, function: str, **kwargs) -> dict:
        params = {
            "user": self.user,
            "pass": self.password,
            "function": function,
            **kwargs,
        }
        return params

    def fetch_series(
        self,
        series_id: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[dict]:
        """
        Descarga datos de una serie de tiempo del BCCh.

        Args:
            series_id: Código de la serie (ej. 'F073.IPC.IND.N.DIC.Z.Z.2023100')
            from_date: Fecha inicio 'YYYY-MM-DD' (default: hace 5 años)
            to_date:   Fecha fin 'YYYY-MM-DD' (default: hoy)

        Returns:
            Lista de dicts {'date': str, 'value': float}
        """
        if not settings.has_bcentral_credentials:
            raise ValueError(
                "Credenciales del Banco Central no configuradas. "
                "Completa BCENTRAL_USER y BCENTRAL_PASS en tu .env"
            )

        today = date.today()
        if from_date is None:
            from_date = str(today.replace(year=today.year - 5))
        if to_date is None:
            to_date = str(today)

        params = self._build_params(
            function="GetSeries",
            timeseries=series_id,
            firstdate=from_date,
            lastdate=to_date,
        )

        logger.info(f"Fetching BCCh serie={series_id} from={from_date} to={to_date}")

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()
                
                # Si arroja un internal error (-50) provocado usualmente por rangos de fechas
                # incompatibles (ej. serie nueva pero pidiendo 5 años atrás), reintentamos sin fechas
                if data.get("Codigo") == -50 and (from_date or to_date):
                    logger.warning(f"[{series_id}] Error -50 (fechas inválidas). Reintentando todo el historial...")
                    fallback_params = self._build_params(function="GetSeries", timeseries=series_id)
                    fb_response = client.get(BASE_URL, params=fallback_params)
                    fb_response.raise_for_status()
                    data = fb_response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching {series_id}: {e.response.status_code}")
            raise
        except object as e:
            logger.error(f"Error inesperado fetching {series_id}: {e}")
            raise

        return self._parse_response(data, series_id)

    def _parse_response(self, data: dict, series_id: str) -> list[dict]:
        """
        Parsea la respuesta JSON de la BDE API.
        """
        # Verificar si la API devolvio un error (Codigo != 0)
        codigo = data.get("Codigo", 0)
        if codigo != 0:
            msg = data.get("Descripcion", "Error desconocido")
            logger.warning(f"[{series_id}] Error desde BDE API: {msg} (Codigo {codigo})")
            return []

        records = []

        try:
            series_data = data.get("Series", {})
            obs_raw = series_data.get("Obs")
            
            if not obs_raw:
                logger.warning(f"[{series_id}] Sin datos en la respuesta.")
                return []

            if isinstance(obs_raw, dict):
                # Si solo hay una observación, la API la devuelve como dict, no lista
                obs_raw = [obs_raw]

        except (KeyError, TypeError) as e:
            logger.warning(f"Estructura inesperada en respuesta para {series_id}: {e}")
            logger.debug(f"Raw response: {data}")
            return []

        for obs in obs_raw:
            status_code = obs.get("statusCode", "").upper()
            if status_code not in ("OK", ""):
                continue  # saltar observaciones con errores

            raw_date = obs.get("indexDateString", "")
            raw_value = obs.get("value", "")

            parsed_date = self._parse_date(raw_date)
            parsed_value = self._parse_value(raw_value)

            if parsed_date and parsed_value is not None:
                records.append({"date": parsed_date, "value": parsed_value})

        logger.info(f"Parseadas {len(records)} observaciones para {series_id}")
        return records

    @staticmethod
    def _parse_date(raw: str) -> Optional[str]:
        """
        Convierte fechas del formato BCCh a ISO 8601.
        Ejemplos: '2024-01' → '2024-01-01', '2024-01-15' → '2024-01-15'
        """
        if not raw:
            return None
        raw = raw.strip()
        parts = raw.split("-")
        if len(parts) == 2:
            return f"{parts[0]}-{parts[1].zfill(2)}-01"
        elif len(parts) == 3:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        return None

    @staticmethod
    def _parse_value(raw: str) -> Optional[float]:
        """Convierte el valor string a float, manejando puntos de miles y comas decimales."""
        if not raw or raw.strip() in ("", "NaN", "N/A", "-"):
            return None
        cleaned = raw.strip().replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def search_series(self, term: str) -> list[dict]:
        """
        Busca series disponibles en la BDE por término.

        Args:
            term: Texto a buscar (ej. 'IPC', 'PIB', 'dolar')

        Returns:
            Lista de dicts con información de las series encontradas
        """
        if not settings.has_bcentral_credentials:
            raise ValueError("Credenciales del Banco Central no configuradas.")

        params = self._build_params(function="SearchSeries", term=term)

        with httpx.Client(timeout=20.0) as client:
            response = client.get(BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        results = []
        series_infos = data.get("SeriesInfos") or []
        
        # BDE SieteRestWS sometimes returns a list directly or a dict with SeriesInfo when multiple
        if isinstance(series_infos, dict):
            series_list = series_infos.get("SeriesInfo", [])
        else:
            series_list = series_infos

        for item in series_list:
            if not isinstance(item, dict):
                continue
            results.append({
                "id": item.get("seriesId"),
                "name": item.get("frequencyCode"),
                "description": item.get("englishTitle") or item.get("spanishTitle"),
            })
        return results
