"""add soft-delete/audit columns to projects and project_drawings

Revision ID: 729653b3a22b
Revises: ae0813e57c51
Create Date: 2026-08-13 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '729653b3a22b'
down_revision: Union[str, None] = 'c20de1a412eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('projects', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_projects_is_deleted'), 'projects', ['is_deleted'], unique=False)

    op.add_column(
        'project_drawings',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column(
        'project_drawings', sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column('project_drawings', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_project_drawings_is_deleted'), 'project_drawings', ['is_deleted'], unique=False)
    op.create_index(
        'ix_project_drawings_active_lookup',
        'project_drawings', ['project_id', 'type', 'title', 'is_deleted'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_project_drawings_active_lookup', table_name='project_drawings')
    op.drop_index(op.f('ix_project_drawings_is_deleted'), table_name='project_drawings')
    op.drop_column('project_drawings', 'deleted_at')
    op.drop_column('project_drawings', 'is_deleted')
    op.drop_column('project_drawings', 'updated_at')

    op.drop_index(op.f('ix_projects_is_deleted'), table_name='projects')
    op.drop_column('projects', 'deleted_at')
    op.drop_column('projects', 'is_deleted')
