"""
Per-file Upload tab API: upload ONE file, auto-extract it immediately, poll
its status, or remove it. Replaces the old "pick up to 4 files, then click
one shared Extraction button" flow -- that button re-submitted every card's
currently-selected file on every click, so extracting a second card
silently re-extracted the first one too. Here each card is independent:
selecting a file uploads+extracts it right away, and nothing about that
depends on any other card's state.

    POST   /api/v1/drawings              upload one file, kicks off extraction
    GET    /api/v1/drawings/{id}         poll extraction status
    DELETE /api/v1/drawings/{id}         soft-delete (the "x" button)
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import Project
from app.models.project_drawing import DrawingType, ProjectDrawing
from app.schemas.drawing_upload import DrawingStatusResponse
from app.services import persistence_service
from app.services.pipeline_service import run_single_drawing_extraction
from app.utils.file_utils import new_job_work_dir, save_upload, validate_dxf_upload, validate_pdf_upload

router = APIRouter(prefix="/drawings", tags=["drawings"])

_TYPE_LABEL = {
    DrawingType.GAD: "GAD",
    DrawingType.SUB: "SUB",
    DrawingType.JOB_CARD: "JOB_CARD",
    DrawingType.DXF: "DXF",
}
_VALID_TYPES = set(_TYPE_LABEL)


def _to_response(drawing: ProjectDrawing) -> DrawingStatusResponse:
    return DrawingStatusResponse(
        id=drawing.id,
        project_id=drawing.project_id,
        title=drawing.title,
        type=drawing.type,
        type_label=_TYPE_LABEL.get(drawing.type, "UNKNOWN"),
        is_extracted=drawing.is_extracted,
        extraction_error=drawing.extraction_error,
        is_deleted=drawing.is_deleted,
        created_at=drawing.created_at,
        updated_at=drawing.updated_at,
    )


@router.post("", response_model=DrawingStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_drawing(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="The PDF (GAD/sub/job card) or DXF file"),
    drawing_type: int = Form(..., description="0=GAD, 1=SUB, 2=JOB_CARD, 3=DXF"),
    project_id: int | None = Form(None, description="Project to attach this drawing to"),
    db: Session = Depends(get_db),
) -> DrawingStatusResponse:
    if drawing_type not in _VALID_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid drawing_type {drawing_type}.")
    if project_id is not None and db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {project_id}.")

    project = persistence_service.get_project_or_default(db, project_id)

    work_dir = new_job_work_dir()
    validator = validate_dxf_upload if drawing_type == DrawingType.DXF else validate_pdf_upload
    saved_path = await save_upload(file, work_dir, validator=validator)

    # Row is created NOW, before extraction runs -- is_extracted=False until
    # the background task finishes. This means an upload always leaves a
    # trace even if extraction later fails, instead of the old convention
    # where a failed extraction left nothing in project_drawings at all.
    drawing = persistence_service.save_drawing(
        db, project_id=project.id, title=saved_path.name, path=str(saved_path), drawing_type=drawing_type
    )

    background_tasks.add_task(
        run_single_drawing_extraction,
        drawing_id=drawing.id,
        path=str(saved_path),
        drawing_type=drawing_type,
        work_dir=str(work_dir),
    )

    return _to_response(drawing)


@router.get("/{drawing_id}", response_model=DrawingStatusResponse)
def get_drawing_status(drawing_id: int, db: Session = Depends(get_db)) -> DrawingStatusResponse:
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No drawing with id {drawing_id}.")
    return _to_response(drawing)


@router.delete("/{drawing_id}", response_model=DrawingStatusResponse)
def delete_drawing(drawing_id: int, db: Session = Depends(get_db)) -> DrawingStatusResponse:
    drawing = persistence_service.soft_delete_drawing(db, drawing_id)
    if drawing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No drawing with id {drawing_id}.")
    return _to_response(drawing)
