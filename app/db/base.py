"""
Import every ORM model here so that a single `from app.db.base import Base`
gives Alembic (and `Base.metadata.create_all`) full knowledge of the schema.

Alembic's env.py imports `Base` from this module -- not from
`app.db.base_class` directly -- specifically so that new models are picked
up automatically as long as they're added to the import list below.
"""
from app.db.base_class import Base  # noqa: F401

from app.models.project import Project  # noqa: F401
from app.models.project_drawing import ProjectDrawing  # noqa: F401
from app.models.extraction_result import ExtractionResult  # noqa: F401
from app.models.comparison_result import ComparisonResult  # noqa: F401
from app.models.jobcard_comparison_result import JobCardComparisonResult  # noqa: F401
from app.models.gad_dxf_validation import GadDxfValidationRun, GadDxfValidationMatch  # noqa: F401
