"""Server-rendered data-management routes for real and synthetic datasets."""

from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from data_service import (
    DataValidationError,
    create_room_with_seats,
    generate_synthetic_dataset,
    import_real_exam_dataset,
)
from extensions import db
from models import DataImport, Dataset, Exam, Room, Student
from security import record_audit


data_bp = Blueprint("data", __name__)


def _bounded_int(name, default, minimum, maximum, label):
    raw = request.form.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise DataValidationError(f"{label} must be a whole number.") from exc
    if not minimum <= value <= maximum:
        raise DataValidationError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _form_date(name, label):
    try:
        return date.fromisoformat(request.form.get(name, "").strip())
    except ValueError as exc:
        raise DataValidationError(f"{label} must be a valid date.") from exc


def _form_time(name, label):
    try:
        return datetime.strptime(request.form.get(name, "").strip(), "%H:%M").time()
    except ValueError as exc:
        raise DataValidationError(f"{label} must be a valid time.") from exc


@data_bp.get("/data")
def data_management():
    datasets = db.session.scalars(
        select(Dataset).order_by(Dataset.created_at.desc()).limit(50)
    ).all()
    exams = db.session.scalars(
        select(Exam).order_by(Exam.exam_date.desc(), Exam.start_time.desc()).limit(50)
    ).all()
    rooms = db.session.scalars(
        select(Room).where(Room.active.is_(True)).order_by(Room.building, Room.room_number)
    ).all()
    imports = db.session.scalars(
        select(DataImport).order_by(DataImport.created_at.desc()).limit(20)
    ).all()
    recent_students = db.session.scalars(
        select(Student)
        .options(selectinload(Student.dataset))
        .order_by(Student.created_at.desc(), Student.id.desc())
        .limit(50)
    ).all()
    stats = {
        "datasets": db.session.scalar(select(func.count()).select_from(Dataset)),
        "students": db.session.scalar(select(func.count()).select_from(Student)),
        "exams": db.session.scalar(select(func.count()).select_from(Exam)),
        "rooms": db.session.scalar(select(func.count()).select_from(Room)),
    }
    return render_template(
        "data_management.html",
        datasets=datasets,
        exams=exams,
        rooms=rooms,
        imports=imports,
        recent_students=recent_students,
        stats=stats,
        today=date.today().isoformat(),
    )


@data_bp.post("/data/import-students")
def import_students():
    csv_file = request.files.get("csv_file")
    if csv_file is None or not csv_file.filename:
        flash("Select a CSV file to import.", "error")
        return redirect(url_for("data.data_management"))
    try:
        dataset, exam, import_record = import_real_exam_dataset(
            csv_file,
            dataset_name=request.form.get("dataset_name", ""),
            dataset_version=request.form.get("dataset_version", ""),
            exam_date=_form_date("exam_date", "Exam date"),
            start_time=_form_time("start_time", "Start time"),
            end_time=_form_time("end_time", "End time"),
            session_name=request.form.get("session_name", ""),
            academic_year=request.form.get("academic_year", ""),
        )
        flash(
            f"Imported {import_record.valid_rows} real student records into "
            f"'{dataset.name}' and created exam session #{exam.id}.",
            "success",
        )
        record_audit(
            "real_dataset_imported",
            "dataset",
            dataset.id,
            after_values={
                "exam_id": exam.id,
                "import_id": import_record.id,
                "valid_rows": import_record.valid_rows,
            },
        )
    except DataValidationError as exc:
        record_audit(
            "real_dataset_import_rejected",
            "dataset",
            "not-created",
            after_values={"reason": str(exc)[:250]},
        )
        flash(str(exc), "error")
    return redirect(url_for("data.data_management"))


@data_bp.post("/data/generate-synthetic")
def generate_synthetic():
    try:
        dataset, exam = generate_synthetic_dataset(
            dataset_name=request.form.get("dataset_name", "").strip(),
            dataset_version=request.form.get("dataset_version", "").strip(),
            seed=_bounded_int("seed", 42, 0, 2_147_483_647, "Generation seed"),
            student_count=_bounded_int("student_count", 60, 2, 1000, "Student count"),
            class_count=_bounded_int("class_count", 6, 2, 50, "Class count"),
            subject_count=_bounded_int("subject_count", 4, 1, 20, "Subject count"),
            room_count=_bounded_int("room_count", 3, 1, 20, "Room count"),
            rows_per_room=_bounded_int("rows_per_room", 5, 1, 20, "Rows per room"),
            benches_per_row=_bounded_int(
                "benches_per_row", 3, 1, 15, "Benches per row"
            ),
            seats_per_bench=_bounded_int(
                "seats_per_bench", 2, 1, 2, "Seats per bench"
            ),
            exam_date=_form_date("exam_date", "Exam date"),
        )
        flash(
            f"Generated reproducible synthetic dataset '{dataset.name}' with seed "
            f"{dataset.generation_seed}. Exam session #{exam.id} is ready.",
            "success",
        )
        record_audit(
            "synthetic_dataset_generated",
            "dataset",
            dataset.id,
            after_values={"exam_id": exam.id, "seed": dataset.generation_seed},
        )
    except DataValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("data.data_management"))


@data_bp.post("/data/rooms")
def create_room():
    try:
        accessible_rule = request.form.get("accessible_rule", "row_1_and_aisle")
        if accessible_rule not in {"row_1", "aisle", "row_1_and_aisle", "front_aisle_only"}:
            raise DataValidationError("Invalid accessible-seat rule.")
        room = create_room_with_seats(
            dataset_id=_bounded_int("dataset_id", 0, 1, 2_147_483_647, "Dataset"),
            room_number=request.form.get("room_number", ""),
            building=request.form.get("building", ""),
            floor=request.form.get("floor", ""),
            rows=_bounded_int("rows", 4, 1, 20, "Rows"),
            benches_per_row=_bounded_int("benches_per_row", 3, 1, 15, "Benches per row"),
            seats_per_bench=_bounded_int("seats_per_bench", 2, 1, 2, "Seats per bench"),
            accessible_rule=accessible_rule,
        )
        flash(
            f"Created room {room.room_number} with {room.capacity} persisted seats.",
            "success",
        )
        record_audit(
            "room_created",
            "room",
            room.id,
            after_values={"dataset_id": room.dataset_id, "capacity": room.capacity},
        )
    except DataValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("data.data_management"))
