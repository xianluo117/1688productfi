from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://product_manager:product_manager@db:5432/product_manager"
    admin_username: str = "admin"
    admin_password: str
    session_secret: str
    ingest_api_token: str
    allowed_origins: str = "https://s.1688.com"
    cookie_secure: bool = False
    image_storage_path: Path = Path("/data/images")
    session_max_age: int = 7 * 24 * 60 * 60
    max_ingest_items: int = 200
    max_image_bytes: int = 8 * 1024 * 1024

    @field_validator("admin_password", "session_secret", "ingest_api_token")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("secret values must contain at least 12 characters")
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
