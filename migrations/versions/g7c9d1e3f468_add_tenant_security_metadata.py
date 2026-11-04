"""add tenant security metadata

Revision ID: g7c9d1e3f468
Revises: f6b8c0d2e357
Create Date: 2026-11-05 01:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "g7c9d1e3f468"
down_revision = "f6b8c0d2e357"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_constraint("uq_datasets_name_version", type_="unique")
        batch_op.create_unique_constraint(
            "uq_datasets_institution_name_version", ["institution_id", "name", "version"]
        )
    with op.batch_alter_table("students") as batch_op:
        batch_op.drop_index("ix_students_student_code")
        batch_op.create_index("ix_students_student_code", ["student_code"], unique=False)
        batch_op.create_unique_constraint(
            "uq_students_institution_code", ["institution_id", "student_code"]
        )
    with op.batch_alter_table("subjects") as batch_op:
        batch_op.drop_index("ix_subjects_code")
        batch_op.create_index("ix_subjects_code", ["code"], unique=False)
        batch_op.create_unique_constraint(
            "uq_subjects_institution_code", ["institution_id", "code"]
        )
    with op.batch_alter_table("rooms") as batch_op:
        batch_op.drop_constraint("uq_rooms_building_number", type_="unique")
        batch_op.create_unique_constraint(
            "uq_rooms_institution_building_number",
            ["institution_id", "building", "room_number"],
        )
    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.add_column(sa.Column("institution_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_seating_runs_institution_id", ["institution_id"])
        batch_op.create_foreign_key(
            "fk_seating_runs_institution_id", "institutions", ["institution_id"], ["id"],
            ondelete="RESTRICT",
        )
    op.execute(
        "UPDATE seating_runs SET institution_id = "
        "(SELECT institution_id FROM exams WHERE exams.id = seating_runs.exam_id) "
        "WHERE institution_id IS NULL"
    )

    with op.batch_alter_table("background_jobs") as batch_op:
        batch_op.add_column(sa.Column("institution_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_background_jobs_institution_id", ["institution_id"])
        batch_op.create_foreign_key(
            "fk_background_jobs_institution_id", "institutions", ["institution_id"], ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("institution_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("request_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("ip_address", sa.String(length=45), nullable=True))
        batch_op.create_index("ix_audit_logs_institution_id", ["institution_id"])
        batch_op.create_index("ix_audit_logs_actor_user_id", ["actor_user_id"])
        batch_op.create_index("ix_audit_logs_request_id", ["request_id"])
        batch_op.create_foreign_key(
            "fk_audit_logs_institution_id", "institutions", ["institution_id"], ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_audit_logs_actor_user_id", "users", ["actor_user_id"], ["id"],
            ondelete="SET NULL",
        )
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_constraint("fk_audit_logs_actor_user_id", type_="foreignkey")
        batch_op.drop_constraint("fk_audit_logs_institution_id", type_="foreignkey")
        batch_op.drop_index("ix_audit_logs_request_id")
        batch_op.drop_index("ix_audit_logs_actor_user_id")
        batch_op.drop_index("ix_audit_logs_institution_id")
        batch_op.drop_column("ip_address")
        batch_op.drop_column("request_id")
        batch_op.drop_column("actor_user_id")
        batch_op.drop_column("institution_id")
    with op.batch_alter_table("background_jobs") as batch_op:
        batch_op.drop_constraint("fk_background_jobs_institution_id", type_="foreignkey")
        batch_op.drop_index("ix_background_jobs_institution_id")
        batch_op.drop_column("institution_id")
    with op.batch_alter_table("seating_runs") as batch_op:
        batch_op.drop_constraint("fk_seating_runs_institution_id", type_="foreignkey")
        batch_op.drop_index("ix_seating_runs_institution_id")
        batch_op.drop_column("institution_id")
    with op.batch_alter_table("rooms") as batch_op:
        batch_op.drop_constraint("uq_rooms_institution_building_number", type_="unique")
        batch_op.create_unique_constraint(
            "uq_rooms_building_number", ["building", "room_number"]
        )
    with op.batch_alter_table("subjects") as batch_op:
        batch_op.drop_constraint("uq_subjects_institution_code", type_="unique")
        batch_op.drop_index("ix_subjects_code")
        batch_op.create_index("ix_subjects_code", ["code"], unique=True)
    with op.batch_alter_table("students") as batch_op:
        batch_op.drop_constraint("uq_students_institution_code", type_="unique")
        batch_op.drop_index("ix_students_student_code")
        batch_op.create_index("ix_students_student_code", ["student_code"], unique=True)
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_constraint("uq_datasets_institution_name_version", type_="unique")
        batch_op.create_unique_constraint("uq_datasets_name_version", ["name", "version"])
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=ON")
