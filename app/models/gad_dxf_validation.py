"""
GAD DXF vs cutting-sheet DXF validation results (Run Comparison tab's
"GAD vs DXF" card; see app/services/gad_dxf_service.py).

GAD_DXF_VALIDATION_RUNS: one row per run -- which two DXF drawings were
compared, the run-level numbers from the merged match JSON, and where the
generated files (overlay PNG etc.) live on disk. Re-validating the same
(gad, cutting) pair replaces the earlier run (see
persistence_service.save_gad_dxf_validation), so there is one current run
per pair.

GAD_DXF_VALIDATION_MATCHES: one row per capsule -- its nearest baffle hole,
the radii being compared, and MATCH/MISMATCH (radii within
gad_dxf_service.RADIUS_MATCH_TOL). mismatch_acceptance works like the other
comparison tables' accept-this-mismatch checkbox.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GadDxfValidationRun(Base):
    __tablename__ = "gad_dxf_validation_runs"
    __table_args__ = (
        Index("ix_gad_dxf_validation_runs_pair", "gad_drawing_id", "cutting_drawing_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    gad_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_drawings.id"), nullable=False)
    cutting_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_drawings.id"), nullable=False)

    # Paths relative to settings.upload_dir_path, POSIX-separated (URL-safe: served via /uploads).
    output_dir: Mapped[str] = mapped_column(String(512), nullable=False)
    overlay_path: Mapped[str] = mapped_column(String(512), nullable=False)

    outer_radius_gad: Mapped[float | None] = mapped_column(Float, nullable=True)
    outer_radius_baffle: Mapped[float | None] = mapped_column(Float, nullable=True)
    outer_radius_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    capsule_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hole_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_diff: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_center_offset: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_center_offset: Mapped[float | None] = mapped_column(Float, nullable=True)
    capsule_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    capsule_overall_length: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class GadDxfValidationMatch(Base):
    __tablename__ = "gad_dxf_validation_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("gad_dxf_validation_runs.id"), nullable=False, index=True)
    capsule_index: Mapped[int] = mapped_column(Integer, nullable=False)

    capsule_center_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    capsule_center_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    hole_center_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    hole_center_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    capsule_radius: Mapped[float | None] = mapped_column(Float, nullable=True)
    hole_radius: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_offset: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mismatch_acceptance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
