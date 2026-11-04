"""align background job uniqueness with SQLAlchemy metadata

Revision ID: f6b8c0d2e357
Revises: f5a7b9c1d246
Create Date: 2026-11-04 23:05:00
"""

from alembic import op


revision = "f6b8c0d2e357"
down_revision = "f5a7b9c1d246"
branch_labels = None
depends_on = None


def upgrade():
    # The preceding migration created both unique indexes and redundant unique
    # constraints. SQLAlchemy models use the indexes, so remove only the
    # redundant PostgreSQL constraints without touching data or index coverage.
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "background_jobs_idempotency_key_key",
            "background_jobs",
            type_="unique",
        )
        op.drop_constraint(
            "background_jobs_task_id_key",
            "background_jobs",
            type_="unique",
        )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint(
            "background_jobs_task_id_key", "background_jobs", ["task_id"]
        )
        op.create_unique_constraint(
            "background_jobs_idempotency_key_key",
            "background_jobs",
            ["idempotency_key"],
        )
