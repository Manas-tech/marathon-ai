"""Request model for POST /comparisons/from-extraction."""
from __future__ import annotations

from pydantic import BaseModel


class CompareFromExtractionRequest(BaseModel):
    project_id: int
    gad_drawing_id: int
    sub_drawing_ids: list[int]
