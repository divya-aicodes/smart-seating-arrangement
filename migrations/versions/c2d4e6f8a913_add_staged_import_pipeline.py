"""add staged import pipeline

Revision ID: c2d4e6f8a913
Revises: b6f0a9e2c741
Create Date: 2026-11-04 18:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c2d4e6f8a913"
down_revision = "b6f0a9e2c741"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("data_imports") as batch_op:
        batch_op.drop_constraint("ck_data_imports_status", type_="check")
        batch_op.create_check_constraint(
            "ck_data_imports_status",
            "status IN ('pending', 'processing', 'validated', 'committing', 'completed', 'failed', 'cancelled')",
        )
        batch_op.add_column(sa.Column("checksum_sha256", sa.String(length=64)))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=64)))
        batch_op.add_column(sa.Column("total_rows", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("processed_rows", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("chunk_size", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("import_options", sa.JSON()))
        batch_op.add_column(sa.Column("committed_at", sa.DateTime(timezone=True)))
        batch_op.create_index("ix_data_imports_checksum_sha256", ["checksum_sha256"])
        batch_op.create_index(
            "ix_data_imports_idempotency_key", ["idempotency_key"], unique=True
        )

    op.execute(
        "UPDATE data_imports SET total_rows = valid_rows + invalid_rows, "
        "processed_rows = valid_rows + invalid_rows, chunk_size = 5000"
    )

    with op.batch_alter_table("data_imports") as batch_op:
        batch_op.alter_column("total_rows", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("processed_rows", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("chunk_size", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            "ck_data_imports_total_rows_nonnegative", "total_rows >= 0"
        )
        batch_op.create_check_constraint(
            "ck_data_imports_processed_rows_nonnegative", "processed_rows >= 0"
        )
        batch_op.create_check_constraint(
            "ck_data_imports_valid_rows_nonnegative", "valid_rows >= 0"
        )
        batch_op.create_check_constraint(
            "ck_data_imports_invalid_rows_nonnegative", "invalid_rows >= 0"
        )
        batch_op.create_check_constraint(
            "ck_data_imports_chunk_size_positive", "chunk_size > 0"
        )

    op.create_table(
        "import_staging_students",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_import_id", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("student_code", sa.String(length=64), nullable=False),
        sa.Column("student_code_normalized", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=True),
        sa.Column("class_name", sa.String(length=100), nullable=False),
        sa.Column("department_name", sa.String(length=100), nullable=True),
        sa.Column("subject_code", sa.String(length=50), nullable=False),
        sa.Column("subject_name", sa.String(length=150), nullable=False),
        sa.Column("accessibility_required", sa.Boolean(), nullable=False),
        sa.Column("row_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_import_id"], ["data_imports.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_import_id", "row_number", name="uq_import_staging_row"
        ),
        sa.UniqueConstraint(
            "data_import_id", "student_code_normalized",
            name="uq_import_staging_student_code",
        ),
    )
    op.create_index(
        "ix_import_staging_students_data_import_id",
        "import_staging_students",
        ["data_import_id"],
    )
    op.create_index(
        "ix_import_staging_import_subject",
        "import_staging_students",
        ["data_import_id", "subject_code"],
    )

    op.create_table(
        "import_row_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_import_id", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_import_id"], ["data_imports.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_row_errors_data_import_id",
        "import_row_errors",
        ["data_import_id"],
    )
    op.create_index(
        "ix_import_row_errors_import_row",
        "import_row_errors",
        ["data_import_id", "row_number"],
    )


def downgrade():
    op.drop_index("ix_import_row_errors_import_row", table_name="import_row_errors")
    op.drop_index("ix_import_row_errors_data_import_id", table_name="import_row_errors")
    op.drop_table("import_row_errors")
    op.drop_index("ix_import_staging_import_subject", table_name="import_staging_students")
    op.drop_index(
        "ix_import_staging_students_data_import_id", table_name="import_staging_students"
    )
    op.drop_table("import_staging_students")

    with op.batch_alter_table("data_imports") as batch_op:
        batch_op.drop_constraint("ck_data_imports_chunk_size_positive", type_="check")
        batch_op.drop_constraint("ck_data_imports_invalid_rows_nonnegative", type_="check")
        batch_op.drop_constraint("ck_data_imports_valid_rows_nonnegative", type_="check")
        batch_op.drop_constraint("ck_data_imports_processed_rows_nonnegative", type_="check")
        batch_op.drop_constraint("ck_data_imports_total_rows_nonnegative", type_="check")
        batch_op.drop_index("ix_data_imports_idempotency_key")
        batch_op.drop_index("ix_data_imports_checksum_sha256")
        batch_op.drop_column("committed_at")
        batch_op.drop_column("import_options")
        batch_op.drop_column("chunk_size")
        batch_op.drop_column("processed_rows")
        batch_op.drop_column("total_rows")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("checksum_sha256")
        batch_op.drop_constraint("ck_data_imports_status", type_="check")
        batch_op.create_check_constraint(
            "ck_data_imports_status",
            "status IN ('pending', 'processing', 'completed', 'failed')",
        )
