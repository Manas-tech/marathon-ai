"""
Given the structured extraction of a GAD and one or more sub-drawings,
match each sub-drawing to its corresponding BOM row and produce a
parameter-by-parameter diff.

This is a single text-only Gemini call (no images — the extractions are
already JSON), which is what keeps the whole pipeline cheap. Ported from
the original matcher_comparator.py with no logic changes.
"""
from __future__ import annotations
import json
import re
from typing import List

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.schemas.drawing import DrawingExtraction, ComparisonReport
from app.schemas.jobcard import JobCardComparisonReport, JobCardExtraction
from app.services.usage_service import UsageTracker

settings = get_settings()

COMPARISON_PROMPT = """\
You are a mechanical QA engineer checking that component/sub-drawings agree
with the main assembly drawing (GAD) they belong to.

You are given:
1. The full structured extraction of the GAD (title block, BOM table,
   design-data table, any callouts on the GAD sheet itself).
2. The full structured extraction of one or more sub-drawings (each a
   single component, with its own spec block and every dimensional
   callout drawn on that sheet).

For EACH sub-drawing:
1. Find the BOM row in the GAD whose DESCRIPTION matches that sub-drawing's
   title/part (e.g. sub-drawing titled "HEATER FLANGE" matches the BOM row
   with description "HEATER FLANGE"). If no BOM row matches, set
   overall_status to COULD_NOT_MATCH and explain why in a single finding.
   A GAD extraction may contain several separate BOM tables (see each row's
   bom_table field) that reuse the same description/SR.NO across a main
   assembly and a spare/duplicate one (e.g. "BILL OF MATERIAL (FOR EACH
   BUNDLE)" vs "BILL OF MATERIAL (FOR SPARE BUNDLE)"). If a description
   matches rows in more than one table, prefer the main/primary table (not
   the one whose bom_table heading says SPARE) unless the sub-drawing itself
   indicates it is the spare; if the matched value differs between the
   tables, prefer the main table's value but do not silently drop the
   discrepancy — flag it as a SUB_ONLY/GAD_ONLY note if relevant.
2. ALWAYS check these four first, if stated on both sides — they are
   non-negotiable baseline checks regardless of anything else: MATERIAL,
   SIZE/RATING, GOVERNING STANDARD, QUANTITY.
2a. QUANTITY needs one adjustment before you compare it. The DATA below
    includes a top-level "gad_equipment_quantity_multiplier" field — this
    is already computed for you, so do NOT search part_specifications for
    it yourself. A BOM row's own qty column is the PER-ASSEMBLY quantity;
    multiply it by gad_equipment_quantity_multiplier to get the correct
    gad_value for the QUANTITY finding (state the plain result, e.g. "2",
    not a formula). If gad_equipment_quantity_multiplier is 1, just use
    the BOM qty as-is.
3. Then compare every other parameter that appears on BOTH sides — not just
   the BOM row's own columns, but ALSO every dimensional_callout on the
   sub-drawing against every dimensional_callout and part_specification on
   the GAD sheet. Do this exhaustively: go through the sub-drawing's callout
   list one by one and actively search the GAD's callouts/specs for anything
   describing the same physical feature, even if worded completely
   differently.

   Match by PHYSICAL FEATURE, not by label text. Examples of the kind of
   pairing you must actively look for:
     - a sub-drawing's "X COLD LENGTH" / "Y HOT LENGTH" describes the same
       physical zones as a GAD's "COLD ZONE" / "ACTIVE ZONE" / "IMMERSION
       LENGTH" dimensions on the assembly view showing that same part —
       compare them even though the wording differs completely.
     - a sub-drawing's hole/thread callouts (bolt hole count+diameter, bolt
       circle diameter, thermowell hole diameter, raised face diameter,
       tapping depth) against any matching dimension the GAD shows for that
       same part's own section/detail view.
     - a sub-drawing's overall length/diameter/thickness against any matching
       dimension in the GAD's section views or design-data table.
     - element/hole counts, wattage/ratings, terminal type, and any other
       numeric or textual spec against the GAD's equivalent field.
4. CROSS-REFERENCE relational hints against OTHER BOM rows, not just the
   matched one. Many callouts on a sub-drawing describe a feature that is
   sized to *receive* a different component — the callout text usually says
   so directly (e.g. "46 x Ø17.3 THRU FOR ELEMENTS" on a flange means those
   46 holes are meant for heating elements; "2 x Ø12.6 THRU FOR THERMOWELL"
   means those holes are meant for thermowells). Whenever you see a callout
   like this, look up the GAD's actual quantity for the referenced component
   (from that OTHER component's own BOM row or design-data table — e.g.
   "NUMBERS OF HEATING ELEMENTS", or the THERMOWELL BOM row's qty) and check
   whether the hole/feature count on the sub-drawing actually matches how
   many of that component the assembly uses. Flag a MISMATCH by name (e.g.
   "ELEMENT HOLE COUNT vs ACTUAL ELEMENT QUANTITY") if they disagree — this
   kind of internal-consistency check is exactly what this comparison exists
   to catch, and it is easy to miss if you only look at the matched BOM row's
   own columns.

   Judge equivalence by engineering meaning, not exact string match — e.g.
   "SA-105" and "SA105" are the same, but "SA-105" and "SA-105N" are NOT the
   same material grade and must be flagged; "8 inch" and "8\\"" are the same
   size, but "8\\" 300#" and "4\\" 150#" are NOT the same and must be flagged.
   If a sub-drawing dimension and the closest-matching GAD dimension disagree
   numerically, flag it — do not silently drop it just because the labels
   don't match word-for-word.
5. For every parameter you compare, emit one Finding with status:
   - MATCH: values agree (may be worded differently but mean the same thing)
   - MISMATCH: values genuinely conflict
   - GAD_ONLY: parameter is only stated in the GAD (informational, not a defect)
   - SUB_ONLY: parameter is only stated on the sub-drawing (informational,
     usually just extra detail the GAD doesn't carry, not a defect)
6. Set overall_status to DISCREPANCIES_FOUND if there is at least one
   MISMATCH finding, otherwise CONSISTENT (GAD_ONLY/SUB_ONLY findings alone
   do not count as discrepancies).

Be thorough on steps 3 and 4 — a shallow comparison that only checks the 3-4
BOM columns and ignores the callout lists and cross-component relations is
exactly the failure mode to avoid. At the same time, be precise: only mark
MISMATCH when there is a genuine conflict in stated values, not just because
the GAD is less detailed than the sub-drawing.
"""


def _client() -> genai.Client:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _gad_equipment_quantity_multiplier(gad: DrawingExtraction) -> int:
    for spec in gad.part_specifications:
        if spec.parameter.strip().upper() == "QUANTITY":
            match = re.search(r"\d+", spec.value or "")
            if match:
                return int(match.group())
    return 1


def compare(
    gad: DrawingExtraction,
    subs: List[DrawingExtraction],
    model: str | None = None,
    usage_tracker: UsageTracker | None = None,
) -> ComparisonReport:
    model = model or settings.GEMINI_COMPLEX_MODEL or settings.GEMINI_MODEL
    client = _client()

    payload = {
        "gad": gad.model_dump(),
        "gad_equipment_quantity_multiplier": _gad_equipment_quantity_multiplier(gad),
        "sub_drawings": [s.model_dump() for s in subs],
    }

    max_output_tokens = settings.GEMINI_MAX_OUTPUT_TOKENS

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=COMPARISON_PROMPT),
                    types.Part.from_text(text="DATA:\n" + json.dumps(payload, ensure_ascii=False)),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ComparisonReport,
            max_output_tokens=max_output_tokens,
        ),
    )

    if usage_tracker is not None:
        usage_tracker.record("compare", getattr(response, "usage_metadata", None))

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        finish_reason_name = getattr(finish_reason, "name", str(finish_reason))
        if finish_reason_name == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini's comparison response was cut off because it hit the output "
                f"token limit ({max_output_tokens} tokens) before finishing the JSON. "
                f"Increase GEMINI_MAX_OUTPUT_TOKENS and try again."
            )

    report: ComparisonReport = response.parsed
    if report is None:
        try:
            report = ComparisonReport.model_validate_json(response.text)
        except Exception as e:
            raise RuntimeError(
                f"Could not parse Gemini's comparison response as JSON (it was likely "
                f"cut off before finishing). Try increasing GEMINI_MAX_OUTPUT_TOKENS "
                f"(current: {max_output_tokens}). Original error: {e}"
            ) from e
    report.gad_file = gad.source_file
    report.gad_drawing_no = gad.title_block.drawing_no
    return report


# ---------- Sub-drawing vs job card ----------

# Sub-drawings whose filename (lowercased, non-alphanumerics stripped)
# contains one of these are checked against the job card. static/js/
# run-comparison.js mirrors this list to enable its button -- keep in sync.
JOBCARD_COMPARABLE_SUB_KEYWORDS = ("heatingelement",)


def is_jobcard_comparable_sub(filename: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", (filename or "").lower())
    return any(k in normalized for k in JOBCARD_COMPARABLE_SUB_KEYWORDS)


JOBCARD_COMPARISON_PROMPT = """\
You are a manufacturing QA engineer checking that a JOB CARD (the shop-floor
work order for a heating element) agrees with the SUB-DRAWING of that same
part (the engineering drawing it was generated from).

You are given:
1. The job card extraction: document_meta plus a flat list of parameters
   (parameter/value/unit/category/table_or_section) and notes.
2. The sub-drawing extraction: title block, part_specifications, and every
   dimensional_callout drawn on the sheet.

Compare every parameter that appears on BOTH sides. Match by PHYSICAL
FEATURE, not label text -- the job card and the drawing word things
differently (e.g. job card "Watts"/"Power" vs drawing "RATING"; job card
"Heated Length"/"Active" vs drawing "HOT LENGTH"/"ACTIVE ZONE"; job card
"Cold Length" vs drawing "COLD LENGTH"/"COLD ZONE"; diameters, sheath/
material grade, voltage, quantity, terminal type, drawing number and
revision). Go through the job card parameters one by one and actively look
for the matching drawing spec or callout, and vice versa.

Judge equivalence by engineering meaning, not exact string match -- "SS316"
and "SS 316" are the same, "SS316" and "SS316L" are NOT; "1000 mm" and
"1000" with unit mm are the same; units must be compared (convert only if
the conversion is exact, e.g. inch <-> mm at 25.4). Do not compute derived
values that are not printed on either document.

Emit one finding per compared parameter with status:
  - MATCH: values agree (may be worded differently but mean the same thing)
  - MISMATCH: values genuinely conflict
  - JOBCARD_ONLY: stated only on the job card (informational, not a defect)
  - SUB_ONLY: stated only on the sub-drawing (informational, not a defect)
Put each side's value as printed (with unit) in jobcard_value / sub_value.
Only mark MISMATCH for a genuine conflict in stated values, not because one
document is less detailed than the other.

Set overall_status to DISCREPANCIES_FOUND if there is at least one MISMATCH
finding, otherwise CONSISTENT.
"""


def compare_jobcard_to_sub(
    job_card: JobCardExtraction,
    sub: DrawingExtraction,
    model: str | None = None,
    usage_tracker: UsageTracker | None = None,
) -> JobCardComparisonReport:
    """One text-only Gemini call: job card extraction vs one sub-drawing extraction."""
    model = model or settings.GEMINI_COMPLEX_MODEL or settings.GEMINI_MODEL
    client = _client()

    payload = {"job_card": job_card.model_dump(), "sub_drawing": sub.model_dump()}
    max_output_tokens = settings.GEMINI_MAX_OUTPUT_TOKENS

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=JOBCARD_COMPARISON_PROMPT),
                    types.Part.from_text(text="DATA:\n" + json.dumps(payload, ensure_ascii=False)),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=JobCardComparisonReport,
            max_output_tokens=max_output_tokens,
        ),
    )

    if usage_tracker is not None:
        usage_tracker.record("compare: jobcard vs sub", getattr(response, "usage_metadata", None))

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        if getattr(finish_reason, "name", str(finish_reason)) == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini's job card comparison response was cut off because it hit the output "
                f"token limit ({max_output_tokens} tokens). Increase GEMINI_MAX_OUTPUT_TOKENS and try again."
            )

    report: JobCardComparisonReport = response.parsed
    if report is None:
        try:
            report = JobCardComparisonReport.model_validate_json(response.text)
        except Exception as e:
            raise RuntimeError(
                f"Could not parse Gemini's job card comparison response as JSON. Original error: {e}"
            ) from e
    report.job_card_file = job_card.source_file
    report.sub_drawing_file = sub.source_file
    return report
