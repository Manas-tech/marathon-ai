"""
Runs the full GAD-vs-sub-drawings pipeline synchronously and returns the
result directly -- no job row, no polling. The request handler that calls
these functions blocks until they return.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.project_drawing import DrawingType, ProjectDrawing
from app.services.pdf_service import render_pdf_to_images
from app.services.extraction_service import extract_drawing, extract_drawing_multi_page
from app.services.comparison_service import compare, compare_jobcard_to_sub
from app.services.jobcard_service import extract_job_card
from app.services.dxf_service import run_dxf_split, run_dxf_validate
from app.services.gad_dxf_service import is_gad_dxf_name
from app.services.usage_service import UsageTracker
from app.services import persistence_service
from app.schemas.drawing import ComparisonReport
from app.schemas.job import (
    ComparisonReportOut,
    FindingOut,
    JobCardComparisonReportOut,
    JobCardFindingOut,
    PartComparisonOut,
)

settings = get_settings()
logger = logging.getLogger(__name__)


def _attach_result_ids(
    report: ComparisonReport,
    saved_rows: list,
    drawing_id_by_sub_filename: dict[str, int],
) -> ComparisonReportOut:
    """Pairs each Finding with the ComparisonResult row persistence_service.
    save_comparison() just created for it, so the client gets back a row id
    (and current mismatch_acceptance) to drive the accept-mismatch checkbox.
    Walks report.parts/findings in the exact order save_comparison() does --
    skipping parts with no matched drawing, which never got a row -- so
    saved_rows lines up positionally with what's consumed here."""
    rows_iter = iter(saved_rows)
    parts_out = []
    for part in report.parts:
        was_saved = part.sub_drawing_file in drawing_id_by_sub_filename
        findings_out = []
        for finding in part.findings:
            row = next(rows_iter) if was_saved else None
            findings_out.append(
                FindingOut(
                    id=row.id if row else None,
                    parameter=finding.parameter,
                    gad_value=finding.gad_value,
                    sub_value=finding.sub_value,
                    status=finding.status,
                    mismatch_acceptance=row.mismatch_acceptance if row else False,
                )
            )
        parts_out.append(
            PartComparisonOut(
                part_name=part.part_name,
                sub_drawing_file=part.sub_drawing_file,
                matched_bom_row=part.matched_bom_row,
                overall_status=part.overall_status,
                findings=findings_out,
            )
        )
    return ComparisonReportOut(gad_file=report.gad_file, gad_drawing_no=report.gad_drawing_no, parts=parts_out)


def run_comparison(
    gad_path: str,
    sub_paths: list[str],
    work_dir: str,
    project_id: int | None = None,
    job_card_path: str | None = None,
    dxf_path: str | None = None,
) -> tuple[ComparisonReportOut, dict]:
    """Upload GAD + subs fresh, extract them, then compare. Returns
    (result, usage_totals); raises on failure -- the caller (router) is
    responsible for turning that into an HTTP error response.

    project_id: which PROJECTS row to attach the uploaded drawings/results
    to (the project the user had selected in the UI). Falls back to the
    auto-created default project if not given.

    job_card_path: optional job-card PDF, extracted and saved independently
    of the GAD-vs-sub comparison (extraction only, no comparison -- see
    jobcard_service.py). A failure extracting it does not fail the overall
    call, since the comparison is the primary deliverable.

    dxf_path: optional DXF sheet, split + overlaid independently of the
    comparison (see dxf_service.py). Also non-fatal on failure."""
    db = SessionLocal()
    usage = UsageTracker()
    try:
        project = persistence_service.get_project_or_default(db, project_id)

        gad_images = render_pdf_to_images(gad_path, dpi=settings.RENDER_DPI)
        gad_extraction = extract_drawing_multi_page(
            gad_images,
            source_label=Path(gad_path).name,
            model=settings.GEMINI_COMPLEX_MODEL,
            usage_tracker=usage,
        )

        gad_drawing = persistence_service.save_drawing(
            db,
            project_id=project.id,
            title=Path(gad_path).name,
            path=gad_path,
            drawing_type=DrawingType.GAD,
        )
        persistence_service.save_extraction(db, project.id, gad_drawing.id, gad_extraction)

        sub_extractions = []
        drawing_id_by_sub_filename: dict[str, int] = {}
        for sub_path in sub_paths:
            sub_name = Path(sub_path).name
            sub_images = render_pdf_to_images(sub_path, dpi=settings.RENDER_DPI)
            sub_ext = extract_drawing(
                sub_images, source_label=sub_name, model=settings.GEMINI_MODEL, usage_tracker=usage
            )
            sub_extractions.append(sub_ext)

            sub_drawing = persistence_service.save_drawing(
                db,
                project_id=project.id,
                title=sub_name,
                path=sub_path,
                drawing_type=DrawingType.SUB,
            )
            persistence_service.save_extraction(db, project.id, sub_drawing.id, sub_ext)
            drawing_id_by_sub_filename[sub_name] = sub_drawing.id

        if job_card_path:
            job_card_name = Path(job_card_path).name
            try:
                job_card_ext = extract_job_card(
                    job_card_path, source_label=job_card_name, model=settings.GEMINI_MODEL, usage_tracker=usage
                )
                job_card_drawing = persistence_service.save_drawing(
                    db,
                    project_id=project.id,
                    title=job_card_name,
                    path=job_card_path,
                    drawing_type=DrawingType.JOB_CARD,
                )
                persistence_service.save_jobcard_extraction(db, project.id, job_card_drawing.id, job_card_ext)
            except Exception:
                logger.exception("job card extraction failed, continuing without it")

        if dxf_path:
            dxf_name = Path(dxf_path).name
            try:
                dxf_outdir = Path(work_dir) / "dxf_output"
                split_files = run_dxf_split(dxf_path, str(dxf_outdir))
                dxf_result = run_dxf_validate(split_files, str(dxf_outdir))

                dxf_drawing = persistence_service.save_drawing(
                    db,
                    project_id=project.id,
                    title=dxf_name,
                    path=dxf_path,
                    drawing_type=DrawingType.DXF,
                )
                overlays_rel = [
                    (label, png_path.relative_to(settings.upload_dir_path).as_posix())
                    for label, png_path in dxf_result.overlays
                ]
                persistence_service.save_dxf_outputs(db, project.id, dxf_drawing.id, overlays_rel)
            except Exception:
                logger.exception("DXF processing failed, continuing without it")

        result = compare(
            gad_extraction, sub_extractions, model=settings.GEMINI_COMPLEX_MODEL, usage_tracker=usage
        )
        saved_rows = persistence_service.save_comparison(
            db, project.id, gad_drawing.id, result, drawing_id_by_sub_filename
        )
        db.commit()
        return _attach_result_ids(result, saved_rows, drawing_id_by_sub_filename), usage.to_totals()

    finally:
        db.close()
        # Only the rendered page PNGs are disposable scratch space -- the
        # original uploaded PDFs must survive, since project_drawings.path
        # (MySQL) points at them for later viewing.
        shutil.rmtree(Path(work_dir) / "_rendered", ignore_errors=True)


def run_single_drawing_extraction(drawing_id: int, path: str, drawing_type: int, work_dir: str) -> None:
    """Upload tab's per-file auto-extraction: the drawing row already exists
    (persistence_service.save_drawing() creates it with is_extracted=False
    the moment the file is saved to disk, before this background task even
    starts -- see routers/drawings.py) -- this just runs the type-specific
    extraction/split step and flips is_extracted via
    persistence_service.mark_drawing_extracted() on success or failure.

    One drawing per call (not a batch): each Upload-tab card auto-extracts
    the instant its own file is selected, independently of every other
    card, so uploading a second file never re-triggers extraction for a
    card that already finished -- that was the bug in the old shared
    "Run extraction" button, which resubmitted every card's current file
    together on every click."""
    db = SessionLocal()
    usage = UsageTracker()
    try:
        drawing = db.get(ProjectDrawing, drawing_id)
        if drawing is None:
            logger.error("Drawing %s vanished before extraction started", drawing_id)
            return

        project_id, title = drawing.project_id, drawing.title

        if drawing_type == DrawingType.GAD:
            images = render_pdf_to_images(path, dpi=settings.RENDER_DPI)
            ext = extract_drawing_multi_page(
                images, source_label=title, model=settings.GEMINI_COMPLEX_MODEL, usage_tracker=usage
            )
            persistence_service.save_extraction(db, project_id, drawing_id, ext)

        elif drawing_type == DrawingType.SUB:
            images = render_pdf_to_images(path, dpi=settings.RENDER_DPI)
            ext = extract_drawing(images, source_label=title, model=settings.GEMINI_MODEL, usage_tracker=usage)
            persistence_service.save_extraction(db, project_id, drawing_id, ext)

        elif drawing_type == DrawingType.JOB_CARD:
            ext = extract_job_card(path, source_label=title, model=settings.GEMINI_MODEL, usage_tracker=usage)
            persistence_service.save_jobcard_extraction(db, project_id, drawing_id, ext)

        elif drawing_type == DrawingType.DXF and is_gad_dxf_name(title):
            # GAD DXF: nothing to split or extract up front -- the GAD-vs-DXF
            # validation reads the raw file at compare time. Just confirm it
            # is a readable DXF so a corrupt upload fails here, not later.
            import ezdxf

            try:
                ezdxf.readfile(path)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Could not read this GAD DXF: {exc}") from exc

        elif drawing_type == DrawingType.DXF:
            outdir = Path(work_dir) / "dxf_output"
            split_files = run_dxf_split(path, str(outdir))
            if not split_files:
                raise RuntimeError("No geometry entities found in the DXF sheet's modelspace.")
            persistence_service.save_dxf_split(db, project_id, drawing_id, split_files)

        else:
            raise ValueError(f"Unknown drawing_type {drawing_type}.")

        persistence_service.mark_drawing_extracted(db, drawing_id)
        logger.info("Drawing %s extracted successfully", drawing_id)

    except Exception as exc:  # noqa: BLE001 -- surfaced to the client via extraction_error
        logger.exception("Drawing %s extraction failed", drawing_id)
        persistence_service.mark_drawing_extracted(db, drawing_id, error=str(exc))
    finally:
        db.close()
        shutil.rmtree(Path(work_dir) / "_rendered", ignore_errors=True)


def run_comparison_from_extraction(
    project_id: int, gad_drawing_id: int, sub_drawing_ids: list[int]
) -> tuple[ComparisonReportOut, dict]:
    """Run Comparison tab's "Compare GAD vs Sub" button: GAD and subs were
    already extracted by an earlier per-file upload (see
    run_single_drawing_extraction), so their DrawingExtraction is
    reconstructed from the stored EXTRACTION_RESULTS rows instead of
    re-rendering/re-calling Gemini. Returns (result, usage_totals); raises
    on failure -- the caller (router) turns that into an HTTP error."""
    from sqlalchemy import select
    from app.models.extraction_result import ExtractionResult
    from app.models.project_drawing import ProjectDrawing

    db = SessionLocal()
    usage = UsageTracker()
    try:
        def _load_extraction(drawing_id: int):
            drawing = db.get(ProjectDrawing, drawing_id)
            if drawing is None:
                raise ValueError(f"No drawing with id {drawing_id}.")
            rows = list(db.scalars(select(ExtractionResult).where(ExtractionResult.drawing_id == drawing_id)))
            if not rows:
                raise ValueError(f"Drawing '{drawing.title}' (id {drawing_id}) has no stored extraction.")
            return drawing, persistence_service.rebuild_drawing_extraction(drawing.title, rows)

        gad_drawing, gad_extraction = _load_extraction(gad_drawing_id)

        sub_extractions = []
        drawing_id_by_sub_filename: dict[str, int] = {}
        for sub_id in sub_drawing_ids:
            sub_drawing, sub_ext = _load_extraction(sub_id)
            sub_extractions.append(sub_ext)
            drawing_id_by_sub_filename[sub_drawing.title] = sub_drawing.id

        result = compare(gad_extraction, sub_extractions, model=settings.GEMINI_COMPLEX_MODEL, usage_tracker=usage)
        saved_rows = persistence_service.save_comparison(db, project_id, gad_drawing.id, result, drawing_id_by_sub_filename)
        db.commit()
        return _attach_result_ids(result, saved_rows, drawing_id_by_sub_filename), usage.to_totals()

    finally:
        db.close()


def run_jobcard_vs_sub(
    project_id: int, job_card_drawing_id: int, sub_drawing_id: int
) -> tuple[JobCardComparisonReportOut, dict]:
    """Run Comparison tab's job card "Compare" button: both the job card and
    the sub-drawing were already extracted by earlier per-file uploads, so
    both extractions are rebuilt from the stored EXTRACTION_RESULTS rows (no
    re-render, no re-extract) and checked against each other with one Gemini
    call. Returns (result, usage_totals); raises on failure -- the caller
    (router) turns that into an HTTP error."""
    from sqlalchemy import select
    from app.models.extraction_result import ExtractionResult

    db = SessionLocal()
    usage = UsageTracker()
    try:
        def _rows(drawing_id: int):
            return list(db.scalars(select(ExtractionResult).where(ExtractionResult.drawing_id == drawing_id)))

        job_card_drawing = db.get(ProjectDrawing, job_card_drawing_id)
        sub_drawing = db.get(ProjectDrawing, sub_drawing_id)
        if job_card_drawing is None or sub_drawing is None:
            raise ValueError("Job card or sub-drawing no longer exists.")

        jc_rows, sub_rows = _rows(job_card_drawing_id), _rows(sub_drawing_id)
        if not jc_rows:
            raise ValueError(f"Job card '{job_card_drawing.title}' has no stored extraction.")
        if not sub_rows:
            raise ValueError(f"Drawing '{sub_drawing.title}' has no stored extraction.")

        job_card_ext = persistence_service.unflatten_jobcard_extraction(jc_rows)
        job_card_ext.source_file = job_card_drawing.title
        sub_ext = persistence_service.rebuild_drawing_extraction(sub_drawing.title, sub_rows)

        report = compare_jobcard_to_sub(
            job_card_ext, sub_ext, model=settings.GEMINI_COMPLEX_MODEL, usage_tracker=usage
        )
        saved = persistence_service.save_jobcard_comparison(
            db, project_id, sub_drawing_id, job_card_drawing_id, report
        )
        db.commit()

        result = JobCardComparisonReportOut(
            job_card_file=job_card_drawing.title,
            sub_drawing_file=sub_drawing.title,
            overall_status=report.overall_status,
            findings=[
                JobCardFindingOut(
                    id=row.id,
                    parameter=row.parameter,
                    jobcard_value=row.jobcard_value,
                    sub_value=row.subd_value,
                    status=row.status,
                    mismatch_acceptance=row.mismatch_acceptance,
                )
                for row in saved
            ],
        )
        return result, usage.to_totals()

    finally:
        db.close()
