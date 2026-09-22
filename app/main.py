"""
FastAPI application entrypoint.

Replaces the old Streamlit `app.py` and CLI `main.py`: the extraction +
comparison pipeline (extractor.py / matcher_comparator.py / pdf_utils.py /
report.py / usage_tracker.py) is unchanged in behaviour and now lives under
app/services/, invoked through the HTTP API defined in app/routers/.

Run locally with:
    uvicorn app.main:app --reload
or:
    python startup.py
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.db.base import Base
from app.db.session import engine
from app.middleware.cors import add_cors_middleware
from app.middleware.request_logging import RequestLoggingMiddleware
from app.routers import comparisons, drawings, dxf, frontend, health, projects

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="Checks whether sub-drawings are consistent with their main assembly drawing (GAD).",
        version="1.0.0",
    )

    add_cors_middleware(app)
    app.add_middleware(RequestLoggingMiddleware)

    app.mount("/static", StaticFiles(directory="static"), name="static")
    # Serves generated DXF overlay PNGs (and, incidentally, uploaded PDFs) by
    # the relative path persistence_service stores in OVERLAY.<n>.PATH rows.
    settings.upload_dir_path.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(settings.upload_dir_path)), name="uploads")

    app.include_router(health.router, prefix=settings.API_V1_PREFIX)
    app.include_router(comparisons.router, prefix=settings.API_V1_PREFIX)
    app.include_router(drawings.router, prefix=settings.API_V1_PREFIX)
    app.include_router(dxf.router, prefix=settings.API_V1_PREFIX)
    app.include_router(projects.router, prefix=settings.API_V1_PREFIX)
    app.include_router(frontend.router)

    @app.on_event("startup")
    def on_startup() -> None:
        settings.upload_dir_path  # ensure the upload dir exists
        if settings.AUTO_CREATE_TABLES:
            # Convenience for local/dev use. In production, prefer running
            # `alembic upgrade head` as part of your deploy and leaving this off.
            Base.metadata.create_all(bind=engine)
            logger.info("AUTO_CREATE_TABLES=true -> ensured tables exist via metadata.create_all()")
        logger.info("%s started", settings.PROJECT_NAME)

    return app


app = create_app()
