"""
Structured data contract for job-card extraction, mirroring how
app/schemas/drawing.py's DrawingExtraction is passed to Gemini as
response_schema. This is a Pydantic version of the JSON-schema dict used by
the original standalone extract_jobcard.py -- same fields, now a real model
so it round-trips through Gemini's structured output and Pydantic validation
instead of a hand-checked dict.

Job cards are shop-floor work orders derived from a drawing (heater/element
cards, machining travelers, PCB fab cards, etc.) -- a flat, generic
label/value schema fits any of them, unlike DrawingExtraction's BOM/callout
structure which is specific to engineering drawings.
"""
from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field


class JobCardDocumentMeta(BaseModel):
    work_order_or_drawing_number: str = ""
    revision: str = ""
    date: str = ""
    customer_or_client: str = ""
    prepared_by: str = ""
    checked_by: str = ""


class JobCardParameter(BaseModel):
    parameter: str = Field(description="The label/name exactly as printed, e.g. 'Hot Ohms', 'Volts'")
    value: str = Field(description="The value exactly as printed, including fractions/tolerances")
    unit: str = Field(default="", description="Unit if present (mm, inch, ohms, watts, volts, amps, %, etc.)")
    category: str = Field(
        default="",
        description="Best-fit grouping, e.g. 'Electrical', 'Dimensional', 'Material', "
                    "'Identification', 'Process/Manufacturing', 'Tolerance', 'Quality/Inspection', 'Other'.",
    )
    table_or_section: str = Field(default="", description="Which section/table on the card this came from")


class JobCardExtraction(BaseModel):
    source_file: str = ""
    document_meta: JobCardDocumentMeta = Field(default_factory=JobCardDocumentMeta)
    parameters: List[JobCardParameter] = Field(
        default_factory=list,
        description="Every discrete labeled parameter found anywhere on the job card.",
    )
    notes_and_flags: List[str] = Field(
        default_factory=list, description="Any printed notes, warnings, or special instructions"
    )


# ---------- Job card vs sub-drawing comparison ----------

JobCardFindingStatus = Literal["MATCH", "MISMATCH", "JOBCARD_ONLY", "SUB_ONLY"]


class JobCardFinding(BaseModel):
    parameter: str = Field(description="What is being compared, e.g. WATTAGE, VOLTAGE, ELEMENT LENGTH, MATERIAL")
    jobcard_value: str = Field(default="", description="Value as printed on the job card (with unit); empty if absent there")
    sub_value: str = Field(default="", description="Value as it appears on the sub-drawing; empty if absent there")
    status: JobCardFindingStatus


class JobCardComparisonReport(BaseModel):
    """Gemini response_schema for one sub-drawing checked against one job card."""

    job_card_file: str = ""
    sub_drawing_file: str = ""
    overall_status: Literal["CONSISTENT", "DISCREPANCIES_FOUND"]
    findings: List[JobCardFinding] = Field(default_factory=list)
