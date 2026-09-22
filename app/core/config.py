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

    # -- Supabase Storage (DXF overlay PNGs only) ----------------------------
    # Optional: only the generated overlay/highlight images are uploaded here
    # (not the uploaded PDFs/DXFs) so they survive a host with no persistent
    # disk (e.g. Render's free tier, whose local filesystem resets on every
    # restart/spin-down). Uses the service_role key (server-side, bypasses
    # RLS) -- never the anon key. If left blank, the app falls back to
    # serving overlays from local disk exactly as before.
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "dxf-overlays"

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
