"""
Structured data contracts for the extraction + comparison pipeline.

This is the old top-level `schemas.py`, moved under app/schemas/ unchanged
in content. Extraction schemas are passed to Gemini as response_schema so
the model is forced to return valid, predictable JSON instead of free text
we'd have to regex out of a paragraph.
"""
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


# ---------- Extraction (per-drawing) ----------

class TitleBlock(BaseModel):
    drawing_no: str = Field(description="DWG NO. field, e.g. MH-TH/2193000445_02")
    title: str = Field(description="TITLE field, e.g. HEATER FLANGE")
    customer: str = Field(default="", description="CUSTOMER field")
    revision: str = Field(default="", description="REVISION field")


class SpecField(BaseModel):
    """A labeled key:value fact stated in text anywhere on the sheet —
    title-block specs, the free-text spec block (e.g. 'MATERIAL: SA-105N'),
    or an equipment-data-table row in a GAD (e.g. 'POWER RATING | 5 kW')."""
    parameter: str = Field(description="Name of the parameter, e.g. MATERIAL, SIZE, QUANTITY, POWER RATING")
    value: str = Field(description="The stated value, verbatim, e.g. 'SA-105N', '8\" 300# BLRF', '23 NOS'")


class BOMItem(BaseModel):
    sr_no: str = ""
    description: str = Field(description="Part description/name, e.g. HEATER FLANGE")
    qty: str = ""
    material: str = ""
    size: str = ""
    remarks: str = ""
    bom_table: str = Field(
        default="",
        description="The heading of the BOM table this row came from, verbatim, e.g. "
                    "'BILL OF MATERIAL', 'BILL OF MATERIAL (FOR EACH BUNDLE)', "
                    "'BILL OF MATERIAL (FOR SPARE BUNDLE)'. A multi-sheet assembly drawing "
                    "often has several separate BOM tables (one per sheet/sub-assembly) that "
                    "reuse the same SR.NO numbering — this field disambiguates them.",
    )


class Callout(BaseModel):
    """A labeled feature called out on the diagram itself via a leader
    line or dimension label — NOT part of a title block or table.
    e.g. '12 x Ø25.4 FOR BOLT HOLES', '46 x Ø17.3 THRU FOR ELEMENTS',
    'M8 TAPPING UPTO 15mm DEEP', 'BCD Ø330.2'."""
    label: str = Field(description="The full callout text as drawn, verbatim")
    category: str = Field(
        description="Short category tag you infer, e.g. 'bolt_hole', 'thread_tap', "
                    "'thru_hole', 'bolt_circle_diameter', 'flange_od', 'raised_face', "
                    "'thickness', 'length', 'element_count', 'other'"
    )


class DrawingExtraction(BaseModel):
    source_file: str = ""
    drawing_category: Literal["ASSEMBLY_GAD", "SUB_PART"] = Field(
        description="ASSEMBLY_GAD if this sheet shows a full assembly with a Bill of "
                    "Materials table; SUB_PART if it is a single component drawing."
    )
    title_block: TitleBlock
    part_specifications: List[SpecField] = Field(
        default_factory=list,
        description="Every labeled spec stated as text on the sheet: material, size/rating, "
                    "standard, quantity, ratings, design-data-table rows, etc. Be exhaustive."
    )
    bill_of_materials: List[BOMItem] = Field(
        default_factory=list,
        description="Full BOM table rows if present (GAD sheets only). Empty list if none."
    )
    dimensional_callouts: List[Callout] = Field(
        default_factory=list,
        description="Every leader-line / diagram label on the sheet, verbatim. Be exhaustive — "
                    "this is the most important field for catching real discrepancies."
    )
    notes: List[str] = Field(default_factory=list, description="Any free-standing NOTE: text")


# ---------- Comparison (per matched pair, across drawings) ----------

Status = Literal["MATCH", "MISMATCH", "GAD_ONLY", "SUB_ONLY"]


class Finding(BaseModel):
    parameter: str = Field(description="What is being compared, e.g. MATERIAL, SIZE/RATING, BOLT HOLE COUNT, COLD ZONE LENGTH")
    gad_value: str = Field(default="", description="Value as it appears in/derived from the GAD; empty if not present there")
    sub_value: str = Field(default="", description="Value as it appears on the sub-drawing; empty if not present there")
    status: Status


class PartComparison(BaseModel):
    part_name: str
    sub_drawing_file: str
    matched_bom_row: Optional[BOMItem] = None
    overall_status: Literal["CONSISTENT", "DISCREPANCIES_FOUND", "COULD_NOT_MATCH"]
    findings: List[Finding] = Field(default_factory=list)


class ComparisonReport(BaseModel):
    gad_file: str
    gad_drawing_no: str = ""
    parts: List[PartComparison] = Field(default_factory=list)
