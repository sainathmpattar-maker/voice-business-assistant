"""
config.py — centralised settings loaded from .env
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env", "../.env.local", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://polaris:polaris@localhost:5432/polaris"

    # External APIs
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # App
    merchant_id: int = 1
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    allowed_origins: str = ""
    log_level: str = "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = f"{self.cors_origins},{self.allowed_origins}"
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        # Always allow localhost variants for dev/testing
        for dev_url in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]:
            if dev_url not in origins:
                origins.append(dev_url)
        return list(dict.fromkeys(origins))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

