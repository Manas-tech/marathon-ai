"""
Job-card extraction: PDF -> JobCardExtraction (structured JSON).

Ported from the standalone extract_jobcard.py script into the app's service
conventions (config from app.core.config, usage tracked via UsageTracker,
returns a validated JobCardExtraction instead of a raw dict). The HYBRID
approach itself is unchanged:

    1. pdfplumber pulls raw text + table structure directly from the PDF
       (cheap, deterministic, preserves numbers exactly as printed).
    2. Each page is also rendered to an image and sent to Gemini alongside
       that text -- job cards are dense grids where a label and its value
       are visually adjacent but far apart in raw text order, so the image
       lets the model use layout/position to disambiguate.
    3. Gemini does only the structuring step, constrained to
       response_schema=JobCardExtraction, one call per page (kept small/
       accurate), merged afterwards -- same as extraction_service.py's
       one-call-per-page GAD path.

Unlike drawing extraction, this doesn't go through pdf_service's poppler
rendering -- pdfplumber's own page.to_image() is used instead, since it also
gives us table_text detection from the same library in one pass.
"""
from __future__ import annotations

import io
from typing import Any

import pdfplumber
from google import genai
from google.genai import types

from app.core.config import get_settings
from app.schemas.jobcard import JobCardDocumentMeta, JobCardExtraction
from app.services.usage_service import UsageTracker

settings = get_settings()


def _client() -> genai.Client:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key "
            "(https://aistudio.google.com/apikey)."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _extract_pdf_content(pdf_path: str, dpi: int = 220) -> list[dict[str, Any]]:
    """Returns a list of {page_number, text, table_text, image_bytes} per page."""
    pages_content: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            table_text = ""
            try:
                tables = page.extract_tables()
                for t_idx, table in enumerate(tables):
                    table_text += f"\n[Table {t_idx + 1}]\n"
                    for row in table:
                        clean_row = [c.strip() if c else "" for c in row]
                        table_text += " | ".join(clean_row) + "\n"
            except Exception:
                pass

            img = page.to_image(resolution=dpi)
            buf = io.BytesIO()
            img.original.save(buf, format="PNG")
            image_bytes = buf.getvalue()

            pages_content.append(
                {"page_number": i, "text": text, "table_text": table_text, "image_bytes": image_bytes}
            )
    return pages_content


def _build_prompt(page_number: int, text: str, table_text: str) -> str:
    return f"""You are extracting parameters from page {page_number} of an
ENGINEERING JOB CARD (a shop-floor work order/traveler generated from an
engineering drawing, e.g. for a manufactured part/assembly).

Below is the raw text pdfplumber extracted from this page, followed by any
tables it could detect. An image of the actual page is also attached --
use the image to resolve any label/value pairs that are ambiguous or
mis-ordered in the raw text (job cards are dense grids; text extraction
often separates a label from its value).

Extract EVERY discrete parameter you can find: identification fields
(work order/drawing number, revision, date, customer), electrical values,
dimensional values, material/spec callouts, tolerances, quantities,
process/manufacturing settings, and any notes. Do not skip repeated or
minor fields. Use the exact printed value (keep fractions, +/- tolerances,
and units as printed) -- do not round or reformat numbers. Do not invent
values that are not visibly written on the page (e.g. do not compute a
derived decimal that isn't printed).

If a field is clearly a duplicate/cross-check of another (e.g. the same
value printed twice in different sections), still include both, but note
the section in `table_or_section` so duplicates are distinguishable.

--- RAW TEXT ---
{text}

--- DETECTED TABLES ---
{table_text if table_text.strip() else "(none detected)"}
--- END ---
"""


def extract_job_card(
    pdf_path: str, source_label: str, model: str | None = None, usage_tracker: UsageTracker | None = None
) -> JobCardExtraction:
    """Runs one Gemini call per page and merges into a single JobCardExtraction."""
    model = model or settings.GEMINI_MODEL
    client = _client()

    pages_content = _extract_pdf_content(pdf_path, dpi=settings.RENDER_DPI)

    merged = JobCardExtraction(source_file=source_label)
    seen_meta: dict[str, str] = {}

    for page in pages_content:
        prompt = _build_prompt(page["page_number"], page["text"], page["table_text"])
        image_part = types.Part.from_bytes(data=page["image_bytes"], mime_type="image/png")

        call_label = f"{source_label} (p{page['page_number']}/{len(pages_content)})"
        response = client.models.generate_content(
            model=model,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt), image_part])],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=JobCardExtraction,
                max_output_tokens=settings.GEMINI_MAX_OUTPUT_TOKENS,
            ),
        )

        if usage_tracker is not None:
            usage_tracker.record(f"jobcard: {call_label}", getattr(response, "usage_metadata", None))

        page_result: JobCardExtraction | None = response.parsed
        if page_result is None:
            page_result = JobCardExtraction.model_validate_json(response.text)

        for field_name in JobCardDocumentMeta.model_fields:
            value = getattr(page_result.document_meta, field_name)
            if value and not seen_meta.get(field_name):
                seen_meta[field_name] = value

        merged.parameters.extend(page_result.parameters)
        merged.notes_and_flags.extend(page_result.notes_and_flags)

    merged.document_meta = JobCardDocumentMeta(**seen_meta)
    return merged
