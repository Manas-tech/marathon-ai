"""
Vision extraction: rendered drawing image(s) -> DrawingExtraction (structured JSON).

One Gemini call per drawing (all pages of that drawing in a single call, so
a multi-sheet sub-drawing still costs one call, not one per page).

Ported from the original extractor.py: same prompt, same call logic, just
reading config from app.core.config instead of os.environ directly and
importing the schema from its new location.
"""
from __future__ import annotations
from typing import List

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.schemas.drawing import DrawingExtraction
from app.services.usage_service import UsageTracker

settings = get_settings()

EXTRACTION_PROMPT = """\
You are reading a mechanical engineering drawing (image of a scanned/rendered
PDF sheet). Extract EVERY piece of labeled information on the sheet into the
given JSON schema. Be exhaustive, especially for `dimensional_callouts` —
that is the list of every leader-line label and dimension note drawn on the
diagram itself (bolt hole patterns, thread taps, thru-holes, bolt circle
diameters, raised face diameters, thicknesses, lengths, counts, etc).
Transcribe callout text verbatim, including symbols like phi/diameter (write
as "Ø"), degree signs, and tolerances (e.g. "±0.1").

Go through EVERY view on the sheet, not just the largest/main one — plan
views, section views (SECTION A-A, SECTION H-H, etc), detail views, hex/end
views, and any small inset diagrams. Dense assembly drawings often put
critical dimensions (raised face diameter, hex size, terminal/thread notes)
in a small secondary view that is easy to skip. Do not skip any view on the
sheet.

For each callout, if the text includes a purpose phrase like "FOR ELEMENTS",
"FOR THERMOWELL", "FOR BOLT HOLES" etc, keep that phrase in the label
verbatim — it tells a later comparison step which other component this
feature is meant to receive, even when that other component is a completely
different BOM row.

Minimum checklist to actively look for and not miss, if present anywhere on
the sheet: material grade, governing standard/code, size/pressure-class or
rating, quantity — these four are always required if stated anywhere on the
sheet. Beyond those, also capture: every diameter (outer diameter, bore/ID,
bolt circle diameter, raised face diameter, hole diameter), every thread/
tapping spec, every length (overall, cold/hot zone, immersion), every count
(holes, elements, terminals, banks), wattage/electrical ratings, and
terminal/connector type.

If the sheet has a Bill of Materials table (a grid with SR.NO / DESCRIPTION /
QTY / MATERIAL / SIZE columns), this is an assembly/GAD sheet: set
drawing_category to ASSEMBLY_GAD and fill bill_of_materials completely, one
row per BOM entry. A single sheet occasionally has more than one BOM table
(e.g. a main assembly table plus a separate table for a sub-assembly or
spare) — extract every row of every table on this sheet, and set each row's
bom_table field to that table's own heading text verbatim (e.g. "BILL OF
MATERIAL", "BILL OF MATERIAL (FOR EACH BUNDLE)"), so rows from different
tables that happen to reuse the same SR.NO are not confused with each other.
Also capture any equipment/design-data table (e.g. POWER RATING, OPERATING
TEMPERATURE) into part_specifications. On a GAD sheet, ALSO capture every
dimension drawn directly on the assembly views into dimensional_callouts,
exactly as you would for a sub-part sheet below — e.g. "80 COLD ZONE", "920
ACTIVE ZONE", "IMMERSION LENGTH 1000 -0", bundle diameter, casing pipe
OD/ID, bolt circle, hole patterns, raised face, hex size, terminal notes,
etc. These sheet-level dimensions are frequently what a sub-drawing's own
dimensions need to be checked against, so do not skip them just because
there's a BOM table.

If the sheet shows a single component with its own spec block (e.g.
"MATERIAL: ...", "SIZE: ...", "QUANTITY: ... NOS") and no BOM table, this is
a SUB_PART sheet: set drawing_category to SUB_PART and put every spec-block
line into part_specifications as one SpecField each.

Do not skip anything because it seems minor. Do not invent values that are
not visibly written on the sheet.
"""


def _client() -> genai.Client:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key "
            "(https://aistudio.google.com/apikey)."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _extract_call(
    image_paths: List[str], call_label: str, model: str, usage_tracker: UsageTracker | None = None
) -> DrawingExtraction:
    """Run one Gemini vision call over the given page-image(s) and return the parsed extraction."""
    client = _client()

    parts = [types.Part.from_text(text=EXTRACTION_PROMPT)]
    for p in image_paths:
        with open(p, "rb") as f:
            parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png"))

    max_output_tokens = settings.GEMINI_MAX_OUTPUT_TOKENS

    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=DrawingExtraction,
            max_output_tokens=max_output_tokens,
        ),
    )

    if usage_tracker is not None:
        usage_tracker.record(f"extract: {call_label}", getattr(response, "usage_metadata", None))

    _check_finish_reason(response, call_label, max_output_tokens)

    extraction: DrawingExtraction = response.parsed
    if extraction is None:
        # Fallback: parse manually if SDK didn't auto-parse for some reason
        try:
            extraction = DrawingExtraction.model_validate_json(response.text)
        except Exception as e:
            raise RuntimeError(
                f"Could not parse Gemini's response for '{call_label}' as JSON "
                f"(the response was likely cut off before the model finished writing it). "
                f"Try increasing GEMINI_MAX_OUTPUT_TOKENS (current: {max_output_tokens}), "
                f"or lowering RENDER_DPI so the image is smaller. Original error: {e}"
            ) from e
    return extraction


def extract_drawing(
    image_paths: List[str], source_label: str, model: str | None = None, usage_tracker: UsageTracker | None = None
) -> DrawingExtraction:
    """Run one Gemini vision call over all page-images of a single drawing.

    Cheap and fine for a sub-drawing (title block + spec block + callouts on one
    or a few sheets). For a dense multi-sheet GAD, prefer extract_drawing_multi_page
    instead -- cramming several dense sheets (each with its own BOM table) into one
    call causes the model to truncate or merge tables it should have kept separate.
    """
    model = model or settings.GEMINI_MODEL
    extraction = _extract_call(image_paths, source_label, model, usage_tracker)
    extraction.source_file = source_label
    return extraction


def extract_drawing_multi_page(
    image_paths: List[str], source_label: str, model: str | None = None, usage_tracker: UsageTracker | None = None
) -> DrawingExtraction:
    """Extract a (possibly multi-sheet) drawing one Gemini call per page, then merge.

    Dense assembly (GAD) drawings routinely span several sheets, each with its own
    BOM table, design-data table, and dozens of callouts. Sending all sheets in one
    call makes the model compete for context across sheets and it silently truncates
    or merges BOM tables it should have kept separate. One call per page costs more
    but each sheet gets full attention, and results are merged afterwards. Safe to
    use even for a 1-page GAD.
    """
    model = model or settings.GEMINI_MODEL
    n = len(image_paths)
    pages = [
        _extract_call([img], f"{source_label} (p{i}/{n})", model, usage_tracker)
        for i, img in enumerate(image_paths, start=1)
    ]
    return _merge_extractions(pages, source_label)


def _merge_extractions(pages: List[DrawingExtraction], source_label: str) -> DrawingExtraction:
    category = "ASSEMBLY_GAD" if any(p.drawing_category == "ASSEMBLY_GAD" for p in pages) else pages[0].drawing_category
    title_block = next((p.title_block for p in pages if p.title_block.drawing_no), pages[0].title_block)
    return DrawingExtraction(
        source_file=source_label,
        drawing_category=category,
        title_block=title_block,
        part_specifications=[s for p in pages for s in p.part_specifications],
        bill_of_materials=[b for p in pages for b in p.bill_of_materials],
        dimensional_callouts=[c for p in pages for c in p.dimensional_callouts],
        notes=[n for p in pages for n in p.notes],
    )


def _check_finish_reason(response, source_label: str, max_output_tokens: int) -> None:
    """Raise a clear error if Gemini stopped before finishing, instead of letting
    a truncated-JSON parse error surface later with no context."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return
    finish_reason = getattr(candidates[0], "finish_reason", None)
    finish_reason_name = getattr(finish_reason, "name", str(finish_reason))
    if finish_reason_name == "MAX_TOKENS":
        raise RuntimeError(
            f"Gemini's response for '{source_label}' was cut off because it hit the "
            f"output token limit ({max_output_tokens} tokens) before finishing the JSON. "
            f"Increase GEMINI_MAX_OUTPUT_TOKENS and try again."
        )
