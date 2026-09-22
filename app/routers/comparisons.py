"""
API surface that replaces the old Streamlit `app.py` UI.

    POST  /api/v1/comparisons                 upload a GAD + sub-drawings, run + return the report
    POST  /api/v1/comparisons/from-extraction  compare drawings already extracted by the Upload tab
    PATCH /api/v1/comparisons/results/{id}     accept/unaccept one MISMATCH finding
    POST  /api/v1/comparisons/jobcard-vs-sub   compare an extracted heating-element sub-drawing to the job card
    PATCH /api/v1/comparisons/jobcard-results/{id}  accept/unaccept one job card MISMATCH finding

The two POST endpoints run the pipeline synchronously and block until it
finishes -- no job row, no polling. The client just waits on the request; on
success the response body is the finished report + usage totals, on failure
it's an HTTP error.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.comparison_result import ComparisonResult
from app.models.jobcard_comparison_result import JobCardComparisonResult
from app.models.project import Project
from app.models.project_drawing import DrawingType, ProjectDrawing
from app.schemas.extraction_batch import CompareFromExtractionRequest
from app.schemas.job import (
    ComparisonResponse,
    JobCardComparisonResponse,
    JobCardVsSubRequest,
    MismatchAcceptanceResponse,
    MismatchAcceptanceUpdate,
)
from app.services.comparison_service import is_jobcard_comparable_sub
from app.services.pipeline_service import run_comparison, run_comparison_from_extraction, run_jobcard_vs_sub
from app.utils.file_utils import new_job_work_dir, save_upload, validate_dxf_upload

router = APIRouter(prefix="/comparisons", tags=["comparisons"])


@router.post("", response_model=ComparisonResponse)
async def create_comparison(
    gad: UploadFile = File(..., description="Main assembly drawing (GAD), PDF"),
    subs: list[UploadFile] = File(..., description="One or more sub-drawing PDFs"),
    job_card: UploadFile | None = File(None, description="Optional job card PDF (extraction only, no comparison)"),
    dxf: UploadFile | None = File(None, description="Optional DXF sheet (split + baffle overlay, no comparison)"),
    project_id: int | None = Form(None, description="Project to attach this run's drawings/results to"),
    db: Session = Depends(get_db),
) -> ComparisonResponse:
    if not subs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one sub-drawing is required.")

    if project_id is not None and db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {project_id}.")

    work_dir = new_job_work_dir()
    gad_path = await save_upload(gad, work_dir)
    sub_paths = [await save_upload(f, work_dir) for f in subs]
    job_card_path = await save_upload(job_card, work_dir) if job_card is not None and job_card.filename else None
    dxf_path = (
        await save_upload(dxf, work_dir, validator=validate_dxf_upload)
        if dxf is not None and dxf.filename
        else None
    )

    try:
        result, usage = run_comparison(
            gad_path=str(gad_path),
            sub_paths=[str(p) for p in sub_paths],
            work_dir=str(work_dir),
            project_id=project_id,
            job_card_path=str(job_card_path) if job_card_path else None,
            dxf_path=str(dxf_path) if dxf_path else None,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the client as the error response
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return ComparisonResponse(result=result, usage=usage)


@router.patch("/results/{result_id}", response_model=MismatchAcceptanceResponse)
def update_mismatch_acceptance(
    result_id: int, payload: MismatchAcceptanceUpdate, db: Session = Depends(get_db)
) -> MismatchAcceptanceResponse:
    """Comparison table's accept-mismatch checkbox: flips mismatch_acceptance
    on one comparison_results row. Not restricted to MISMATCH-status rows at
    the API level -- the checkbox is only ever rendered for those, but there's
    no reason to enforce that server-side too."""
    row = db.get(ComparisonResult, result_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No comparison result with id {result_id}.")
    row.mismatch_acceptance = payload.mismatch_acceptance
    db.commit()
    return MismatchAcceptanceResponse(id=row.id, mismatch_acceptance=row.mismatch_acceptance)


@router.post("/from-extraction", response_model=ComparisonResponse)
def create_comparison_from_extraction(
    payload: CompareFromExtractionRequest, db: Session = Depends(get_db)
) -> ComparisonResponse:
    """Run Comparison tab's "Compare GAD vs Sub" button: GAD/subs were
    already extracted by an earlier per-file upload -- this just runs the
    compare() step against their stored extractions, no re-upload."""
    if not payload.sub_drawing_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one sub-drawing id is required.")
    if db.get(Project, payload.project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {payload.project_id}.")

    try:
        result, usage = run_comparison_from_extraction(
            project_id=payload.project_id,
            gad_drawing_id=payload.gad_drawing_id,
            sub_drawing_ids=payload.sub_drawing_ids,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the client as the error response
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return ComparisonResponse(result=result, usage=usage)


@router.post("/jobcard-vs-sub", response_model=JobCardComparisonResponse)
def create_jobcard_vs_sub_comparison(
    payload: JobCardVsSubRequest, db: Session = Depends(get_db)
) -> JobCardComparisonResponse:
    """Job card card's "Compare" button: checks one already-extracted
    sub-drawing (only heating-element drawings qualify) against the
    already-extracted job card. No re-extraction."""
    if db.get(Project, payload.project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {payload.project_id}.")

    job_card = db.get(ProjectDrawing, payload.job_card_drawing_id)
    sub = db.get(ProjectDrawing, payload.sub_drawing_id)
    checks = ((job_card, DrawingType.JOB_CARD, "job card"), (sub, DrawingType.SUB, "sub-drawing"))
    for drawing, expected_type, label in checks:
        if drawing is None or drawing.is_deleted or drawing.project_id != payload.project_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No active {label} in this project with that id.")
        if drawing.type != expected_type:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Drawing {drawing.id} is not a {label}.")
        if not drawing.is_extracted:
            raise HTTPException(status.HTTP_409_CONFLICT, f"'{drawing.title}' has not finished extracting.")
    if not is_jobcard_comparable_sub(sub.title):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{sub.title}' is not a heating-element drawing; only those are compared against the job card.",
        )

    try:
        result, usage = run_jobcard_vs_sub(
            project_id=payload.project_id,
            job_card_drawing_id=payload.job_card_drawing_id,
            sub_drawing_id=payload.sub_drawing_id,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced to the client as the error response
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    return JobCardComparisonResponse(result=result, usage=usage)


@router.patch("/jobcard-results/{result_id}", response_model=MismatchAcceptanceResponse)
def update_jobcard_mismatch_acceptance(
    result_id: int, payload: MismatchAcceptanceUpdate, db: Session = Depends(get_db)
) -> MismatchAcceptanceResponse:
    row = db.get(JobCardComparisonResult, result_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No job card comparison result with id {result_id}.")
    row.mismatch_acceptance = payload.mismatch_acceptance
    db.commit()
    return MismatchAcceptanceResponse(id=row.id, mismatch_acceptance=row.mismatch_acceptance)
