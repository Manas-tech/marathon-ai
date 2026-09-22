#!/usr/bin/env python3
"""
Convenience launcher so the app can be started as `python startup.py`
(also the entry point most PaaS platforms, e.g. Azure App Service, expect
to find at the project root).

For local development, `uvicorn app.main:app --reload` works too and gives
you auto-reload on file changes, which this script does not.
"""
from __future__ import annotations

import socket

import uvicorn

from app.core.config import get_settings


def _port_in_use(port: int) -> bool:
    # Under `streamlit run startup.py` this script is re-executed on every
    # session/rerun; skip launching a second server if one is already bound.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    settings = get_settings()
    if _port_in_use(settings.APP_PORT):
        print(f"Port {settings.APP_PORT} already in use; server already running.")
        raise SystemExit(0)
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=False,
    )
