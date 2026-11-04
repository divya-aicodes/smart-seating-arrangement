"""add institution and academic foundation

Revision ID: b6f0a9e2c741
Revises: 08f8080be851
Create Date: 2026-11-04 17:00:00

This migration is intentionally additive. Existing operational rows remain valid
with a nullable institution link until an administrator supplies verified scoping
data in a later migration phase.
"""

from alembic import op
import sqlalchemy as sa


revision = "b6f0a9e2c741"
down_revision = "08f8080be851"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "institutions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_institutions_active", "institutions", ["active"])
    op.create_index("ix_institutions_code", "institutions", ["code"], unique=True)

    op.create_table(
        "academic_years",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_academic_years_date_order"),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "institution_id", "label", name="uq_academic_years_institution_label"
        ),
    )
    op.create_index("ix_academic_years_active", "academic_years", ["active"])
    op.create_index(
        "ix_academic_years_institution_id", "academic_years", ["institution_id"]
    )

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["institution_id"], ["institutions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "institution_id", "code", name="uq_departments_institution_code"
        ),
    )
    op.create_index("ix_departments_active", "departments", ["active"])
    op.create_index("ix_departments_institution_id", "departments", ["institution_id"])

    op.create_table(
        "semesters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("academic_year_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date >= start_date", name="ck_semesters_date_order"),
        sa.CheckConstraint("number > 0", name="ck_semesters_number_positive"),
        sa.ForeignKeyConstraint(
            ["academic_year_id"], ["academic_years.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("academic_year_id", "number", name="uq_semesters_year_number"),
    )
    op.create_index("ix_semesters_academic_year_id", "semesters", ["academic_year_id"])
    op.create_index("ix_semesters_active", "semesters", ["active"])

    op.create_table(
        "specialisations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id", "code", name="uq_specialisations_department_code"
        ),
    )
    op.create_index("ix_specialisations_active", "specialisations", ["active"])
    op.create_index(
        "ix_specialisations_department_id", "specialisations", ["department_id"]
    )

    op.create_table(
        "sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("specialisation_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["specialisation_id"], ["specialisations.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "semester_id", "department_id", "code",
            name="uq_sections_academic_identity",
        ),
    )
    op.create_index("ix_sections_active", "sections", ["active"])
    op.create_index("ix_sections_department_id", "sections", ["department_id"])
    op.create_index("ix_sections_semester_id", "sections", ["semester_id"])
    op.create_index("ix_sections_specialisation_id", "sections", ["specialisation_id"])

    for table_name in (
        "users",
        "datasets",
        "students",
        "subjects",
        "exams",
        "rooms",
        "invigilators",
        "data_imports",
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("institution_id", sa.Integer(), nullable=True))
            batch_op.create_index(
                f"ix_{table_name}_institution_id", ["institution_id"], unique=False
            )
            batch_op.create_foreign_key(
                f"fk_{table_name}_institution_id_institutions",
                "institutions",
                ["institution_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    op.create_table(
        "student_academic_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("registration_number", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_student_academic_records_date_order",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'withdrawn', 'transferred')",
            name="ck_student_academic_records_status",
        ),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "student_id", "section_id", "valid_from",
            name="uq_student_academic_records_period",
        ),
    )
    op.create_index(
        "ix_student_academic_records_registration_number",
        "student_academic_records",
        ["registration_number"],
    )
    op.create_index(
        "ix_student_academic_records_section_id",
        "student_academic_records",
        ["section_id"],
    )
    op.create_index(
        "ix_student_academic_records_student_status",
        "student_academic_records",
        ["student_id", "status"],
    )


def downgrade():
    op.drop_index(
        "ix_student_academic_records_student_status",
        table_name="student_academic_records",
    )
    op.drop_index(
        "ix_student_academic_records_section_id",
        table_name="student_academic_records",
    )
    op.drop_index(
        "ix_student_academic_records_registration_number",
        table_name="student_academic_records",
    )
    op.drop_table("student_academic_records")

    for table_name in reversed((
        "users",
        "datasets",
        "students",
        "subjects",
        "exams",
        "rooms",
        "invigilators",
        "data_imports",
    )):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table_name}_institution_id_institutions", type_="foreignkey"
            )
            batch_op.drop_index(f"ix_{table_name}_institution_id")
            batch_op.drop_column("institution_id")

    op.drop_index("ix_sections_specialisation_id", table_name="sections")
    op.drop_index("ix_sections_semester_id", table_name="sections")
    op.drop_index("ix_sections_department_id", table_name="sections")
    op.drop_index("ix_sections_active", table_name="sections")
    op.drop_table("sections")
    op.drop_index("ix_specialisations_department_id", table_name="specialisations")
    op.drop_index("ix_specialisations_active", table_name="specialisations")
    op.drop_table("specialisations")
    op.drop_index("ix_semesters_active", table_name="semesters")
    op.drop_index("ix_semesters_academic_year_id", table_name="semesters")
    op.drop_table("semesters")
    op.drop_index("ix_departments_institution_id", table_name="departments")
    op.drop_index("ix_departments_active", table_name="departments")
    op.drop_table("departments")
    op.drop_index("ix_academic_years_institution_id", table_name="academic_years")
    op.drop_index("ix_academic_years_active", table_name="academic_years")
    op.drop_table("academic_years")
    op.drop_index("ix_institutions_code", table_name="institutions")
    op.drop_index("ix_institutions_active", table_name="institutions")
    op.drop_table("institutions")
