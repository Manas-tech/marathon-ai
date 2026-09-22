"""drop comparison_jobs and extraction_batches tables

Both job-tracking tables were removed in favor of synchronous comparison
requests (result returned directly in the response) and per-drawing
is_extracted status (extraction_batches was already dead -- superseded by
the per-file upload flow). Their create-table migrations were deleted from
history, so this only needs to actually drop the tables on databases that
already ran them; a fresh database never creates them in the first place,
hence the existence checks.

Revision ID: 7b1c4e9a2f33
Revises: 406021bbd894
Create Date: 2026-08-13 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b1c4e9a2f33'
down_revision: Union[str, None] = '406021bbd894'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    if 'comparison_jobs' in existing:
        op.drop_table('comparison_jobs')
    if 'extraction_batches' in existing:
        op.drop_table('extraction_batches')


def downgrade() -> None:
    op.create_table(
        'comparison_jobs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('gad_filename', sa.String(length=512), nullable=False),
        sa.Column('sub_filenames_json', sa.Text(), nullable=False),
        sa.Column('progress_message', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('usage_json', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'extraction_batches',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('progress_message', sa.Text(), nullable=False),
        sa.Column('gad_drawing_id', sa.Integer(), nullable=True),
        sa.Column('sub_drawing_ids_json', sa.Text(), nullable=False),
        sa.Column('job_card_drawing_id', sa.Integer(), nullable=True),
        sa.Column('dxf_drawing_id', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('usage_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['gad_drawing_id'], ['project_drawings.id']),
        sa.ForeignKeyConstraint(['job_card_drawing_id'], ['project_drawings.id']),
        sa.ForeignKeyConstraint(['dxf_drawing_id'], ['project_drawings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_extraction_batches_project_id'), 'extraction_batches', ['project_id'], unique=False)
