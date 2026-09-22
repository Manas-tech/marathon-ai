"""add gad_dxf_validation_runs / gad_dxf_validation_matches tables

Revision ID: a3c5e8f10b72
Revises: 9d4e2b7c1a58
Create Date: 2026-09-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c5e8f10b72'
down_revision: Union[str, None] = '9d4e2b7c1a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AUTO_CREATE_TABLES=true (dev) may already have created these via
    # Base.metadata.create_all() before this migration ran; skip what exists.
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if 'gad_dxf_validation_runs' not in existing:
        op.create_table(
            'gad_dxf_validation_runs',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('gad_drawing_id', sa.Integer(), nullable=False),
            sa.Column('cutting_drawing_id', sa.Integer(), nullable=False),
            sa.Column('output_dir', sa.String(length=512), nullable=False),
            sa.Column('overlay_path', sa.String(length=512), nullable=False),
            sa.Column('outer_radius_gad', sa.Float(), nullable=True),
            sa.Column('outer_radius_baffle', sa.Float(), nullable=True),
            sa.Column('outer_radius_diff', sa.Float(), nullable=True),
            sa.Column('capsule_count', sa.Integer(), nullable=False),
            sa.Column('hole_count', sa.Integer(), nullable=False),
            sa.Column('count_diff', sa.Integer(), nullable=False),
            sa.Column('mean_center_offset', sa.Float(), nullable=True),
            sa.Column('max_center_offset', sa.Float(), nullable=True),
            sa.Column('capsule_width', sa.Float(), nullable=True),
            sa.Column('capsule_overall_length', sa.Float(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
            sa.ForeignKeyConstraint(['gad_drawing_id'], ['project_drawings.id']),
            sa.ForeignKeyConstraint(['cutting_drawing_id'], ['project_drawings.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_gad_dxf_validation_runs_project_id'), 'gad_dxf_validation_runs', ['project_id'])
        op.create_index(
            'ix_gad_dxf_validation_runs_pair', 'gad_dxf_validation_runs', ['gad_drawing_id', 'cutting_drawing_id']
        )

    if 'gad_dxf_validation_matches' not in existing:
        op.create_table(
            'gad_dxf_validation_matches',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('run_id', sa.Integer(), nullable=False),
            sa.Column('capsule_index', sa.Integer(), nullable=False),
            sa.Column('capsule_center_x', sa.Float(), nullable=True),
            sa.Column('capsule_center_y', sa.Float(), nullable=True),
            sa.Column('hole_center_x', sa.Float(), nullable=True),
            sa.Column('hole_center_y', sa.Float(), nullable=True),
            sa.Column('capsule_radius', sa.Float(), nullable=True),
            sa.Column('hole_radius', sa.Float(), nullable=True),
            sa.Column('center_offset', sa.Float(), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('mismatch_acceptance', sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(['run_id'], ['gad_dxf_validation_runs.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_gad_dxf_validation_matches_run_id'), 'gad_dxf_validation_matches', ['run_id'])
        op.create_index(op.f('ix_gad_dxf_validation_matches_status'), 'gad_dxf_validation_matches', ['status'])


def downgrade() -> None:
    op.drop_table('gad_dxf_validation_matches')
    op.drop_table('gad_dxf_validation_runs')
