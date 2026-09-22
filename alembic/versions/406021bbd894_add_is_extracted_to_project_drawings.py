"""add is_extracted/extraction_error to project_drawings

Revision ID: 406021bbd894
Revises: 729653b3a22b
Create Date: 2026-08-13 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '406021bbd894'
down_revision: Union[str, None] = '729653b3a22b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default=true backfills every pre-existing row as already-extracted
    # (under the old implicit convention, a project_drawings row only ever got
    # created after a successful extraction/split -- see persistence_service.py).
    # New rows going forward use the ORM's Python-side default=False instead,
    # since save_drawing() now creates the row before extraction runs.
    op.add_column(
        'project_drawings',
        sa.Column('is_extracted', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column('project_drawings', sa.Column('extraction_error', sa.Text(), nullable=True))
    op.create_index(op.f('ix_project_drawings_is_extracted'), 'project_drawings', ['is_extracted'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_project_drawings_is_extracted'), table_name='project_drawings')
    op.drop_column('project_drawings', 'extraction_error')
    op.drop_column('project_drawings', 'is_extracted')
