"""add durable background jobs

Revision ID: f5a7b9c1d246
Revises: e4f6a8b0c135
Create Date: 2026-11-04 22:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a7b9c1d246"
down_revision = "e4f6a8b0c135"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("progress_message", sa.String(length=250), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("cancellation_requested", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=150), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "job_type IN ('import_stage', 'import_commit', 'seating_optimization', 'report_generation')",
            name="ck_background_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_background_jobs_status",
        ),
        sa.CheckConstraint("progress_current >= 0", name="ck_background_jobs_progress_current"),
        sa.CheckConstraint("progress_total >= 0", name="ck_background_jobs_progress_total"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_background_jobs_task_id", "background_jobs", ["task_id"], unique=True)
    op.create_index("ix_background_jobs_idempotency_key", "background_jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_background_jobs_type_status", "background_jobs", ["job_type", "status"])
    op.create_index("ix_background_jobs_created_status", "background_jobs", ["created_at", "status"])


def downgrade():
    op.drop_index("ix_background_jobs_created_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_type_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_idempotency_key", table_name="background_jobs")
    op.drop_index("ix_background_jobs_task_id", table_name="background_jobs")
    op.drop_table("background_jobs")
