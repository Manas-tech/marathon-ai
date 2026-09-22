"""
Uploads DXF overlay PNGs to Supabase Storage so they survive a host with no
persistent disk (Render's free tier resets local files on every restart/
spin-down). Scoped to overlay/highlight images only -- uploaded PDFs/DXFs
stay on local disk, this module does not touch them.

If SUPABASE_URL / SUPABASE_SERVICE_KEY aren't configured, every function
here is a no-op that returns None, and callers fall back to serving the
local file exactly as before.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def enabled() -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY)


def upload_png(local_path: Path, remote_path: str) -> str | None:
    """Uploads local_path to <bucket>/remote_path in Supabase Storage.
    Returns the object's public URL, or None if Storage isn't configured or
    the upload failed (caller should fall back to the local file in that
    case, not raise -- a failed image upload shouldn't fail the whole
    validate/compare request)."""
    if not enabled():
        return None
    base = settings.SUPABASE_URL.rstrip("/")
    bucket = settings.SUPABASE_STORAGE_BUCKET
    try:
        resp = requests.put(
            f"{base}/storage/v1/object/{bucket}/{remote_path}",
            headers={
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
                "Content-Type": "image/png",
                "x-upsert": "true",
            },
            data=local_path.read_bytes(),
            timeout=30,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("Supabase Storage upload failed for %s", remote_path)
        return None
    return f"{base}/storage/v1/object/public/{bucket}/{remote_path}"


def resolve_image_url(stored: str) -> str:
    """A stored image reference is either a Supabase Storage URL (uploaded
    there) or a path relative to UPLOAD_DIR (local fallback) -- resolve
    either into something the frontend can load directly."""
    if stored.startswith(("http://", "https://")):
        return stored
    return f"/uploads/{stored}"
