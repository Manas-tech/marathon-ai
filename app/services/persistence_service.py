"""
Persists a pipeline run's extraction + comparison results into MySQL, per
the PROJECTS / PROJECT_DRAWINGS / EXTRACTION_RESULTS / COMPARISON_RESULTS
schema in `Marathon Drawing Comparator(DB_PLAN).csv`.

There is no project-management API yet, so every run is attached to a
single default project (get_or_create_default_project) rather than one
created per upload.

`DrawingExtraction` is a nested Pydantic model (title_block, a list of
part_specifications, a list of BOM rows, a list of dimensional_callouts, a
list of notes). EXTRACTION_RESULTS only has flat (parameter, value) text
columns and no raw-JSON column by design (so every field stays indexable/
queryable in SQL) -- flatten_extraction() turns the nested structure into
one row per leaf field using a dotted-key scheme:

    TITLE_BLOCK.DRAWING_NO / .TITLE / .CUSTOMER / .REVISION
    DRAWING_CATEGORY
    SPEC.<parameter as extracted>                  (from part_specifications)
    BOM.<n>.SR_NO / .DESCRIPTION / .QTY / .MATERIAL / .SIZE / .REMARKS / .BOM_TABLE
    CALLOUT.<n>.LABEL / .CATEGORY
    NOTE.<n>

where <n> is the 1-based position in that list, so rows from different BOM
tables or repeated callouts never collide.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from app.models.comparison_result import ComparisonResult
from app.models.extraction_result import ExtractionResult
from app.models.gad_dxf_validation import GadDxfValidationMatch, GadDxfValidationRun
from app.models.jobcard_comparison_result import JobCardComparisonResult
from app.models.project import Project
from app.models.project_drawing import ProjectDrawing
from app.services import storage_service
from app.schemas.drawing import ComparisonReport, DrawingExtraction
from app.schemas.job import GadDxfMatchOut, GadDxfRunOut
from app.schemas.jobcard import JobCardComparisonReport, JobCardDocumentMeta, JobCardExtraction, JobCardParameter

DEFAULT_PROJECT_NAME = "Default Project"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_default_project(db: Session) -> Project:
    """Fallback used when a run isn't attached to a specific project (no
    project_id passed) -- kept for backwards compatibility now that real
    project selection (get_project_or_default) exists."""
    project = db.query(Project).filter(
        Project.name == DEFAULT_PROJECT_NAME, Project.is_deleted.is_(False)
    ).first()
    if project is None:
        project = Project(name=DEFAULT_PROJECT_NAME, description="Auto-created default project")
        db.add(project)
        db.commit()
        db.refresh(project)
    return project


def get_project_or_default(db: Session, project_id: int | None) -> Project:
    """Resolves the project a new job's drawings/results should be attached
    to: the explicitly selected project if given, else the default project."""
    if project_id is None:
        return get_or_create_default_project(db)
    project = db.get(Project, project_id)
    if project is None or project.is_deleted:
        raise ValueError(f"No project with id {project_id}.")
    return project


def save_drawing(db: Session, project_id: int, title: str, path: str, drawing_type: int) -> ProjectDrawing:
    """Always inserts a fresh row -- filename is NOT a uniqueness key.
    Uploading the same GAD/sub/job-card/DXF filename twice in the same
    project is legitimate (e.g. re-checking an unchanged file, or two
    genuinely different files that happen to share a name) and must not
    collapse into "one" drawing keyed by title; every upload gets its own
    independent id.

    Correctness for "which exact drawing is this comparison/validation
    using" comes entirely from drawing_id, not title matching -- the
    frontend tracks the specific id returned by THIS upload
    (window.SessionUploads in upload.js) and every downstream call
    (compare-from-extraction, dxf validate, job-card display) takes that id
    explicitly, so two same-titled drawings never get confused with each
    other. is_deleted/soft-delete still exists, just user-triggered only
    (the Upload tab's "x" button -> soft_delete_drawing()), not an automatic
    side effect of uploading a same-named file again."""
    drawing = ProjectDrawing(project_id=project_id, title=title, path=path, type=drawing_type)
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return drawing


def soft_delete_drawing(db: Session, drawing_id: int) -> ProjectDrawing | None:
    """The Upload tab's per-file "x" button: marks a drawing (and thus every
    view/listing that filters is_deleted) as removed without touching its
    extraction_results/comparison_results rows or the file on disk -- purely
    a visibility flag, reversible in principle, safe to call even while a
    background extraction for this same drawing_id is still in flight (that
    task's eventual mark_drawing_extracted() call just writes onto an
    already-hidden row, harmless)."""
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None or drawing.is_deleted:
        return drawing
    drawing.is_deleted = True
    drawing.deleted_at = _utcnow()
    db.add(drawing)
    db.commit()
    db.refresh(drawing)
    return drawing


def mark_drawing_extracted(db: Session, drawing_id: int, error: str | None = None) -> None:
    """Flips is_extracted once the background extraction step for this
    specific drawing finishes -- True/cleared error on success, False with
    extraction_error set on failure. Called from
    pipeline_service.run_single_drawing_extraction() after
    save_extraction()/save_jobcard_extraction()/save_dxf_split() succeed (or
    from its except block if they raise)."""
    drawing = db.get(ProjectDrawing, drawing_id)
    if drawing is None:
        return
    drawing.is_extracted = error is None
    drawing.extraction_error = error
    db.add(drawing)
    db.commit()


def flatten_extraction(extraction: DrawingExtraction) -> List[Tuple[str, str]]:
    """DrawingExtraction -> list of (parameter, value) pairs. See module docstring for the key scheme."""
    rows: List[Tuple[str, str]] = []

    rows.append(("DRAWING_CATEGORY", extraction.drawing_category))

    tb = extraction.title_block
    rows.append(("TITLE_BLOCK.DRAWING_NO", tb.drawing_no))
    rows.append(("TITLE_BLOCK.TITLE", tb.title))
    rows.append(("TITLE_BLOCK.CUSTOMER", tb.customer))
    rows.append(("TITLE_BLOCK.REVISION", tb.revision))

    for spec in extraction.part_specifications:
        rows.append((f"SPEC.{spec.parameter}", spec.value))

    for i, bom in enumerate(extraction.bill_of_materials, start=1):
        rows.append((f"BOM.{i}.SR_NO", bom.sr_no))
        rows.append((f"BOM.{i}.DESCRIPTION", bom.description))
        rows.append((f"BOM.{i}.QTY", bom.qty))
        rows.append((f"BOM.{i}.MATERIAL", bom.material))
        rows.append((f"BOM.{i}.SIZE", bom.size))
        rows.append((f"BOM.{i}.REMARKS", bom.remarks))
        rows.append((f"BOM.{i}.BOM_TABLE", bom.bom_table))

    for i, callout in enumerate(extraction.dimensional_callouts, start=1):
        rows.append((f"CALLOUT.{i}.LABEL", callout.label))
        rows.append((f"CALLOUT.{i}.CATEGORY", callout.category))

    for i, note in enumerate(extraction.notes, start=1):
        rows.append((f"NOTE.{i}", note))

    # Skip parameters with no actual value -- nothing to query/index.
    return [(p, v) for p, v in rows if v]


def _save_extraction_rows(db: Session, project_id: int, drawing_id: int, rows: List[Tuple[str, str]]) -> None:
    db.bulk_save_objects(
        [
            ExtractionResult(project_id=project_id, drawing_id=drawing_id, parameter=parameter, value=value)
            for parameter, value in rows
        ]
    )
    db.commit()


def save_extraction(db: Session, project_id: int, drawing_id: int, extraction: DrawingExtraction) -> None:
    _save_extraction_rows(db, project_id, drawing_id, flatten_extraction(extraction))


def flatten_jobcard_extraction(extraction: JobCardExtraction) -> List[Tuple[str, str]]:
    """JobCardExtraction -> list of (parameter, value) pairs, same dotted-key idea as
    flatten_extraction() above but for the job-card schema (app/schemas/jobcard.py):

        DOC_META.WORK_ORDER_OR_DRAWING_NUMBER / .REVISION / .DATE / .CUSTOMER_OR_CLIENT / .PREPARED_BY / .CHECKED_BY
        PARAM.<n>.PARAMETER / .VALUE / .UNIT / .CATEGORY / .SECTION
        NOTE.<n>

    Reuses the same EXTRACTION_RESULTS table as drawings -- a job card is just
    another drawing with a flatter extraction shape (no BOM/callouts)."""
    rows: List[Tuple[str, str]] = []

    dm = extraction.document_meta
    rows.append(("DOC_META.WORK_ORDER_OR_DRAWING_NUMBER", dm.work_order_or_drawing_number))
    rows.append(("DOC_META.REVISION", dm.revision))
    rows.append(("DOC_META.DATE", dm.date))
    rows.append(("DOC_META.CUSTOMER_OR_CLIENT", dm.customer_or_client))
    rows.append(("DOC_META.PREPARED_BY", dm.prepared_by))
    rows.append(("DOC_META.CHECKED_BY", dm.checked_by))

    for i, param in enumerate(extraction.parameters, start=1):
        rows.append((f"PARAM.{i}.PARAMETER", param.parameter))
        rows.append((f"PARAM.{i}.VALUE", param.value))
        rows.append((f"PARAM.{i}.UNIT", param.unit))
        rows.append((f"PARAM.{i}.CATEGORY", param.category))
        rows.append((f"PARAM.{i}.SECTION", param.table_or_section))

    for i, note in enumerate(extraction.notes_and_flags, start=1):
        rows.append((f"NOTE.{i}", note))

    return [(p, v) for p, v in rows if v]


def save_jobcard_extraction(db: Session, project_id: int, drawing_id: int, extraction: JobCardExtraction) -> None:
    _save_extraction_rows(db, project_id, drawing_id, flatten_jobcard_extraction(extraction))


def save_dxf_outputs(
    db: Session,
    project_id: int,
    drawing_id: int,
    overlays: List[Tuple[str, str]],
) -> None:
    """Stores the DXF pipeline's overlay PNG outputs against the DXF's own
    drawing_id, same EXTRACTION_RESULTS table as everything else (a DXF
    "extraction" is a set of generated images rather than parsed text, but
    the (project_id, drawing_id, parameter, value) shape still fits):

        OVERLAY.<n>.LABEL   e.g. "SEGMENTAL_BAFFLE_A_vs_FULL_BAFFLE"
        OVERLAY.<n>.PATH    a Supabase Storage URL when storage_service is
                            configured, otherwise a path relative to
                            UPLOAD_DIR servable via the /uploads static
                            mount -- resolve either via
                            storage_service.resolve_image_url().
    """
    rows: List[Tuple[str, str]] = []
    for i, (label, rel_path) in enumerate(overlays, start=1):
        rows.append((f"OVERLAY.{i}.LABEL", label))
        rows.append((f"OVERLAY.{i}.PATH", rel_path))
    if rows:
        _save_extraction_rows(db, project_id, drawing_id, rows)


_DXF_OVERLAY_KEY = re.compile(r"^OVERLAY\.(\d+)\.(.+)$")


def unflatten_dxf_outputs(rows: List[ExtractionResult]) -> List[Dict[str, str]]:
    """Inverse of save_dxf_outputs(): -> [{"label": ..., "path": ...}, ...]."""
    overlays_by_index: Dict[int, Dict[str, str]] = {}
    for row in rows:
        if (m := _DXF_OVERLAY_KEY.match(row.parameter)):
            idx, field = int(m.group(1)), m.group(2).lower()
            overlays_by_index.setdefault(idx, {})[field] = row.value
    return [overlays_by_index[i] for i in sorted(overlays_by_index)]


def save_dxf_split(db: Session, project_id: int, drawing_id: int, split_files: List) -> None:
    """Stores the split-stage's sub-part .dxf file paths so a later
    run_dxf_validate() call (see routers/dxf.py) can find them again without
    re-splitting: SPLIT.<n>.NAME / .PATH (path relative to UPLOAD_DIR, same
    convention as save_dxf_outputs)."""
    from app.core.config import get_settings

    upload_dir = get_settings().upload_dir_path
    rows: List[Tuple[str, str]] = []
    for i, path in enumerate(split_files, start=1):
        rows.append((f"SPLIT.{i}.NAME", path.stem))
        rows.append((f"SPLIT.{i}.PATH", path.relative_to(upload_dir).as_posix()))
    if rows:
        _save_extraction_rows(db, project_id, drawing_id, rows)


_DXF_SPLIT_KEY = re.compile(r"^SPLIT\.(\d+)\.(.+)$")


def unflatten_dxf_split(rows: List[ExtractionResult]) -> List[Dict[str, str]]:
    """Inverse of save_dxf_split(): -> [{"name": ..., "path": ...}, ...]."""
    split_by_index: Dict[int, Dict[str, str]] = {}
    for row in rows:
        if (m := _DXF_SPLIT_KEY.match(row.parameter)):
            idx, field = int(m.group(1)), m.group(2).lower()
            split_by_index.setdefault(idx, {})[field] = row.value
    return [split_by_index[i] for i in sorted(split_by_index)]


def save_comparison(
    db: Session,
    project_id: int,
    gad_drawing_id: int,
    report: ComparisonReport,
    drawing_id_by_sub_filename: Dict[str, int],
) -> list[ComparisonResult]:
    """One ComparisonResult row per Finding, attached to the matched sub-drawing's
    PROJECT_DRAWINGS row. gad_drawing_id records which GAD drawing produced this
    comparison, set explicitly here (not inferred later) since a project can
    accumulate multiple GAD revisions/jobs over time. Parts that couldn't be
    matched to a BOM row (no findings) are skipped -- there's nothing to store
    per the COMPARISON_RESULTS schema.

    Returns the created rows (ids populated via flush) in the same order as
    report.parts/findings, minus skipped parts -- callers use this to report
    each finding's row id back to the client for the mismatch-acceptance
    checkbox."""
    objects = []
    for part in report.parts:
        drawing_id = drawing_id_by_sub_filename.get(part.sub_drawing_file)
        if drawing_id is None:
            continue
        for finding in part.findings:
            objects.append(
                ComparisonResult(
                    project_id=project_id,
                    drawing_id=drawing_id,
                    gad_drawing_id=gad_drawing_id,
                    parameter=finding.parameter,
                    gad_value=finding.gad_value,
                    subd_value=finding.sub_value,
                    status=finding.status,
                    mismatch_acceptance=False,
                )
            )
    if objects:
        db.add_all(objects)
        db.flush()
    return objects


def save_jobcard_comparison(
    db: Session,
    project_id: int,
    sub_drawing_id: int,
    job_card_drawing_id: int,
    report: JobCardComparisonReport,
) -> list[JobCardComparisonResult]:
    """One JobCardComparisonResult row per Finding. Returns the created rows
    (ids populated via flush) in finding order, so the caller can hand each
    row id back to the client for the mismatch-acceptance checkbox.

    Re-comparing the same (sub, job card) pair replaces that pair's earlier
    rows, so Browse always shows one current result instead of stacked
    duplicates from every click."""
    db.query(JobCardComparisonResult).filter(
        JobCardComparisonResult.sub_drawing_id == sub_drawing_id,
        JobCardComparisonResult.job_card_drawing_id == job_card_drawing_id,
    ).delete(synchronize_session=False)

    objects = [
        JobCardComparisonResult(
            project_id=project_id,
            sub_drawing_id=sub_drawing_id,
            job_card_drawing_id=job_card_drawing_id,
            parameter=f.parameter,
            jobcard_value=f.jobcard_value,
            subd_value=f.sub_value,
            status=f.status,
            mismatch_acceptance=False,
        )
        for f in report.findings
    ]
    if objects:
        db.add_all(objects)
        db.flush()
    return objects


def save_gad_dxf_validation(
    db: Session,
    project_id: int,
    gad_drawing_id: int,
    cutting_drawing_id: int,
    output_dir_rel: str,
    overlay_rel: str,
    merged: dict,
    radius_match,
) -> tuple[GadDxfValidationRun, list[GadDxfValidationMatch]]:
    """Persists one GAD-DXF-vs-cutting-DXF run (see models/gad_dxf_validation.py)
    from the pipeline's merged match JSON. radius_match(capsule_radius,
    hole_radius) -> "MATCH"/"MISMATCH" is passed in so this module doesn't
    import the geometry service. Re-validating the same (gad, cutting) pair
    replaces the earlier run and its match rows."""
    old_runs = db.query(GadDxfValidationRun).filter(
        GadDxfValidationRun.gad_drawing_id == gad_drawing_id,
        GadDxfValidationRun.cutting_drawing_id == cutting_drawing_id,
    ).all()
    for old in old_runs:
        db.query(GadDxfValidationMatch).filter(GadDxfValidationMatch.run_id == old.id).delete(
            synchronize_session=False
        )
        db.delete(old)
    db.flush()

    size = merged.get("capsule_size") or {}
    run = GadDxfValidationRun(
        project_id=project_id,
        gad_drawing_id=gad_drawing_id,
        cutting_drawing_id=cutting_drawing_id,
        output_dir=output_dir_rel,
        overlay_path=overlay_rel,
        outer_radius_gad=merged.get("outer_radius_gad"),
        outer_radius_baffle=merged.get("outer_radius_baffle"),
        outer_radius_diff=merged.get("outer_radius_diff"),
        capsule_count=merged.get("capsule_count") or 0,
        hole_count=merged.get("hole_count") or 0,
        count_diff=merged.get("count_diff") or 0,
        mean_center_offset=merged.get("mean_center_offset"),
        max_center_offset=merged.get("max_center_offset"),
        capsule_width=size.get("width"),
        capsule_overall_length=size.get("overall_length"),
    )
    db.add(run)
    db.flush()

    matches = []
    for m in merged.get("matches", []):
        cap_c = m.get("capsule_center") or [None, None]
        hole_c = m.get("nearest_hole_center") or [None, None]
        capsule_radius, hole_radius = m.get("capsule_radius"), m.get("nearest_hole_radius")
        matches.append(
            GadDxfValidationMatch(
                run_id=run.id,
                capsule_index=m["capsule_index"],
                capsule_center_x=cap_c[0],
                capsule_center_y=cap_c[1],
                hole_center_x=hole_c[0],
                hole_center_y=hole_c[1],
                capsule_radius=capsule_radius,
                hole_radius=hole_radius,
                center_offset=m.get("center_offset"),
                status=radius_match(capsule_radius, hole_radius),
                mismatch_acceptance=False,
            )
        )
    if matches:
        db.add_all(matches)
        db.flush()
    return run, matches


def gad_dxf_run_to_out(db: Session, run: GadDxfValidationRun) -> GadDxfRunOut:
    """API view of a stored GAD DXF validation run (summary + every match)."""
    match_rows = list(
        db.query(GadDxfValidationMatch)
        .filter(GadDxfValidationMatch.run_id == run.id)
        .order_by(GadDxfValidationMatch.capsule_index)
    )
    gad = db.get(ProjectDrawing, run.gad_drawing_id)
    cutting = db.get(ProjectDrawing, run.cutting_drawing_id)

    def _pair(x, y):
        return [] if x is None or y is None else [x, y]

    matches = [
        GadDxfMatchOut(
            id=m.id,
            capsule_index=m.capsule_index,
            capsule_center=_pair(m.capsule_center_x, m.capsule_center_y),
            hole_center=_pair(m.hole_center_x, m.hole_center_y),
            capsule_radius=m.capsule_radius,
            hole_radius=m.hole_radius,
            center_offset=m.center_offset,
            status=m.status,
            mismatch_acceptance=m.mismatch_acceptance,
        )
        for m in match_rows
    ]
    return GadDxfRunOut(
        id=run.id,
        gad_drawing_id=run.gad_drawing_id,
        cutting_drawing_id=run.cutting_drawing_id,
        gad_file=gad.title if gad else "",
        cutting_file=cutting.title if cutting else "",
        overlay_url=storage_service.resolve_image_url(run.overlay_path),
        outer_radius_gad=run.outer_radius_gad,
        outer_radius_baffle=run.outer_radius_baffle,
        outer_radius_diff=run.outer_radius_diff,
        capsule_count=run.capsule_count,
        hole_count=run.hole_count,
        count_diff=run.count_diff,
        mean_center_offset=run.mean_center_offset,
        max_center_offset=run.max_center_offset,
        capsule_width=run.capsule_width,
        capsule_overall_length=run.capsule_overall_length,
        mismatch_count=sum(1 for m in matches if m.status == "MISMATCH" and not m.mismatch_acceptance),
        matches=matches,
    )


_BOM_KEY = re.compile(r"^BOM\.(\d+)\.(.+)$")
_CALLOUT_KEY = re.compile(r"^CALLOUT\.(\d+)\.(.+)$")
_NOTE_KEY = re.compile(r"^NOTE\.(\d+)$")


def unflatten_extraction(rows: List[ExtractionResult]) -> Dict:
    """Inverse of flatten_extraction(): reassembles the flat (parameter, value)
    rows stored in EXTRACTION_RESULTS back into the nested shape the UI wants
    to render (title block, spec list, BOM table, callouts, notes) -- kept
    separate from DrawingExtraction since it's display-only, not validated
    against the Gemini response_schema."""
    drawing_category = ""
    title_block: Dict[str, str] = {}
    specifications: List[Dict[str, str]] = []
    bom_by_index: Dict[int, Dict[str, str]] = {}
    callout_by_index: Dict[int, Dict[str, str]] = {}
    notes_by_index: Dict[int, str] = {}

    for row in rows:
        param, value = row.parameter, row.value

        if param == "DRAWING_CATEGORY":
            drawing_category = value
        elif param.startswith("TITLE_BLOCK."):
            title_block[param.removeprefix("TITLE_BLOCK.").lower()] = value
        elif param.startswith("SPEC."):
            specifications.append({"parameter": param.removeprefix("SPEC."), "value": value})
        elif (m := _BOM_KEY.match(param)):
            idx, field = int(m.group(1)), m.group(2).lower()
            bom_by_index.setdefault(idx, {})[field] = value
        elif (m := _CALLOUT_KEY.match(param)):
            idx, field = int(m.group(1)), m.group(2).lower()
            callout_by_index.setdefault(idx, {})[field] = value
        elif (m := _NOTE_KEY.match(param)):
            notes_by_index[int(m.group(1))] = value

    return {
        "drawing_category": drawing_category,
        "title_block": title_block,
        "specifications": specifications,
        "bill_of_materials": [bom_by_index[i] for i in sorted(bom_by_index)],
        "dimensional_callouts": [callout_by_index[i] for i in sorted(callout_by_index)],
        "notes": [notes_by_index[i] for i in sorted(notes_by_index)],
    }


def rebuild_drawing_extraction(drawing_title: str, rows: List[ExtractionResult]) -> DrawingExtraction:
    """Reconstructs a real DrawingExtraction (not just the display dict
    unflatten_extraction returns) from stored EXTRACTION_RESULTS rows, so a
    GAD/SUB extracted in an earlier Upload-tab run can be fed into
    comparison_service.compare() later without re-extracting. source_file is
    set to drawing_title (the same value used as source_label at extraction
    time -- see pipeline_service.py) since compare()'s output echoes it back
    verbatim in ComparisonReport.parts[].sub_drawing_file, and the caller
    needs that to match for drawing_id_by_sub_filename lookups."""
    view = unflatten_extraction(rows)
    return DrawingExtraction(
        source_file=drawing_title,
        drawing_category=view["drawing_category"] or "SUB_PART",
        # flatten_extraction() drops empty values, so a title block field the
        # sheet left blank has no stored row -- default them back to "" since
        # drawing_no/title are required on TitleBlock.
        title_block={"drawing_no": "", "title": "", **view["title_block"]},
        part_specifications=view["specifications"],
        bill_of_materials=view["bill_of_materials"],
        dimensional_callouts=view["dimensional_callouts"],
        notes=view["notes"],
    )


_JC_PARAM_KEY = re.compile(r"^PARAM\.(\d+)\.(.+)$")
_JC_NOTE_KEY = re.compile(r"^NOTE\.(\d+)$")


def unflatten_jobcard_extraction(rows: List[ExtractionResult]) -> JobCardExtraction:
    """Inverse of flatten_jobcard_extraction(): reassembles the flat rows back
    into a JobCardExtraction for display -- reuses the same Pydantic model
    Gemini's response was validated against, since the flat scheme is a
    lossless round-trip of it."""
    doc_meta: Dict[str, str] = {}
    params_by_index: Dict[int, Dict[str, str]] = {}
    notes_by_index: Dict[int, str] = {}

    for row in rows:
        param, value = row.parameter, row.value

        if param.startswith("DOC_META."):
            doc_meta[param.removeprefix("DOC_META.").lower()] = value
        elif (m := _JC_PARAM_KEY.match(param)):
            idx, field = int(m.group(1)), m.group(2).lower()
            params_by_index.setdefault(idx, {})[field] = value
        elif (m := _JC_NOTE_KEY.match(param)):
            notes_by_index[int(m.group(1))] = value

    return JobCardExtraction(
        document_meta=JobCardDocumentMeta(**doc_meta),
        parameters=[
            JobCardParameter(
                parameter=params_by_index[i].get("parameter", ""),
                value=params_by_index[i].get("value", ""),
                unit=params_by_index[i].get("unit", ""),
                category=params_by_index[i].get("category", ""),
                table_or_section=params_by_index[i].get("section", ""),
            )
            for i in sorted(params_by_index)
        ],
        notes_and_flags=[notes_by_index[i] for i in sorted(notes_by_index)],
    )
