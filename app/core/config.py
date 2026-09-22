"""
Central application settings.

Everything the app used to read straight out of `os.environ` in the old
Streamlit/CLI scripts (app.py, main.py, extractor.py, matcher_comparator.py,
usage_tracker.py) is now declared here once, so every router/service pulls
config from the same place instead of calling os.environ.get() ad hoc.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # -- Gemini -------------------------------------------------------------
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_COMPLEX_MODEL: str = "gemini-3.5-flash"
    GEMINI_MAX_OUTPUT_TOKENS: int = 65536
    RENDER_DPI: int = 300
    POPPLER_PATH: str | None = None

    INPUT_COST_PER_MTOK: float = 0.75
    OUTPUT_COST_PER_MTOK: float = 4.50

    # -- App / API ------------------------------------------------------------
    PROJECT_NAME: str = "Drawing Validator API"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "sqlite:///./drawing_validator.db"
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_MB: int = 20
    CORS_ORIGINS: str = "*"
    AUTO_CREATE_TABLES: bool = True

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def upload_dir_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    """Settings are read once and cached; use `get_settings()` everywhere
    instead of instantiating `Settings()` directly, so tests can override
    it via dependency overrides if needed."""
    return Settings()
