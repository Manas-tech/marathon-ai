"""
EXTRACTION_RESULTS stores a DrawingExtraction (see app/schemas/drawing.py)
flattened into one (parameter, value) row per leaf field, instead of one
raw JSON blob per drawing -- so individual fields stay queryable/indexable
in SQL. See app.services.persistence_service.flatten_extraction for the
exact key scheme (TITLE_BLOCK.*, SPEC.*, BOM.<n>.*, CALLOUT.<n>.*, NOTE.<n>).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionResult(Base):
    __tablename__ = "extraction_results"
    __table_args__ = (
        Index("ix_extraction_results_drawing_parameter", "drawing_id", "parameter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    drawing_id: Mapped[int] = mapped_column(
        ForeignKey("project_drawings.id"), nullable=False, index=True
    )

    parameter: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
