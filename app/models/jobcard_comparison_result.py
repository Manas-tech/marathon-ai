"""
JOBCARD_COMPARISON_RESULTS stores one row per Finding produced by comparing
a sub-drawing (currently heating-element drawings only) against the job card.
Separate from COMPARISON_RESULTS on purpose: that table is GAD-shaped
(gad_drawing_id / gad_value) and every GAD browse query assumes it, so mixing
a second kind of comparison into it would need a type filter everywhere.

sub_drawing_id and job_card_drawing_id both point at PROJECT_DRAWINGS rows,
set explicitly at write time (a project can hold several job cards/subs).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobCardComparisonResult(Base):
    __tablename__ = "jobcard_comparison_results"
    __table_args__ = (
        Index("ix_jobcard_comparison_results_sub_parameter", "sub_drawing_id", "parameter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    sub_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_drawings.id"), nullable=False, index=True)
    job_card_drawing_id: Mapped[int] = mapped_column(ForeignKey("project_drawings.id"), nullable=False, index=True)

    parameter: Mapped[str] = mapped_column(String(255), nullable=False)
    jobcard_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subd_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    mismatch_acceptance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
