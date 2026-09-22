"""
GAD DXF vs cutting-sheet DXF validation (POST /dxf/gad-validate and friends,
see the bottom of this file) plus the older split-sheet baffle overlay:

Run Comparison tab's "Validate DXF" button: takes a DXF drawing that was
already split during the Upload tab's extraction step (see
dxf_service.run_dxf_split, persistence_service.save_dxf_split) and runs the
trace + overlay stage on those existing split files -- no re-splitting, no
new upload. Synchronous (no Gemini call involved; matplotlib overlay
rendering is fast), unlike /extractions and /comparisons which poll.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models.extraction_result import ExtractionResult
from app.models.gad_dxf_validation import GadDxfValidationMatch, GadDxfValidationRun
from app.models.project import Project
from app.models.project_drawing import DrawingType, ProjectDrawing
from app.schemas.job import (
    GadDxfHighlightResponse,
    GadDxfRunOut,
    GadDxfValidateRequest,
    MismatchAcceptanceResponse,
    MismatchAcceptanceUpdate,
)
from app.schemas.project import DxfOutputItem
from app.services import persistence_service, storage_service
from app.services.dxf_service import run_dxf_validate
from app.services.gad_dxf_service import classify_match, is_gad_dxf_name, render_highlight, run_gad_dxf_validation

router = APIRouter(prefix="/dxf", tags=["dxf"])
settings = get_settings()
logger = logging.getLogger(__name__)


@router.post("/{drawing_id}/validate", response_model=list[DxfOutputItem])
def validate_dxf(drawing_id: int, db: Session = Depends(get_db)) -> list[DxfOutputItem]:
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None or drawing.type != DrawingType.DXF:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No DXF drawing with id {drawing_id}.")

    rows = list(db.scalars(select(ExtractionResult).where(ExtractionResult.drawing_id == drawing_id)))
    split_entries = persistence_service.unflatten_dxf_split(rows)
    if not split_entries:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Drawing {drawing_id} has no split files recorded -- run extraction first.",
        )

    split_files = [settings.upload_dir_path / e["path"] for e in split_entries]
    missing = [str(p) for p in split_files if not p.exists()]
    if missing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Split file(s) missing on disk, cannot validate: {', '.join(missing)}",
        )

    outdir = split_files[0].parent
    result = run_dxf_validate(split_files, str(outdir))
    if not result.overlays:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Could not find FULL_BAFFLE/SEGMENTAL_BAFFLE_A/B among the split parts -- nothing to overlay.",
        )

    # Clear any previous overlay rows first so re-clicking Validate doesn't
    # accumulate duplicate OVERLAY.<n> rows under the same drawing_id.
    for row in rows:
        if row.parameter.startswith("OVERLAY."):
            db.delete(row)
    db.commit()

    # Store each overlay in Supabase Storage when configured (survives a
    # host with no persistent disk); fall back to the local relative path
    # otherwise, resolved the same way on every read via resolve_image_url.
    overlays_stored = []
    for label, png_path in result.overlays:
        rel = png_path.relative_to(settings.upload_dir_path).as_posix()
        uploaded_url = storage_service.upload_png(png_path, rel)
        overlays_stored.append((label, uploaded_url or rel))
    persistence_service.save_dxf_outputs(db, drawing.project_id, drawing.id, overlays_stored)

    return [
        DxfOutputItem(label=label, image_url=storage_service.resolve_image_url(stored))
        for label, stored in overlays_stored
    ]


# ---------- GAD DXF vs cutting-sheet DXF validation ----------


def _load_dxf_drawing(db: Session, project_id: int, drawing_id: int, label: str) -> ProjectDrawing:
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None or drawing.is_deleted or drawing.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No active {label} in this project with that id.")
    if drawing.type != DrawingType.DXF:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Drawing {drawing.id} is not a DXF file.")
    if not drawing.is_extracted:
        raise HTTPException(status.HTTP_409_CONFLICT, f"'{drawing.title}' has not finished processing.")
    if not Path(drawing.path).exists():
        raise HTTPException(status.HTTP_409_CONFLICT, f"The file for '{drawing.title}' is missing on disk.")
    return drawing


@router.post("/gad-validate", response_model=GadDxfRunOut)
def gad_dxf_validate(payload: GadDxfValidateRequest, db: Session = Depends(get_db)) -> GadDxfRunOut:
    """Run Comparison tab's "GAD vs DXF" Compare button: runs the capsule/
    baffle pipeline on an uploaded GAD DXF (filename contains "GAD") and an
    uploaded cutting-sheet DXF (any other DXF). Synchronous -- pure geometry,
    no LLM call."""
    if db.get(Project, payload.project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No project with id {payload.project_id}.")

    gad = _load_dxf_drawing(db, payload.project_id, payload.gad_drawing_id, "GAD DXF")
    cutting = _load_dxf_drawing(db, payload.project_id, payload.cutting_drawing_id, "cutting DXF")
    if not is_gad_dxf_name(gad.title):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{gad.title}' is not a GAD DXF (its name must contain 'GAD').")
    if is_gad_dxf_name(cutting.title):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"'{cutting.title}' looks like a GAD DXF; the other file must not have 'GAD' in its name."
        )

    # Each run gets its own folder, so a failed re-run never damages the
    # previous successful one; old runs' folders are removed after the swap.
    runs_root = settings.upload_dir_path / "gad_dxf_runs"
    outdir = runs_root / f"{gad.id}_{cutting.id}_{uuid.uuid4().hex[:8]}"
    old_dirs = [
        settings.upload_dir_path / r.output_dir
        for r in db.scalars(
            select(GadDxfValidationRun).where(
                GadDxfValidationRun.gad_drawing_id == gad.id,
                GadDxfValidationRun.cutting_drawing_id == cutting.id,
            )
        )
    ]

    try:
        result = run_gad_dxf_validation(gad.path, cutting.path, outdir)
    except RuntimeError as exc:
        shutil.rmtree(outdir, ignore_errors=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- surfaced to the client as the error response
        logger.exception("GAD DXF validation failed")
        shutil.rmtree(outdir, ignore_errors=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    overlay_png_path = Path(result.paths["trace_diff_png"])
    overlay_rel = overlay_png_path.relative_to(settings.upload_dir_path).as_posix()
    outdir_rel = outdir.relative_to(settings.upload_dir_path).as_posix()
    # Only the overlay PNG goes to Supabase Storage -- outdir itself (the
    # run's geometry/json files) stays local, so a per-capsule highlight
    # requested after a restart still needs a fresh Compare run to work.
    overlay_uploaded_url = storage_service.upload_png(overlay_png_path, overlay_rel)
    run, _ = persistence_service.save_gad_dxf_validation(
        db, payload.project_id, gad.id, cutting.id, outdir_rel, overlay_uploaded_url or overlay_rel,
        result.merged, classify_match,
    )
    db.commit()
    for d in old_dirs:
        shutil.rmtree(d, ignore_errors=True)

    return persistence_service.gad_dxf_run_to_out(db, run)


@router.get("/gad-validate/{run_id}", response_model=GadDxfRunOut)
def get_gad_dxf_run(run_id: int, db: Session = Depends(get_db)) -> GadDxfRunOut:
    run = db.get(GadDxfValidationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No GAD DXF validation run with id {run_id}.")
    return persistence_service.gad_dxf_run_to_out(db, run)


@router.get("/gad-validate/{run_id}/highlight/{capsule_index}", response_model=GadDxfHighlightResponse)
def highlight_gad_dxf_match(run_id: int, capsule_index: int, db: Session = Depends(get_db)) -> GadDxfHighlightResponse:
    """The overlay image with one capsule (and its matched hole) ringed in
    yellow. Rendered on first request and cached next to the run's other files."""
    run = db.get(GadDxfValidationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No GAD DXF validation run with id {run_id}.")
    exists = db.scalar(
        select(GadDxfValidationMatch.id).where(
            GadDxfValidationMatch.run_id == run_id, GadDxfValidationMatch.capsule_index == capsule_index
        )
    )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} has no capsule #{capsule_index}.")

    outdir = settings.upload_dir_path / run.output_dir
    out_png = outdir / f"highlight_{capsule_index}.png"
    rel = out_png.relative_to(settings.upload_dir_path).as_posix()
    if not out_png.exists():
        try:
            render_highlight(outdir, capsule_index, out_png)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GAD DXF highlight render failed")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Could not render highlight: {exc}") from exc
        uploaded_url = storage_service.upload_png(out_png, rel)
        if uploaded_url:
            return GadDxfHighlightResponse(image_url=uploaded_url)
    return GadDxfHighlightResponse(image_url=storage_service.resolve_image_url(rel))


@router.patch("/gad-validate/matches/{match_id}", response_model=MismatchAcceptanceResponse)
def update_gad_dxf_match_acceptance(
    match_id: int, payload: MismatchAcceptanceUpdate, db: Session = Depends(get_db)
) -> MismatchAcceptanceResponse:
    row = db.get(GadDxfValidationMatch, match_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No GAD DXF validation match with id {match_id}.")
    row.mismatch_acceptance = payload.mismatch_acceptance
    db.commit()
    return MismatchAcceptanceResponse(id=row.id, mismatch_acceptance=row.mismatch_acceptance)
