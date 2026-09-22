"""
PROJECT_DRAWINGS is one row per uploaded PDF (the GAD, or one sub-drawing),
recording where it's stored on disk and which kind it is.

Soft-delete: re-running extraction on the same (project_id, type, title) --
e.g. re-uploading a corrected GAD, or clicking "Run extraction" again for
the same job card -- does not pile up duplicate rows forever. The prior
active row is marked is_deleted/deleted_at instead of being hard-deleted
(kept for audit/history) and a fresh row takes its place as the one queries
return. See persistence_service.save_drawing().

is_extracted/extraction_error: the row is created as soon as a file is
uploaded (is_extracted=False), then the background extraction step flips
is_extracted to True on success or records extraction_error on failure --
see persistence_service.mark_drawing_extracted(). This makes "has this
specific upload finished extracting" an explicit, queryable state instead
of an implicit "row exists" convention, and lets the Upload tab show a
per-file uploading/extracting/done/failed status.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DrawingType:
    GAD = 0
    SUB = 1
    JOB_CARD = 2
    DXF = 3


class ProjectDrawing(Base):
    __tablename__ = "project_drawings"
    __table_args__ = (
        Index("ix_project_drawings_project_type", "project_id", "type"),
        # Speeds up save_drawing()'s "find the current active row for this
        # (project, type, title)" lookup on every extraction run.
        Index("ix_project_drawings_active_lookup", "project_id", "type", "title", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    type: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # DrawingType.GAD / .SUB

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_extracted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
