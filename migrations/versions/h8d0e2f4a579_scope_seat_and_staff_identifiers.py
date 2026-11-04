"""scope seat and staff identifiers

Revision ID: h8d0e2f4a579
Revises: g7c9d1e3f468
Create Date: 2026-11-05 01:35:00.000000
"""

from alembic import op


revision = "h8d0e2f4a579"
down_revision = "g7c9d1e3f468"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("seats") as batch_op:
        batch_op.drop_index("ix_seats_seat_code")
        batch_op.create_index("ix_seats_seat_code", ["seat_code"], unique=False)
        batch_op.create_unique_constraint("uq_seats_room_code", ["room_id", "seat_code"])
    with op.batch_alter_table("invigilators") as batch_op:
        batch_op.drop_index("ix_invigilators_employee_code")
        batch_op.create_index(
            "ix_invigilators_employee_code", ["employee_code"], unique=False
        )
        batch_op.create_unique_constraint(
            "uq_invigilators_institution_code", ["institution_id", "employee_code"]
        )
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=ON")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("invigilators") as batch_op:
        batch_op.drop_constraint("uq_invigilators_institution_code", type_="unique")
        batch_op.drop_index("ix_invigilators_employee_code")
        batch_op.create_index("ix_invigilators_employee_code", ["employee_code"], unique=True)
    with op.batch_alter_table("seats") as batch_op:
        batch_op.drop_constraint("uq_seats_room_code", type_="unique")
        batch_op.drop_index("ix_seats_seat_code")
        batch_op.create_index("ix_seats_seat_code", ["seat_code"], unique=True)
    if bind.dialect.name == "sqlite":
        op.execute("PRAGMA foreign_keys=ON")
