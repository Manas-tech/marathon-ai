"""
Read-only browsing API for previously-run jobs, grouped the way the MySQL
persistence layer (see persistence_service.py) stores them:

    GET /api/v1/projects                          list projects
    GET /api/v1/projects/{project_id}              a project's drawings (GAD + subs)
    GET /api/v1/projects/{project_id}/drawings/{drawing_id}
                                                    one drawing's extraction +
                                                    (for SUB drawings) comparison findings

This sits next to /comparisons (which runs new jobs) -- it only reads what
pipeline_service already persisted via persistence_service.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.comparison_result import ComparisonResult
from app.models.extraction_result import ExtractionResult
from app.models.gad_dxf_validation import GadDxfValidationRun
from app.models.jobcard_comparison_result import JobCardComparisonResult
from app.models.project import Project
from app.models.project_drawing import DrawingType, ProjectDrawing
from app.schemas.job import GadDxfRunOut
from app.schemas.project import (
    ComparisonFindingItem,
    DrawingDetailResponse,
    DrawingListItem,
    DxfOutputItem,
    ExtractionView,
    JobCardComparisonGroup,
    JobCardComparisonItem,
    ProjectCreate,
    ProjectDetailResponse,
    ProjectListItem,
)
from app.services import storage_service
from app.services.persistence_service import (
    gad_dxf_run_to_out,
    unflatten_dxf_outputs,
    unflatten_extraction,
    unflatten_jobcard_extraction,
)

router = APIRouter(prefix="/projects", tags=["projects"])

_TYPE_LABEL = {
    DrawingType.GAD: "GAD",
    DrawingType.SUB: "SUB",
    DrawingType.JOB_CARD: "JOB_CARD",
    DrawingType.DXF: "DXF",
}


def _drawing_list_item(drawing: ProjectDrawing) -> DrawingListItem:
    return DrawingListItem(
        id=drawing.id,
        title=drawing.title,
        type=drawing.type,
        type_label=_TYPE_LABEL.get(drawing.type, "UNKNOWN"),
        is_extracted=drawing.is_extracted,
        extraction_error=drawing.extraction_error,
        created_at=drawing.created_at,
        updated_at=drawing.updated_at,
    )


@router.get("", response_model=list[ProjectListItem])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectListItem]:
    stmt = (
        select(Project, func.count(ProjectDrawing.id))
        .outerjoin(
            ProjectDrawing,
            and_(ProjectDrawing.project_id == Project.id, ProjectDrawing.is_deleted.is_(False)),
        )
        .where(Project.is_deleted.is_(False))
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
    )
    return [
        ProjectListItem(
            id=project.id,
            so_no=project.so_no,
            name=project.name,
            description=project.description,
            created_at=project.created_at,
            updated_at=project.updated_at,
            drawing_count=count,
        )
        for project, count in db.execute(stmt).all()
    ]


@router.post("", response_model=ProjectListItem, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectListItem:
    project = Project(name=payload.name, so_no=payload.so_no, description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectListItem(
        id=project.id,
        so_no=project.so_no,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        drawing_count=0,
    )


def _get_project_or_404(project_id: int, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.is_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {project_id}.")
    return project


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectDetailResponse:
    project = _get_project_or_404(project_id, db)

    stmt = (
        select(ProjectDrawing)
        .where(ProjectDrawing.project_id == project_id, ProjectDrawing.is_deleted.is_(False))
        .order_by(ProjectDrawing.type.asc(), ProjectDrawing.created_at.asc())
    )
    drawings = list(db.scalars(stmt))

    return ProjectDetailResponse(
        id=project.id,
        so_no=project.so_no,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        drawing_count=len(drawings),
        drawings=[_drawing_list_item(d) for d in drawings],
    )


def _find_compared_gad(db: Session, project_id: int, sub_drawing: ProjectDrawing) -> ProjectDrawing | None:
    """Fallback only, for legacy comparison_results rows written before
    gad_drawing_id existed and somehow missed the migration's backfill.
    New rows carry gad_drawing_id explicitly (see persistence_service.save_comparison)
    so this timestamp-nearest-GAD guess should no longer be needed in practice."""
    stmt = (
        select(ProjectDrawing)
        .where(
            and_(
                ProjectDrawing.project_id == project_id,
                ProjectDrawing.type == DrawingType.GAD,
                ProjectDrawing.is_deleted.is_(False),
                ProjectDrawing.created_at <= sub_drawing.created_at,
            )
        )
        .order_by(ProjectDrawing.created_at.desc())
        .limit(1)
    )
    gad = db.scalars(stmt).first()
    if gad is not None:
        return gad

    fallback_stmt = (
        select(ProjectDrawing)
        .where(
            ProjectDrawing.project_id == project_id,
            ProjectDrawing.type == DrawingType.GAD,
            ProjectDrawing.is_deleted.is_(False),
        )
        .order_by(ProjectDrawing.created_at.desc())
        .limit(1)
    )
    return db.scalars(fallback_stmt).first()


def _jobcard_comparison_groups(db: Session, drawing: ProjectDrawing) -> list[JobCardComparisonGroup]:
    """Job card vs sub-drawing findings for a JOB_CARD or SUB drawing, grouped
    by the drawing on the other side. Counterparts that were soft-deleted are
    skipped (their comparison is stale), matching how listings hide them."""
    if drawing.type == DrawingType.JOB_CARD:
        own_col, other_col = JobCardComparisonResult.job_card_drawing_id, JobCardComparisonResult.sub_drawing_id
    elif drawing.type == DrawingType.SUB:
        own_col, other_col = JobCardComparisonResult.sub_drawing_id, JobCardComparisonResult.job_card_drawing_id
    else:
        return []

    rows = list(
        db.scalars(
            select(JobCardComparisonResult).where(own_col == drawing.id).order_by(JobCardComparisonResult.id)
        )
    )
    findings_by_other: dict[int, list[JobCardComparisonResult]] = {}
    for row in rows:
        other_id = row.sub_drawing_id if drawing.type == DrawingType.JOB_CARD else row.job_card_drawing_id
        findings_by_other.setdefault(other_id, []).append(row)

    groups = []
    for other_id, findings in findings_by_other.items():
        other = db.get(ProjectDrawing, other_id)
        if other is None or other.is_deleted:
            continue
        groups.append(
            JobCardComparisonGroup(
                counterpart=_drawing_list_item(other),
                findings=[JobCardComparisonItem.model_validate(f) for f in findings],
            )
        )
    return groups


def _gad_dxf_validations(db: Session, drawing: ProjectDrawing) -> list[GadDxfRunOut]:
    """GAD DXF vs cutting DXF validation runs a DXF drawing took part in (as
    either side), newest first. Runs whose other drawing was soft-deleted are
    skipped as stale, same as the job card comparisons."""
    if drawing.type != DrawingType.DXF:
        return []
    runs = db.scalars(
        select(GadDxfValidationRun)
        .where(
            (GadDxfValidationRun.gad_drawing_id == drawing.id)
            | (GadDxfValidationRun.cutting_drawing_id == drawing.id)
        )
        .order_by(GadDxfValidationRun.id.desc())
    )
    out = []
    for run in runs:
        other_id = run.cutting_drawing_id if run.gad_drawing_id == drawing.id else run.gad_drawing_id
        other = db.get(ProjectDrawing, other_id)
        if other is None or other.is_deleted:
            continue
        out.append(gad_dxf_run_to_out(db, run))
    return out


@router.get("/{project_id}/drawings/{drawing_id}", response_model=DrawingDetailResponse)
def get_drawing_detail(
    project_id: int, drawing_id: int, db: Session = Depends(get_db)
) -> DrawingDetailResponse:
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None or drawing.project_id != project_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No drawing with id {drawing_id} in project {project_id}.",
        )

    extraction_rows = list(
        db.scalars(
            select(ExtractionResult).where(ExtractionResult.drawing_id == drawing_id)
        )
    )

    extraction = None
    job_card_extraction = None
    dxf_outputs: list[DxfOutputItem] = []
    if extraction_rows and drawing.type == DrawingType.JOB_CARD:
        job_card_extraction = unflatten_jobcard_extraction(extraction_rows)
    elif extraction_rows and drawing.type == DrawingType.DXF:
        dxf_outputs = [
            DxfOutputItem(label=o["label"], image_url=storage_service.resolve_image_url(o["path"]))
            for o in unflatten_dxf_outputs(extraction_rows)
        ]
    elif extraction_rows:
        extraction = ExtractionView(**unflatten_extraction(extraction_rows))

    comparison_rows = list(
        db.scalars(
            select(ComparisonResult).where(ComparisonResult.drawing_id == drawing_id)
        )
    )

    jobcard_comparisons = _jobcard_comparison_groups(db, drawing)
    gad_dxf_validations = _gad_dxf_validations(db, drawing)

    gad_drawing = None
    if drawing.type == DrawingType.SUB and comparison_rows:
        gad_id = comparison_rows[0].gad_drawing_id
        gad = db.get(ProjectDrawing, gad_id) if gad_id is not None else _find_compared_gad(db, project_id, drawing)
        if gad is not None:
            gad_drawing = _drawing_list_item(gad)

    return DrawingDetailResponse(
        id=drawing.id,
        title=drawing.title,
        type=drawing.type,
        type_label=_TYPE_LABEL.get(drawing.type, "UNKNOWN"),
        is_extracted=drawing.is_extracted,
        extraction_error=drawing.extraction_error,
        created_at=drawing.created_at,
        updated_at=drawing.updated_at,
        extraction=extraction,
        comparison=[ComparisonFindingItem.model_validate(r) for r in comparison_rows],
        gad_drawing=gad_drawing,
        job_card_extraction=job_card_extraction,
        jobcard_comparisons=jobcard_comparisons,
        gad_dxf_validations=gad_dxf_validations,
        dxf_outputs=dxf_outputs,
    )
