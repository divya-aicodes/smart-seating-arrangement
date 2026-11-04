"""add exam sessions and verified registrations

Revision ID: d3e5f7a9b024
Revises: c2d4e6f8a913
Create Date: 2026-11-04 19:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e5f7a9b024"
down_revision = "c2d4e6f8a913"
branch_labels = None
depends_on = None


def _set_sqlite_foreign_keys(enabled):
    """SQLite batch table recreation requires FK enforcement to be temporarily off."""
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    state = "ON" if enabled else "OFF"
    with op.get_context().autocommit_block():
        bind.exec_driver_sql(f"PRAGMA foreign_keys={state}")


def upgrade():
    _set_sqlite_foreign_keys(False)
    op.create_table(
        "exam_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("exam_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("session_name", sa.String(length=80), nullable=False),
        sa.Column("academic_year", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_time > start_time", name="ck_exam_sessions_time_order"),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'completed', 'cancelled')",
            name="ck_exam_sessions_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('real', 'synthetic')",
            name="ck_exam_sessions_source_type",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"], ["datasets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "exam_date", "start_time", "end_time", "session_name",
            name="uq_exam_sessions_dataset_schedule",
        ),
    )
    op.create_index("ix_exam_sessions_dataset_id", "exam_sessions", ["dataset_id"])
    op.create_index("ix_exam_sessions_exam_date", "exam_sessions", ["exam_date"])
    op.create_index("ix_exam_sessions_institution_id", "exam_sessions", ["institution_id"])
    op.create_index(
        "ix_exam_sessions_date_time",
        "exam_sessions",
        ["exam_date", "start_time", "end_time"],
    )

    with op.batch_alter_table("exams") as batch_op:
        batch_op.add_column(sa.Column("exam_session_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_exams_exam_session_id_exam_sessions",
            "exam_sessions",
            ["exam_session_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_exams_exam_session_id", ["exam_session_id"])

    with op.batch_alter_table("exam_registrations") as batch_op:
        batch_op.add_column(sa.Column("exam_session_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("verification_status", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("verification_source", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_exam_registrations_exam_session_id_exam_sessions",
            "exam_sessions",
            ["exam_session_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_exam_registrations_exam_session_id", ["exam_session_id"]
        )

    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.add_column(sa.Column("exam_session_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_seating_runs_exam_session_id_exam_sessions",
            "exam_sessions",
            ["exam_session_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_seating_runs_exam_session_id", ["exam_session_id"])

    bind = op.get_bind()
    metadata = sa.MetaData()
    exams = sa.Table("exams", metadata, autoload_with=bind)
    sessions = sa.Table("exam_sessions", metadata, autoload_with=bind)
    registrations = sa.Table("exam_registrations", metadata, autoload_with=bind)
    runs = sa.Table("seating_runs", metadata, autoload_with=bind)

    # Each legacy exam becomes its own session. This preserves all existing meanings
    # without guessing that two independently created exams were intended to share a shift.
    legacy_exams = bind.execute(sa.select(exams).order_by(exams.c.id)).mappings().all()
    for legacy in legacy_exams:
        inserted = bind.execute(
            sessions.insert().values(
                institution_id=legacy["institution_id"],
                dataset_id=legacy["dataset_id"],
                exam_date=legacy["exam_date"],
                start_time=legacy["start_time"],
                end_time=legacy["end_time"],
                session_name=legacy["session_name"],
                academic_year=legacy["academic_year"],
                status=legacy["status"],
                source_type=legacy["source_type"],
                created_at=legacy["created_at"],
                updated_at=legacy["updated_at"],
            )
        )
        session_id = inserted.inserted_primary_key[0]
        bind.execute(
            exams.update().where(exams.c.id == legacy["id"]).values(exam_session_id=session_id)
        )
        bind.execute(
            registrations.update()
            .where(registrations.c.exam_id == legacy["id"])
            .values(
                exam_session_id=session_id,
                subject_id=sa.func.coalesce(
                    registrations.c.subject_id, legacy["subject_id"]
                ),
                verification_status="verified",
                verification_source="legacy_exam_registration",
                verified_at=registrations.c.created_at,
            )
        )
        bind.execute(
            runs.update()
            .where(runs.c.exam_id == legacy["id"])
            .values(exam_session_id=session_id)
        )

    with op.batch_alter_table("exams") as batch_op:
        batch_op.alter_column(
            "exam_session_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_exams_session_subject", ["exam_session_id", "subject_id"]
        )

    with op.batch_alter_table("exam_registrations") as batch_op:
        batch_op.alter_column(
            "exam_session_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "subject_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.alter_column(
            "verification_status", existing_type=sa.String(length=20), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_exam_registrations_session_student",
            ["exam_session_id", "student_id"],
        )
        batch_op.create_check_constraint(
            "ck_exam_registrations_verification_status",
            "verification_status IN ('pending', 'verified', 'rejected')",
        )
        batch_op.create_index(
            "ix_exam_registrations_session_verification",
            ["exam_session_id", "verification_status"],
        )

    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.alter_column(
            "exam_session_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.create_index(
            "ix_seating_runs_session_status", ["exam_session_id", "status"]
        )
    _set_sqlite_foreign_keys(True)


def downgrade():
    _set_sqlite_foreign_keys(False)
    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.drop_index("ix_seating_runs_session_status")
        batch_op.drop_index("ix_seating_runs_exam_session_id")
        batch_op.drop_constraint(
            "fk_seating_runs_exam_session_id_exam_sessions", type_="foreignkey"
        )
        batch_op.drop_column("exam_session_id")

    with op.batch_alter_table("exam_registrations") as batch_op:
        batch_op.drop_index("ix_exam_registrations_session_verification")
        batch_op.drop_constraint(
            "ck_exam_registrations_verification_status", type_="check"
        )
        batch_op.drop_constraint(
            "uq_exam_registrations_session_student", type_="unique"
        )
        batch_op.drop_index("ix_exam_registrations_exam_session_id")
        batch_op.drop_constraint(
            "fk_exam_registrations_exam_session_id_exam_sessions", type_="foreignkey"
        )
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verification_source")
        batch_op.drop_column("verification_status")
        batch_op.drop_column("exam_session_id")
        batch_op.alter_column("subject_id", existing_type=sa.Integer(), nullable=True)

    with op.batch_alter_table("exams") as batch_op:
        batch_op.drop_constraint("uq_exams_session_subject", type_="unique")
        batch_op.drop_index("ix_exams_exam_session_id")
        batch_op.drop_constraint(
            "fk_exams_exam_session_id_exam_sessions", type_="foreignkey"
        )
        batch_op.drop_column("exam_session_id")

    op.drop_index("ix_exam_sessions_date_time", table_name="exam_sessions")
    op.drop_index("ix_exam_sessions_institution_id", table_name="exam_sessions")
    op.drop_index("ix_exam_sessions_exam_date", table_name="exam_sessions")
    op.drop_index("ix_exam_sessions_dataset_id", table_name="exam_sessions")
    op.drop_table("exam_sessions")
    _set_sqlite_foreign_keys(True)
