"""add truthful solver status

Revision ID: e4f6a8b0c135
Revises: d3e5f7a9b024
Create Date: 2026-11-04 21:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f6a8b0c135"
down_revision = "d3e5f7a9b024"
branch_labels = None
depends_on = None


SOLVER_STATUS_CHECK = (
    "solver_status IN ('OPTIMAL', 'FEASIBLE', 'FEASIBLE_WITH_RELAXATION', "
    "'INFEASIBLE', 'TIME_LIMIT_REACHED', 'UNKNOWN')"
)


def upgrade():
    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.add_column(
            sa.Column("solver_status", sa.String(length=40), nullable=False, server_default="UNKNOWN")
        )
        batch_op.create_check_constraint("ck_seating_runs_solver_status", SOLVER_STATUS_CHECK)
    op.execute(
        "UPDATE seating_runs SET solver_status = 'FEASIBLE' "
        "WHERE status IN ('completed', 'published')"
    )


def downgrade():
    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.drop_constraint("ck_seating_runs_solver_status", type_="check")
        batch_op.drop_column("solver_status")
