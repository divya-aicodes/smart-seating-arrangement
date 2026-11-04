"""Persistent domain model for examination seating management.

The schema is PostgreSQL-ready and remains SQLite-compatible for local development.
Synthetic and institutional records use the same tables and are distinguished through
explicit dataset lineage rather than separate demo-only structures.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Institution(TimestampMixin, db.Model):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    users: Mapped[list[User]] = relationship(back_populates="institution")
    departments: Mapped[list[Department]] = relationship(back_populates="institution")
    academic_years: Mapped[list[AcademicYear]] = relationship(back_populates="institution")
    datasets: Mapped[list[Dataset]] = relationship(back_populates="institution")
    students: Mapped[list[Student]] = relationship(back_populates="institution")
    subjects: Mapped[list[Subject]] = relationship(back_populates="institution")
    exams: Mapped[list[Exam]] = relationship(back_populates="institution")
    rooms: Mapped[list[Room]] = relationship(back_populates="institution")
    invigilators: Mapped[list[Invigilator]] = relationship(back_populates="institution")
    imports: Mapped[list[DataImport]] = relationship(back_populates="institution")


class Department(TimestampMixin, db.Model):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("institution_id", "code", name="uq_departments_institution_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    institution: Mapped[Institution] = relationship(back_populates="departments")
    specialisations: Mapped[list[Specialisation]] = relationship(back_populates="department")
    sections: Mapped[list[Section]] = relationship(back_populates="department")


class Specialisation(TimestampMixin, db.Model):
    __tablename__ = "specialisations"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_specialisations_department_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    department: Mapped[Department] = relationship(back_populates="specialisations")
    sections: Mapped[list[Section]] = relationship(back_populates="specialisation")


class AcademicYear(TimestampMixin, db.Model):
    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("institution_id", "label", name="uq_academic_years_institution_label"),
        CheckConstraint("end_date >= start_date", name="ck_academic_years_date_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    institution: Mapped[Institution] = relationship(back_populates="academic_years")
    semesters: Mapped[list[Semester]] = relationship(back_populates="academic_year")


class Semester(TimestampMixin, db.Model):
    __tablename__ = "semesters"
    __table_args__ = (
        UniqueConstraint("academic_year_id", "number", name="uq_semesters_year_number"),
        CheckConstraint("number > 0", name="ck_semesters_number_positive"),
        CheckConstraint("end_date >= start_date", name="ck_semesters_date_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    academic_year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    academic_year: Mapped[AcademicYear] = relationship(back_populates="semesters")
    sections: Mapped[list[Section]] = relationship(back_populates="semester")


class Section(TimestampMixin, db.Model):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint(
            "semester_id", "department_id", "code",
            name="uq_sections_academic_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(
        ForeignKey("semesters.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    specialisation_id: Mapped[int | None] = mapped_column(
        ForeignKey("specialisations.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    semester: Mapped[Semester] = relationship(back_populates="sections")
    department: Mapped[Department] = relationship(back_populates="sections")
    specialisation: Mapped[Specialisation | None] = relationship(back_populates="sections")
    academic_records: Mapped[list[StudentAcademicRecord]] = relationship(
        back_populates="section"
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'planner', 'viewer')", name="ck_users_role"),
        CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_logins_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    institution: Mapped[Institution | None] = relationship(back_populates="users")

    @property
    def is_active(self) -> bool:
        return self.active

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Dataset(TimestampMixin, db.Model):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_datasets_name_version"),
        CheckConstraint("source_type IN ('real', 'synthetic')", name="ck_datasets_source_type"),
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_datasets_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    generation_seed: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")

    institution: Mapped[Institution | None] = relationship(back_populates="datasets")
    students: Mapped[list[Student]] = relationship(back_populates="dataset")
    subjects: Mapped[list[Subject]] = relationship(back_populates="dataset")
    exams: Mapped[list[Exam]] = relationship(back_populates="dataset")
    rooms: Mapped[list[Room]] = relationship(back_populates="dataset")
    invigilators: Mapped[list[Invigilator]] = relationship(back_populates="dataset")
    imports: Mapped[list[DataImport]] = relationship(back_populates="dataset")


class Student(TimestampMixin, db.Model):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint("source_type IN ('real', 'synthetic')", name="ck_students_source_type"),
        Index("ix_students_class_department", "class_name", "department"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    student_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(150))
    class_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    accessibility_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(150))
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )

    institution: Mapped[Institution | None] = relationship(back_populates="students")
    dataset: Mapped[Dataset | None] = relationship(back_populates="students")
    registrations: Mapped[list[ExamRegistration]] = relationship(back_populates="student")
    allocations: Mapped[list[SeatAllocation]] = relationship(back_populates="student")
    academic_records: Mapped[list[StudentAcademicRecord]] = relationship(
        back_populates="student"
    )


class StudentAcademicRecord(TimestampMixin, db.Model):
    __tablename__ = "student_academic_records"
    __table_args__ = (
        UniqueConstraint(
            "student_id", "section_id", "valid_from",
            name="uq_student_academic_records_period",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_student_academic_records_date_order",
        ),
        CheckConstraint(
            "status IN ('active', 'completed', 'withdrawn', 'transferred')",
            name="ck_student_academic_records_status",
        ),
        Index("ix_student_academic_records_student_status", "student_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    registration_number: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)

    student: Mapped[Student] = relationship(back_populates="academic_records")
    section: Mapped[Section] = relationship(back_populates="academic_records")


class Subject(TimestampMixin, db.Model):
    __tablename__ = "subjects"
    __table_args__ = (
        CheckConstraint("source_type IN ('real', 'synthetic')", name="ck_subjects_source_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="real", server_default="real"
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )

    institution: Mapped[Institution | None] = relationship(back_populates="subjects")
    dataset: Mapped[Dataset | None] = relationship(back_populates="subjects")
    exams: Mapped[list[Exam]] = relationship(back_populates="subject")
    registrations: Mapped[list[ExamRegistration]] = relationship(back_populates="subject")


class Exam(TimestampMixin, db.Model):
    __tablename__ = "exams"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'completed', 'cancelled')",
            name="ck_exams_status",
        ),
        CheckConstraint("end_time > start_time", name="ck_exams_time_order"),
        CheckConstraint("source_type IN ('real', 'synthetic')", name="ck_exams_source_type"),
        Index("ix_exams_date_session", "exam_date", "session_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    exam_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    session_name: Mapped[str] = mapped_column(String(80), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="real", server_default="real"
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )

    institution: Mapped[Institution | None] = relationship(back_populates="exams")
    dataset: Mapped[Dataset | None] = relationship(back_populates="exams")
    subject: Mapped[Subject] = relationship(back_populates="exams")
    registrations: Mapped[list[ExamRegistration]] = relationship(back_populates="exam")
    seating_runs: Mapped[list[SeatingRun]] = relationship(back_populates="exam")
    invigilator_assignments: Mapped[list[InvigilatorAssignment]] = relationship(
        back_populates="exam"
    )


class ExamRegistration(TimestampMixin, db.Model):
    __tablename__ = "exam_registrations"
    __table_args__ = (
        UniqueConstraint("exam_id", "student_id", name="uq_exam_registrations_exam_student"),
        CheckConstraint(
            "status IN ('registered', 'eligible', 'ineligible', 'withdrawn')",
            name="ck_exam_registrations_status",
        ),
        Index("ix_exam_registrations_exam_status", "exam_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="registered")

    exam: Mapped[Exam] = relationship(back_populates="registrations")
    student: Mapped[Student] = relationship(back_populates="registrations")
    subject: Mapped[Subject | None] = relationship(back_populates="registrations")


class Room(TimestampMixin, db.Model):
    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("building", "room_number", name="uq_rooms_building_number"),
        CheckConstraint("rows > 0", name="ck_rooms_rows_positive"),
        CheckConstraint("benches_per_row > 0", name="ck_rooms_benches_positive"),
        CheckConstraint("seats_per_bench > 0", name="ck_rooms_seats_positive"),
        CheckConstraint("capacity > 0", name="ck_rooms_capacity_positive"),
        CheckConstraint(
            "capacity <= rows * benches_per_row * seats_per_bench",
            name="ck_rooms_capacity_within_layout",
        ),
        CheckConstraint("source_type IN ('real', 'synthetic')", name="ck_rooms_source_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    room_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    building: Mapped[str] = mapped_column(String(100), nullable=False, default="Main")
    floor: Mapped[str | None] = mapped_column(String(50))
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    benches_per_row: Mapped[int] = mapped_column(Integer, nullable=False)
    seats_per_bench: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="real", server_default="real"
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )

    institution: Mapped[Institution | None] = relationship(back_populates="rooms")
    dataset: Mapped[Dataset | None] = relationship(back_populates="rooms")
    seats: Mapped[list[Seat]] = relationship(back_populates="room")
    invigilator_assignments: Mapped[list[InvigilatorAssignment]] = relationship(
        back_populates="room"
    )


class Seat(TimestampMixin, db.Model):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint(
            "room_id", "row_number", "bench_number", "seat_number",
            name="uq_seats_room_position",
        ),
        CheckConstraint("row_number > 0", name="ck_seats_row_positive"),
        CheckConstraint("bench_number > 0", name="ck_seats_bench_positive"),
        CheckConstraint("seat_number > 0", name="ck_seats_number_positive"),
        Index("ix_seats_room_active", "room_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    seat_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bench_number: Mapped[int] = mapped_column(Integer, nullable=False)
    seat_number: Mapped[int] = mapped_column(Integer, nullable=False)
    accessible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    room: Mapped[Room] = relationship(back_populates="seats")
    allocations: Mapped[list[SeatAllocation]] = relationship(back_populates="seat")


class SeatingRun(TimestampMixin, db.Model):
    __tablename__ = "seating_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'published')",
            name="ck_seating_runs_status",
        ),
        Index("ix_seating_runs_exam_status", "exam_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    solver_version: Mapped[str] = mapped_column(String(50), nullable=False)
    solver_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(150))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    exam: Mapped[Exam] = relationship(back_populates="seating_runs")
    allocations: Mapped[list[SeatAllocation]] = relationship(
        back_populates="seating_run", cascade="all, delete-orphan"
    )


class SeatAllocation(TimestampMixin, db.Model):
    __tablename__ = "seat_allocations"
    __table_args__ = (
        UniqueConstraint("seating_run_id", "student_id", name="uq_allocations_run_student"),
        UniqueConstraint("seating_run_id", "seat_id", name="uq_allocations_run_seat"),
        Index("ix_allocations_run_room", "seating_run_id", "room_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    seating_run_id: Mapped[int] = mapped_column(
        ForeignKey("seating_runs.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="RESTRICT"), nullable=False
    )
    hard_conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subject_relaxed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    seating_run: Mapped[SeatingRun] = relationship(back_populates="allocations")
    student: Mapped[Student] = relationship(back_populates="allocations")
    seat: Mapped[Seat] = relationship(back_populates="allocations")
    room: Mapped[Room] = relationship()


class Invigilator(TimestampMixin, db.Model):
    __tablename__ = "invigilators"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('real', 'synthetic')", name="ck_invigilators_source_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100), index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="real", server_default="real"
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )

    institution: Mapped[Institution | None] = relationship(back_populates="invigilators")
    dataset: Mapped[Dataset | None] = relationship(back_populates="invigilators")
    assignments: Mapped[list[InvigilatorAssignment]] = relationship(
        back_populates="invigilator"
    )


class InvigilatorAssignment(TimestampMixin, db.Model):
    __tablename__ = "invigilator_assignments"
    __table_args__ = (
        UniqueConstraint(
            "exam_id", "invigilator_id", name="uq_invigilator_assignments_exam_person"
        ),
        Index("ix_invigilator_assignments_exam_room", "exam_id", "room_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="RESTRICT"), nullable=False
    )
    invigilator_id: Mapped[int] = mapped_column(
        ForeignKey("invigilators.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="invigilator")

    exam: Mapped[Exam] = relationship(back_populates="invigilator_assignments")
    room: Mapped[Room] = relationship(back_populates="invigilator_assignments")
    invigilator: Mapped[Invigilator] = relationship(back_populates="assignments")


class DataImport(TimestampMixin, db.Model):
    __tablename__ = "data_imports"
    __table_args__ = (
        CheckConstraint(
            "import_type IN ('students', 'subjects', 'exams', 'registrations', 'rooms', 'invigilators')",
            name="ck_data_imports_type",
        ),
        CheckConstraint(
            "source_type IN ('real', 'synthetic')", name="ck_data_imports_source_type"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_data_imports_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="RESTRICT"), index=True
    )
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )
    import_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_report: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    institution: Mapped[Institution | None] = relationship(back_populates="imports")
    dataset: Mapped[Dataset | None] = relationship(back_populates="imports")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    before_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
