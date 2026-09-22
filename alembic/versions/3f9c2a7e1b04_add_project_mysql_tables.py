"""add projects/project_drawings/extraction_results/comparison_results tables

Revision ID: 3f9c2a7e1b04
Revises: f0719c0cbc19
Create Date: 2026-08-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f9c2a7e1b04'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('so_no', sa.String(length=100), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'project_drawings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('path', sa.String(length=1024), nullable=False),
        sa.Column('type', sa.SmallInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_project_drawings_project_id'), 'project_drawings', ['project_id'], unique=False
    )
    op.create_index(
        'ix_project_drawings_project_type', 'project_drawings', ['project_id', 'type'], unique=False
    )

    op.create_table(
        'extraction_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('drawing_id', sa.Integer(), nullable=False),
        sa.Column('parameter', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['drawing_id'], ['project_drawings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_extraction_results_project_id'), 'extraction_results', ['project_id'], unique=False
    )
    op.create_index(
        op.f('ix_extraction_results_drawing_id'), 'extraction_results', ['drawing_id'], unique=False
    )
    op.create_index(
        'ix_extraction_results_drawing_parameter', 'extraction_results', ['drawing_id', 'parameter'], unique=False
    )

    op.create_table(
        'comparison_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('drawing_id', sa.Integer(), nullable=False),
        sa.Column('parameter', sa.String(length=255), nullable=False),
        sa.Column('gad_value', sa.Text(), nullable=False),
        sa.Column('subd_value', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('mismatch_acceptance', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['drawing_id'], ['project_drawings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_comparison_results_project_id'), 'comparison_results', ['project_id'], unique=False
    )
    op.create_index(
        op.f('ix_comparison_results_drawing_id'), 'comparison_results', ['drawing_id'], unique=False
    )
    op.create_index(
        op.f('ix_comparison_results_status'), 'comparison_results', ['status'], unique=False
    )
    op.create_index(
        'ix_comparison_results_drawing_parameter', 'comparison_results', ['drawing_id', 'parameter'], unique=False
    )


def downgrade() -> None:
    op.drop_table('comparison_results')
    op.drop_table('extraction_results')
    op.drop_index('ix_project_drawings_project_type', table_name='project_drawings')
    op.drop_index(op.f('ix_project_drawings_project_id'), table_name='project_drawings')
    op.drop_table('project_drawings')
    op.drop_table('projects')
