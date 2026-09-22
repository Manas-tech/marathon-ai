"""Request/response models for the /comparisons API surface."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.drawing import BOMItem, Status


class UsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class FindingOut(BaseModel):
    """Same shape as Finding, plus the persisted comparison_results row id
    (None if this finding's part couldn't be matched to a drawing and so was
    never saved) and mismatch_acceptance -- the client needs both to drive
    the "accept this mismatch" checkbox."""

    id: Optional[int] = None
    parameter: str
    gad_value: str = ""
    sub_value: str = ""
    status: Status
    mismatch_acceptance: bool = False


class PartComparisonOut(BaseModel):
    part_name: str
    sub_drawing_file: str
    matched_bom_row: Optional[BOMItem] = None
    overall_status: str
    findings: list[FindingOut]


class ComparisonReportOut(BaseModel):
    gad_file: str
    gad_drawing_no: str = ""
    parts: list[PartComparisonOut]


class ComparisonResponse(BaseModel):
    result: ComparisonReportOut
    usage: UsageTotals


class MismatchAcceptanceUpdate(BaseModel):
    mismatch_acceptance: bool


class MismatchAcceptanceResponse(BaseModel):
    id: int
    mismatch_acceptance: bool


# ---------- Job card vs sub-drawing ----------


class JobCardVsSubRequest(BaseModel):
    project_id: int
    job_card_drawing_id: int
    sub_drawing_id: int


class JobCardFindingOut(BaseModel):
    """Persisted jobcard_comparison_results row id + mismatch_acceptance,
    same role as FindingOut for the GAD comparison."""

    id: Optional[int] = None
    parameter: str
    jobcard_value: str = ""
    sub_value: str = ""
    status: str
    mismatch_acceptance: bool = False


class JobCardComparisonReportOut(BaseModel):
    job_card_file: str
    sub_drawing_file: str
    overall_status: str
    findings: list[JobCardFindingOut]


class JobCardComparisonResponse(BaseModel):
    result: JobCardComparisonReportOut
    usage: UsageTotals


# ---------- GAD DXF vs cutting-sheet DXF validation ----------


class GadDxfValidateRequest(BaseModel):
    project_id: int
    gad_drawing_id: int
    cutting_drawing_id: int


class GadDxfMatchOut(BaseModel):
    id: int
    capsule_index: int
    capsule_center: list[float] = []
    hole_center: list[float] = []
    capsule_radius: Optional[float] = None
    hole_radius: Optional[float] = None
    center_offset: Optional[float] = None
    status: str
    mismatch_acceptance: bool = False


class GadDxfRunOut(BaseModel):
    id: int
    gad_drawing_id: int
    cutting_drawing_id: int
    gad_file: str = ""
    cutting_file: str = ""
    overlay_url: str
    outer_radius_gad: Optional[float] = None
    outer_radius_baffle: Optional[float] = None
    outer_radius_diff: Optional[float] = None
    capsule_count: int = 0
    hole_count: int = 0
    count_diff: int = 0
    mean_center_offset: Optional[float] = None
    max_center_offset: Optional[float] = None
    capsule_width: Optional[float] = None
    capsule_overall_length: Optional[float] = None
    mismatch_count: int = 0
    matches: list[GadDxfMatchOut]


class GadDxfHighlightResponse(BaseModel):
    image_url: str
