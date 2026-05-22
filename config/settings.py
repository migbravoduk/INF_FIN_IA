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
