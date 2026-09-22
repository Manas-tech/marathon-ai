"""
COMPARISON_RESULTS stores one row per Finding (see app/schemas/drawing.py)
produced by the GAD-vs-sub-drawing comparison step. drawing_id points at
the matched sub-drawing's PROJECT_DRAWINGS row; gad_drawing_id points at the
GAD drawing it was compared against (set explicitly by pipeline_service at
write time -- not inferred, since a project can accumulate multiple GAD
revisions/jobs over time and timestamp-nearest-GAD would be a guess).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ComparisonResult(Base):
    __tablename__ = "comparison_results"
    __table_args__ = (
        Index("ix_comparison_results_drawing_parameter", "drawing_id", "parameter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    drawing_id: Mapped[int] = mapped_column(
        ForeignKey("project_drawings.id"), nullable=False, index=True
    )
    gad_drawing_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_drawings.id"), nullable=True, index=True
    )

    parameter: Mapped[str] = mapped_column(String(255), nullable=False)
    gad_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subd_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mismatch_acceptance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
