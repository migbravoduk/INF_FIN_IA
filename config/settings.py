"""
config/settings.py — Configuración centralizada con pydantic-settings

Lee variables de entorno desde .env automáticamente.
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Banco Central de Chile — BDE API
    # Registro en: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/index.htm
    # IMPORTANTE: después de registrarse, iniciar sesión en la página de API para activar
    BCENTRAL_USER: str = ""
    BCENTRAL_PASS: str = ""

    # CMF — sin API pública, acceso por scraping del portal web cmfchile.cl
    # Esta variable queda como placeholder para futuras fases
    CMF_API_KEY: str = ""

    # Base de datos
    DB_PATH: str = "data/finanzas_chile.duckdb"

    # Scheduler
    DAILY_FETCH_TIME: str = "08:00"
    TIMEZONE: str = "America/Santiago"

    # Si True, la app FastAPI arranca el scheduler embebido (BackgroundScheduler) en su
    # propio proceso y la API abre la BD en lectura/escritura (DuckDB no permite lector +
    # escritor en procesos distintos, así que el modo embebido evita ese conflicto).
    RUN_SCHEDULER_IN_APP: bool = False

    # CMF Bancos — apikey pública de la API SBIFv3 (registro gratuito en api.sbif.cl).
    # Override opcional vía .env; el valor por defecto es la clave pública ya usada.
    CMF_SBIF_APIKEY: str = "3a440ec14ceec35463beaf361631829c0ed9dc8d"

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/app.log"

    # Catalog
    CATALOG_PATH: str = "config/series_catalog.yaml"

    @property
    def has_bcentral_credentials(self) -> bool:
        return bool(self.BCENTRAL_USER and self.BCENTRAL_PASS)

    @property
    def cmf_scraping_enabled(self) -> bool:
        """CMF se accede por scraping — siempre disponible, no requiere clave."""
        return True


settings = Settings()
