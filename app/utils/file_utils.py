"""Helpers for accepting uploaded PDFs safely."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings

settings = get_settings()

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def safe_filename(name: str) -> str:
    """Strip path components and unsafe characters from a client-supplied filename."""
    name = Path(name or "upload.pdf").name
    name = _SAFE_NAME_RE.sub("_", name)
    return name or "upload.pdf"


def validate_pdf_upload(upload: UploadFile) -> None:
    if not upload.filename or not upload.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{upload.filename}' is not a PDF file.",
        )


def validate_dxf_upload(upload: UploadFile) -> None:
    if not upload.filename or not upload.filename.lower().endswith(".dxf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{upload.filename}' is not a DXF file.",
        )


async def save_upload(upload: UploadFile, dest_dir: Path, validator=validate_pdf_upload) -> Path:
    """Stream an UploadFile to disk, enforcing MAX_FILE_MB, and return the saved path."""
    validator(upload)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / safe_filename(upload.filename)

    max_bytes = settings.MAX_FILE_MB * 1024 * 1024
    size = 0
    with open(dest_path, "wb") as out_file:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out_file.close()
                dest_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"'{upload.filename}' exceeds the {settings.MAX_FILE_MB} MB limit.",
                )
            out_file.write(chunk)

    await upload.close()
    return dest_path


def new_job_work_dir() -> Path:
    work_dir = settings.upload_dir_path / uuid.uuid4().hex
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir
