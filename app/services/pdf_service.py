"""
Render PDF pages to PNG images for vision extraction.

Rendering (rather than extracting text) is the whole point: title blocks
and dimension strings on mechanical drawings are frequently rotated 90/180/
270 degrees on the sheet. Text-layer extraction reads glyphs in stream
order and returns those strings scrambled or reversed. An image is read
the way a person reads it, rotation and all.

Ported from the original pdf_utils.py with no behavioural changes.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import List

from pdf2image import convert_from_path

from app.core.config import get_settings

settings = get_settings()


def render_pdf_to_images(
    pdf_path: str, dpi: int | None = None, out_dir: str | None = None, poppler_path: str | None = None
) -> List[str]:
    """Render every page of pdf_path to a PNG. Returns list of image file paths, in page order.

    poppler_path: on Windows, if poppler's bin folder isn't on PATH, pass it explicitly
    here (or set the POPPLER_PATH env var) e.g. r"C:\\poppler\\Library\\bin".
    """
    pdf_path = str(pdf_path)
    dpi = dpi or settings.RENDER_DPI
    stem = Path(pdf_path).stem
    out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(pdf_path)) or ".", "_rendered")
    os.makedirs(out_dir, exist_ok=True)

    poppler_path = poppler_path or settings.POPPLER_PATH or None
    pages = convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path)
    paths = []
    for i, page in enumerate(pages, start=1):
        out_path = os.path.join(out_dir, f"{stem}_p{i}.png")
        page.save(out_path, "PNG")
        paths.append(out_path)
    return paths
