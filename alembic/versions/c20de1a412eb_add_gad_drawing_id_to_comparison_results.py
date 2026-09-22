"""add gad_drawing_id to comparison_results

Revision ID: c20de1a412eb
Revises: 3f9c2a7e1b04
Create Date: 2026-08-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c20de1a412eb'
down_revision: Union[str, None] = '3f9c2a7e1b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'comparison_results',
        sa.Column('gad_drawing_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_comparison_results_gad_drawing_id',
        'comparison_results', 'project_drawings',
        ['gad_drawing_id'], ['id'],
    )
    op.create_index(
        op.f('ix_comparison_results_gad_drawing_id'), 'comparison_results', ['gad_drawing_id'], unique=False
    )

    # Backfill existing rows: same nearest-earlier-GAD-in-project heuristic
    # the API used to run at read time, run once here instead so it isn't
    # needed at read time going forward.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT DISTINCT cr.project_id, cr.drawing_id, pd.created_at "
            "FROM comparison_results cr "
            "JOIN project_drawings pd ON pd.id = cr.drawing_id "
            "WHERE cr.gad_drawing_id IS NULL"
        )
    ).fetchall()

    for project_id, drawing_id, sub_created_at in rows:
        gad = conn.execute(
            sa.text(
                "SELECT id FROM project_drawings "
                "WHERE project_id = :project_id AND type = 0 AND created_at <= :sub_created_at "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"project_id": project_id, "sub_created_at": sub_created_at},
        ).fetchone()

        if gad is None:
            gad = conn.execute(
                sa.text(
                    "SELECT id FROM project_drawings "
                    "WHERE project_id = :project_id AND type = 0 "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"project_id": project_id},
            ).fetchone()

        if gad is not None:
            conn.execute(
                sa.text(
                    "UPDATE comparison_results SET gad_drawing_id = :gad_id "
                    "WHERE project_id = :project_id AND drawing_id = :drawing_id"
                ),
                {"gad_id": gad[0], "project_id": project_id, "drawing_id": drawing_id},
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_comparison_results_gad_drawing_id'), table_name='comparison_results')
    op.drop_constraint('fk_comparison_results_gad_drawing_id', 'comparison_results', type_='foreignkey')
    op.drop_column('comparison_results', 'gad_drawing_id')
