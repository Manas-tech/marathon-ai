#!/usr/bin/env python3
"""
Generalized Engineering Job Card Parameter Extractor
======================================================

Extracts structured parameters (label/value/unit/category) from any
engineering "job card" PDF (a manufacturing work-order derived from a
drawing) using a HYBRID approach:

    1. pdfplumber pulls raw text + table structure directly from the PDF
       (cheap, deterministic, no model needed, and preserves numbers
       exactly as printed instead of relying on OCR/vision guesses).
    2. pdfplumber also renders each page to an image, which is sent to
       Gemini ALONGSIDE the extracted text. Job cards are dense grid
       layouts where a label and its value are visually adjacent but far
       apart in raw text order — giving Gemini the image lets it use
       layout/position to disambiguate, while the text gives it exact
       character-accurate values. Neither text-only nor vision-only is
       as reliable alone for this document type.
    3. Gemini is used ONLY for the reasoning step of turning a messy
       tabular layout into structured key/value pairs, using a
       JSON-schema-constrained response (Gemini's native structured
       output) rather than free-text parsing. Nothing is hardcoded to
       any specific job-card template, field name, or industry
       (heater/element cards, machining cards, PCB cards, etc. all work
       the same way) -- the model is asked to find whatever
       label/value pairs exist on the page.

Usage
-----
    export GEMINI_API_KEY="your-key-here"
    python extract_jobcard.py path/to/jobcard.pdf
    python extract_jobcard.py path/to/jobcard.pdf --model gemini-2.5-flash
    python extract_jobcard.py path/to/jobcard.pdf --out results/

Outputs
-------
    <name>_parameters.json   -- full structured extraction
    <name>_parameters.csv    -- flat, spreadsheet-friendly table

Install
-------
    pip install google-genai pdfplumber pillow --break-system-packages
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import pdfplumber

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in the current working directory (if present)
except ImportError:
    pass  # dotenv is optional; env vars set via `export` still work fine

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None


# --------------------------------------------------------------------------
# 1. Generalized extraction schema
#    (No job-specific field names. This is the SAME schema for a heater
#     job card, a machining traveler, a PCB fab card, etc.)
# --------------------------------------------------------------------------

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_meta": {
            "type": "object",
            "description": "High-level identifiers for the job card / work order.",
            "properties": {
                "work_order_or_drawing_number": {"type": "string"},
                "revision": {"type": "string"},
                "date": {"type": "string"},
                "customer_or_client": {"type": "string"},
                "prepared_by": {"type": "string"},
                "checked_by": {"type": "string"},
            },
        },
        "parameters": {
            "type": "array",
            "description": (
                "Every discrete labeled parameter found anywhere on the "
                "job card (dimensions, electrical values, material specs, "
                "tolerances, quantities, process settings, etc.)."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "parameter": {
                        "type": "string",
                        "description": "The label/name exactly as printed (e.g. 'Hot Ohms', 'Volts', 'Sheath Length OAL').",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value exactly as printed, including fractions/tolerances (e.g. '76.17', '460 +5%/-5%').",
                    },
                    "unit": {
                        "type": "string",
                        "description": "Unit if present (mm, inch, ohms, watts, volts, amps, %, etc.); empty string if none.",
                    },
                    "category": {
                        "type": "string",
                        "description": (
                            "Best-fit grouping, e.g. 'Electrical', 'Dimensional', "
                            "'Material', 'Identification', 'Process/Manufacturing', "
                            "'Tolerance', 'Quality/Inspection', 'Other'."
                        ),
                    },
                    "table_or_section": {
                        "type": "string",
                        "description": "Which section/table on the card this came from, if identifiable.",
                    },
                },
                "required": ["parameter", "value"],
            },
        },
        "notes_and_flags": {
            "type": "array",
            "description": "Any printed notes, warnings, or special instructions (e.g. 'Do not assemble w/o drawing').",
            "items": {"type": "string"},
        },
    },
    "required": ["document_meta", "parameters"],
}


@dataclass
class Parameter:
    parameter: str
    value: str
    unit: str = ""
    category: str = ""
    table_or_section: str = ""


# --------------------------------------------------------------------------
# 2. PDF -> text + image (pdfplumber does the deterministic heavy lifting)
# --------------------------------------------------------------------------

def extract_pdf_content(pdf_path: Path, dpi: int = 220) -> list[dict[str, Any]]:
    """Returns a list of {page_number, text, table_text, image_bytes} per page."""
    pages_content = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            # Try structured table extraction too -- job cards are often
            # grids that pdfplumber can recover as real tables, which is
            # more reliable than raw text flow for row/column alignment.
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

            # Render page image so Gemini can use visual/spatial layout
            img = page.to_image(resolution=dpi)
            img_path = pdf_path.parent / f".__page_{i}.png"
            img.save(img_path)
            image_bytes = img_path.read_bytes()
            img_path.unlink(missing_ok=True)

            pages_content.append(
                {
                    "page_number": i,
                    "text": text,
                    "table_text": table_text,
                    "image_bytes": image_bytes,
                }
            )
    return pages_content


# --------------------------------------------------------------------------
# 3. Gemini structured extraction (model does ONLY the reasoning/mapping)
# --------------------------------------------------------------------------

def build_prompt(page_number: int, text: str, table_text: str) -> str:
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
and units as printed) -- do not round or reformat numbers.

If a field is clearly a duplicate/cross-check of another (e.g. the same
value printed twice in different sections), still include both, but note
the section in `table_or_section` so duplicates are distinguishable.

--- RAW TEXT ---
{text}

--- DETECTED TABLES ---
{table_text if table_text.strip() else "(none detected)"}
--- END ---
"""


def extract_with_gemini(
    client: "genai.Client",
    model: str,
    pages_content: list[dict[str, Any]],
) -> dict[str, Any]:
    """Runs one Gemini call per page (keeps context small/accurate) and merges."""
    merged: dict[str, Any] = {
        "document_meta": {},
        "parameters": [],
        "notes_and_flags": [],
    }

    for page in pages_content:
        prompt = build_prompt(page["page_number"], page["text"], page["table_text"])

        image_part = types.Part.from_bytes(
            data=page["image_bytes"], mime_type="image/png"
        )

        response = client.models.generate_content(
            model=model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=EXTRACTION_SCHEMA,
                temperature=0,  # deterministic extraction, not creative
            ),
        )

        try:
            page_result = json.loads(response.text)
        except (json.JSONDecodeError, AttributeError) as e:
            print(f"  [warn] page {page['page_number']}: could not parse model "
                  f"output as JSON ({e}); skipping page.", file=sys.stderr)
            continue

        # Merge: first non-empty meta wins per field; parameters/notes append.
        for k, v in page_result.get("document_meta", {}).items():
            if v and not merged["document_meta"].get(k):
                merged["document_meta"][k] = v

        merged["parameters"].extend(page_result.get("parameters", []))
        merged["notes_and_flags"].extend(page_result.get("notes_and_flags", []))

    return merged


# --------------------------------------------------------------------------
# 4. Output writers
# --------------------------------------------------------------------------

def write_outputs(result: dict[str, Any], stem: str, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{stem}_parameters.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    csv_path = out_dir / f"{stem}_parameters.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Parameter", "Value", "Unit", "Category", "Section"])
        for p in result.get("parameters", []):
            writer.writerow(
                [
                    p.get("parameter", ""),
                    p.get("value", ""),
                    p.get("unit", ""),
                    p.get("category", ""),
                    p.get("table_or_section", ""),
                ]
            )

    return json_path, csv_path


# --------------------------------------------------------------------------
# 5. CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path, help="Path to the job card PDF")
    parser.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        help="Gemini model name (default: gemini-3.1-flash-lite, or $GEMINI_MODEL if set)",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("."), help="Output directory (default: .)"
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GEMINI_API_KEY"),
        help="Gemini API key (default: $GEMINI_API_KEY env var)",
    )
    args = parser.parse_args()

    if genai is None:
        sys.exit(
            "google-genai is not installed.\n"
            "Install with: pip install google-genai --break-system-packages"
        )
    if not args.api_key:
        sys.exit("No API key found. Set GEMINI_API_KEY or pass --api-key.")
    if not args.pdf_path.exists():
        sys.exit(f"File not found: {args.pdf_path}")

    print(f"[1/3] Extracting text/tables/images from {args.pdf_path.name} ...")
    pages_content = extract_pdf_content(args.pdf_path)

    print(f"[2/3] Sending {len(pages_content)} page(s) to Gemini ({args.model}) "
          f"for structured extraction ...")
    client = genai.Client(api_key=args.api_key)
    result = extract_with_gemini(client, args.model, pages_content)

    print(f"[3/3] Writing outputs to {args.out.resolve()} ...")
    stem = args.pdf_path.stem
    json_path, csv_path = write_outputs(result, stem, args.out)

    print(f"\nDone. Extracted {len(result['parameters'])} parameters.")
    print(f"  JSON: {json_path}")
    print(f"  CSV : {csv_path}")


if __name__ == "__main__":
    main()