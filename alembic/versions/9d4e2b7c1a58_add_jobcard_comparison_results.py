"""add jobcard_comparison_results table

Revision ID: 9d4e2b7c1a58
Revises: 7b1c4e9a2f33
Create Date: 2026-09-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4e2b7c1a58'
down_revision: Union[str, None] = '7b1c4e9a2f33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AUTO_CREATE_TABLES=true (dev) may already have created the table via
    # Base.metadata.create_all() before this migration ran; nothing to do then.
    if 'jobcard_comparison_results' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'jobcard_comparison_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('sub_drawing_id', sa.Integer(), nullable=False),
        sa.Column('job_card_drawing_id', sa.Integer(), nullable=False),
        sa.Column('parameter', sa.String(length=255), nullable=False),
        sa.Column('jobcard_value', sa.Text(), nullable=False),
        sa.Column('subd_value', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('mismatch_acceptance', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['sub_drawing_id'], ['project_drawings.id']),
        sa.ForeignKeyConstraint(['job_card_drawing_id'], ['project_drawings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_jobcard_comparison_results_project_id'), 'jobcard_comparison_results', ['project_id'])
    op.create_index(op.f('ix_jobcard_comparison_results_sub_drawing_id'), 'jobcard_comparison_results', ['sub_drawing_id'])
    op.create_index(
        op.f('ix_jobcard_comparison_results_job_card_drawing_id'), 'jobcard_comparison_results', ['job_card_drawing_id']
    )
    op.create_index(op.f('ix_jobcard_comparison_results_status'), 'jobcard_comparison_results', ['status'])
    op.create_index(
        'ix_jobcard_comparison_results_sub_parameter', 'jobcard_comparison_results', ['sub_drawing_id', 'parameter']
    )


def downgrade() -> None:
    op.drop_table('jobcard_comparison_results')
