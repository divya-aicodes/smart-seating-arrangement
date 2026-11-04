"""Server-rendered data-management routes for real and synthetic datasets."""

import csv
import io
import json
from datetime import date, datetime

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from data_service import (
    DataValidationError,
    commit_staged_student_import,
    create_room_with_seats,
    generate_synthetic_dataset,
    stage_student_import,
)
from extensions import db
from models import DataImport, Dataset, ExamSession, ImportRowError, Room, Student
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
    sessions = db.session.scalars(
        select(ExamSession)
        .options(selectinload(ExamSession.papers))
        .order_by(ExamSession.exam_date.desc(), ExamSession.start_time.desc())
        .limit(50)
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
        "exams": db.session.scalar(select(func.count()).select_from(ExamSession)),
        "rooms": db.session.scalar(select(func.count()).select_from(Room)),
    }
    return render_template(
        "data_management.html",
        datasets=datasets,
        sessions=sessions,
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
        import_record, reused = stage_student_import(
            csv_file,
            dataset_name=request.form.get("dataset_name", ""),
            dataset_version=request.form.get("dataset_version", ""),
            exam_date=_form_date("exam_date", "Exam date"),
            start_time=_form_time("start_time", "Start time"),
            end_time=_form_time("end_time", "End time"),
            session_name=request.form.get("session_name", ""),
            academic_year=request.form.get("academic_year", ""),
            chunk_size=int(current_app.config["IMPORT_CHUNK_SIZE"]),
        )
        if reused:
            flash(
                f"This exact upload was already staged as import #{import_record.id}. "
                "Its existing validation result was reused safely.",
                "info",
            )
        elif import_record.status == "validated":
            flash(
                f"Import #{import_record.id} validated {import_record.valid_rows:,} rows; "
                f"{import_record.invalid_rows:,} rows need review. Confirm the import below "
                "before operational records are created.",
                "success",
            )
        else:
            flash(
                f"Import #{import_record.id} contains no valid rows. Download its error report.",
                "error",
            )
        record_audit(
            "real_dataset_import_staged",
            "data_import",
            import_record.id,
            after_values={
                "valid_rows": import_record.valid_rows,
                "invalid_rows": import_record.invalid_rows,
                "reused": reused,
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


@data_bp.post("/data/imports/<int:import_id>/commit")
def commit_import(import_id: int):
    try:
        dataset, exam, import_record = commit_staged_student_import(import_id)
        flash(
            f"Committed {import_record.valid_rows:,} verified rows to '{dataset.name}' "
            f"and created exam session #{exam.exam_session_id} with "
            f"{len(exam.session.papers)} paper(s). "
            f"{import_record.invalid_rows:,} invalid rows remain in the error report.",
            "success",
        )
        record_audit(
            "real_dataset_import_committed",
            "data_import",
            import_record.id,
            after_values={
                "dataset_id": dataset.id,
                "exam_session_id": exam.exam_session_id,
                "exam_id": exam.id,
                "valid_rows": import_record.valid_rows,
                "invalid_rows": import_record.invalid_rows,
            },
        )
    except DataValidationError as exc:
        flash(str(exc), "error")
    return redirect(url_for("data.data_management"))


def _safe_csv_cell(value):
    text = str(value if value is not None else "")
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


@data_bp.get("/data/imports/<int:import_id>/errors.csv")
def import_errors(import_id: int):
    import_record = db.session.get(DataImport, import_id)
    if import_record is None:
        abort(404, description="The requested import does not exist.")
    errors = db.session.scalars(
        select(ImportRowError)
        .where(ImportRowError.data_import_id == import_id)
        .order_by(ImportRowError.row_number, ImportRowError.id)
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Row", "Error Code", "Explanation", "Original Values"])
    for item in errors:
        writer.writerow([
            item.row_number,
            _safe_csv_cell(item.error_code),
            _safe_csv_cell(item.message),
            _safe_csv_cell(json.dumps(item.raw_data or {}, ensure_ascii=False, sort_keys=True)),
        ])
    record_audit(
        "import_error_report_downloaded",
        "data_import",
        import_id,
        after_values={"invalid_rows": import_record.invalid_rows},
    )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment;filename=seatplan-import-{import_id}-errors.csv"
        },
    )


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
                "seats_per_bench", 2, 1, 4, "Seats per bench"
            ),
            exam_date=_form_date("exam_date", "Exam date"),
        )
        flash(
            f"Generated reproducible synthetic dataset '{dataset.name}' with seed "
            f"{dataset.generation_seed}. Exam session #{exam.exam_session_id} with "
            f"{len(exam.session.papers)} paper(s) is ready.",
            "success",
        )
        record_audit(
            "synthetic_dataset_generated",
            "dataset",
            dataset.id,
            after_values={
                "exam_session_id": exam.exam_session_id,
                "exam_id": exam.id,
                "seed": dataset.generation_seed,
            },
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
            seats_per_bench=_bounded_int("seats_per_bench", 2, 1, 4, "Seats per bench"),
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
