"""Real-data import and deterministic synthetic-data generation services."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import random
import re
from datetime import date, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    DataImport,
    Dataset,
    Exam,
    ExamRegistration,
    Invigilator,
    Room,
    Seat,
    Student,
    Subject,
)


MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5_000


class DataValidationError(ValueError):
    """Raised when imported or generated data is unsafe or internally inconsistent."""


def _normalized_headers(fieldnames: list[str] | None) -> dict[str, str]:
    aliases = {
        "id": {"id", "student_id", "student id", "roll_no", "roll number"},
        "name": {"name", "student_name", "student name", "full_name"},
        "class": {"class", "section", "class/section"},
        "department": {"department", "dept"},
        "subject_code": {"subject_code", "subject code", "course_code", "course code"},
        "subject": {"subject", "course", "paper", "exam"},
        "special_needs": {
            "special_needs", "special needs", "accessible", "disability", "pwd"
        },
    }
    normalized: dict[str, str] = {}
    for original in fieldnames or []:
        cleaned = original.strip().lower()
        for canonical, candidates in aliases.items():
            if cleaned in candidates:
                normalized[canonical] = original
                break
    return normalized


def _limited(value: str, label: str, maximum: int, *, required: bool = False) -> str:
    cleaned = (value or "").strip()
    if required and not cleaned:
        raise DataValidationError(f"{label} is required.")
    if len(cleaned) > maximum:
        raise DataValidationError(f"{label} must be {maximum} characters or fewer.")
    return cleaned


def _subject_code(raw_code: str, subject_name: str) -> str:
    code = re.sub(r"[^A-Z0-9-]+", "-", raw_code.strip().upper()).strip("-")
    if code:
        return code[:50]
    readable = re.sub(r"[^A-Z0-9]+", "-", subject_name.upper()).strip("-")[:36]
    digest = hashlib.sha256(subject_name.encode("utf-8")).hexdigest()[:8].upper()
    return f"{readable or 'SUBJECT'}-{digest}"[:50]


def parse_student_import(file_storage) -> list[dict[str, Any]]:
    raw = file_storage.read()
    if not isinstance(raw, bytes):
        raise DataValidationError("The uploaded CSV could not be read as bytes.")
    if len(raw) > MAX_IMPORT_BYTES:
        raise DataValidationError("The uploaded CSV exceeds the 2 MiB limit.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataValidationError("The uploaded CSV must be UTF-8 encoded.") from exc

    try:
        reader = csv.DictReader(io.StringIO(text))
        headers = _normalized_headers(reader.fieldnames)
        if not {"id", "class", "subject"}.issubset(headers):
            raise DataValidationError(
                "CSV headers must include ID, Class, and Subject. Name, Department, "
                "Subject_Code, and Special_Needs are optional."
            )

        rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        true_values = {"Y", "YES", "TRUE", "1", "T"}
        false_values = {"", "N", "NO", "FALSE", "0", "F"}
        for line_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                raise DataValidationError(
                    f"Line {line_number}: the row contains more values than the CSV header."
                )
            if not any(
                str(value or "").strip() for value in raw_row.values()
            ):
                continue
            student_code = _limited(
                raw_row.get(headers["id"], ""), f"Line {line_number} student ID", 64, required=True
            )
            normalized_id = student_code.casefold()
            if normalized_id in seen_ids:
                raise DataValidationError(
                    f"Line {line_number}: duplicate student ID '{student_code}'."
                )
            seen_ids.add(normalized_id)

            class_name = _limited(
                raw_row.get(headers["class"], ""),
                f"Line {line_number} class",
                100,
                required=True,
            )
            subject_name = _limited(
                raw_row.get(headers["subject"], ""),
                f"Line {line_number} subject",
                150,
                required=True,
            )
            special_raw = raw_row.get(headers.get("special_needs", ""), "").strip().upper()
            if special_raw not in true_values | false_values:
                raise DataValidationError(
                    f"Line {line_number}: Special_Needs must be Y/N, True/False, or 1/0."
                )

            raw_subject_code = raw_row.get(headers.get("subject_code", ""), "")
            rows.append({
                "student_code": student_code,
                "full_name": _limited(
                    raw_row.get(headers.get("name", ""), ""),
                    f"Line {line_number} name",
                    150,
                ) or None,
                "class_name": class_name,
                "department": _limited(
                    raw_row.get(headers.get("department", ""), ""),
                    f"Line {line_number} department",
                    100,
                ) or None,
                "subject_code": _subject_code(raw_subject_code, subject_name),
                "subject_name": subject_name,
                "accessibility_required": special_raw in true_values,
            })
            if len(rows) > MAX_IMPORT_ROWS:
                raise DataValidationError(
                    f"A maximum of {MAX_IMPORT_ROWS:,} students can be imported at once."
                )
    except csv.Error as exc:
        raise DataValidationError(f"Malformed CSV: {exc}") from exc

    if not rows:
        raise DataValidationError("The CSV does not contain any student records.")
    return rows


def _record_failed_import(import_id: int, message: str) -> None:
    db.session.rollback()
    record = db.session.get(DataImport, import_id)
    if record is not None:
        record.status = "failed"
        record.error_report = {"message": message[:1000]}
        db.session.commit()


def import_real_exam_dataset(
    file_storage,
    *,
    dataset_name: str,
    dataset_version: str,
    exam_date: date,
    start_time: time,
    end_time: time,
    session_name: str,
    academic_year: str,
) -> tuple[Dataset, Exam, DataImport]:
    original_filename = (getattr(file_storage, "filename", None) or "students.csv")[:255]
    import_record = DataImport(
        import_type="students",
        source_type="real",
        original_filename=original_filename,
        status="processing",
    )
    db.session.add(import_record)
    db.session.commit()
    import_id = import_record.id

    try:
        dataset_name = _limited(dataset_name, "Dataset name", 150, required=True)
        dataset_version = _limited(dataset_version, "Dataset version", 50, required=True)
        session_name = _limited(session_name, "Session name", 80, required=True)
        academic_year = _limited(academic_year, "Academic year", 20, required=True)
        if end_time <= start_time:
            raise DataValidationError("Exam end time must be later than start time.")
        if db.session.scalar(
            select(Dataset).where(
                Dataset.name == dataset_name, Dataset.version == dataset_version
            )
        ):
            raise DataValidationError("A dataset with this name and version already exists.")

        rows = parse_student_import(file_storage)
        dataset = Dataset(
            name=dataset_name,
            version=dataset_version,
            source_type="real",
            description=f"Imported from {original_filename}",
            status="active",
        )
        db.session.add(dataset)
        db.session.flush()

        subjects: dict[str, Subject] = {}
        for row in rows:
            code = row["subject_code"]
            if code in subjects:
                if subjects[code].name != row["subject_name"]:
                    raise DataValidationError(
                        f"Subject code '{code}' is mapped to multiple subject names."
                    )
                continue
            subject = db.session.scalar(select(Subject).where(Subject.code == code))
            if subject is None:
                subject = Subject(
                    code=code,
                    name=row["subject_name"],
                    department=row["department"],
                    source_type="real",
                    dataset=dataset,
                )
                db.session.add(subject)
            elif subject.name.casefold() != row["subject_name"].casefold():
                raise DataValidationError(
                    f"Existing subject code '{code}' belongs to '{subject.name}', not "
                    f"'{row['subject_name']}'."
                )
            subjects[code] = subject

        db.session.flush()
        primary_subject = subjects[rows[0]["subject_code"]]
        exam = Exam(
            subject=primary_subject,
            exam_date=exam_date,
            start_time=start_time,
            end_time=end_time,
            session_name=session_name,
            academic_year=academic_year,
            status="scheduled",
            source_type="real",
            dataset=dataset,
        )
        db.session.add(exam)
        db.session.flush()

        for row in rows:
            student = db.session.scalar(
                select(Student).where(Student.student_code == row["student_code"])
            )
            if student is None:
                student = Student(
                    student_code=row["student_code"],
                    source_type="real",
                )
                db.session.add(student)
            elif student.source_type != "real":
                raise DataValidationError(
                    f"Student ID '{row['student_code']}' already belongs to synthetic data."
                )
            student.full_name = row["full_name"]
            student.class_name = row["class_name"]
            student.department = row["department"]
            student.accessibility_required = row["accessibility_required"]
            student.active = True
            student.dataset = dataset
            db.session.flush()
            db.session.add(
                ExamRegistration(
                    exam=exam,
                    student=student,
                    subject=subjects[row["subject_code"]],
                    status="eligible",
                )
            )

        import_record = db.session.get(DataImport, import_id)
        import_record.dataset = dataset
        import_record.status = "completed"
        import_record.valid_rows = len(rows)
        import_record.invalid_rows = 0
        db.session.commit()
        return dataset, exam, import_record
    except (DataValidationError, IntegrityError) as exc:
        message = str(exc)
        _record_failed_import(import_id, message)
        if isinstance(exc, DataValidationError):
            raise
        raise DataValidationError("The import violates an existing database constraint.") from exc
    except Exception as exc:
        _record_failed_import(import_id, "Unexpected import failure")
        raise


def generate_synthetic_dataset(
    *,
    dataset_name: str,
    dataset_version: str,
    seed: int,
    student_count: int,
    class_count: int,
    subject_count: int,
    room_count: int,
    rows_per_room: int,
    benches_per_row: int,
    seats_per_bench: int,
    exam_date: date,
) -> tuple[Dataset, Exam]:
    dataset_name = _limited(dataset_name, "Dataset name", 150, required=True)
    dataset_version = _limited(dataset_version, "Dataset version", 50, required=True)
    numeric_limits = {
        "Seed": (seed, 0, 2_147_483_647),
        "Student count": (student_count, 1, 5_000),
        "Class count": (class_count, 1, 100),
        "Subject count": (subject_count, 1, 100),
        "Room count": (room_count, 1, 100),
        "Rows per room": (rows_per_room, 1, 100),
        "Benches per row": (benches_per_row, 1, 100),
        "Seats per bench": (seats_per_bench, 1, 10),
    }
    for label, (value, minimum, maximum) in numeric_limits.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise DataValidationError(f"{label} must be a whole number.")
        if not minimum <= value <= maximum:
            raise DataValidationError(
                f"{label} must be between {minimum:,} and {maximum:,}."
            )
    if db.session.scalar(
        select(Dataset).where(
            Dataset.name == dataset_name, Dataset.version == dataset_version
        )
    ):
        raise DataValidationError("A dataset with this name and version already exists.")
    total_capacity = room_count * rows_per_room * benches_per_row * seats_per_bench
    if total_capacity < student_count:
        raise DataValidationError(
            f"Generated room capacity ({total_capacity}) is smaller than the student count "
            f"({student_count})."
        )

    rng = random.Random(seed)
    token_input = f"{dataset_name}|{dataset_version}|{seed}".encode("utf-8")
    token = hashlib.sha256(token_input).hexdigest()[:8].upper()

    try:
        dataset = Dataset(
            name=dataset_name,
            version=dataset_version,
            source_type="synthetic",
            generation_seed=seed,
            description="Deterministic synthetic examination dataset",
            status="active",
        )
        db.session.add(dataset)
        db.session.flush()

        subjects = []
        for index in range(1, subject_count + 1):
            subject = Subject(
                code=f"SYN-{token}-SUB{index:02d}",
                name=f"Synthetic Subject {index:02d}",
                department=f"Department {(index - 1) % max(1, class_count) + 1:02d}",
                source_type="synthetic",
                dataset=dataset,
            )
            db.session.add(subject)
            subjects.append(subject)
        db.session.flush()

        exam = Exam(
            subject=subjects[0],
            exam_date=exam_date,
            start_time=time(9, 0),
            end_time=time(12, 0),
            session_name="Synthetic Morning Session",
            academic_year=f"{exam_date.year}-{str(exam_date.year + 1)[-2:]}",
            status="scheduled",
            source_type="synthetic",
            dataset=dataset,
        )
        db.session.add(exam)

        for index in range(1, student_count + 1):
            class_number = ((index - 1) % class_count) + 1
            subject = subjects[rng.randrange(subject_count)]
            student = Student(
                student_code=f"SYN-{token}-{index:05d}",
                full_name=f"Synthetic Student {index:05d}",
                class_name=f"SYN-CLASS-{class_number:02d}",
                department=f"Department {((class_number - 1) % 5) + 1:02d}",
                accessibility_required=(index % 23 == 0),
                active=True,
                source_type="synthetic",
                dataset=dataset,
            )
            db.session.add(student)
            db.session.add(
                ExamRegistration(
                    exam=exam,
                    student=student,
                    subject=subject,
                    status="eligible",
                )
            )

        for room_index in range(1, room_count + 1):
            room = Room(
                room_number=f"SYN-{token}-R{room_index:02d}",
                building="Synthetic Academic Block",
                floor=str((room_index - 1) // 5 + 1),
                rows=rows_per_room,
                benches_per_row=benches_per_row,
                seats_per_bench=seats_per_bench,
                capacity=rows_per_room * benches_per_row * seats_per_bench,
                source_type="synthetic",
                dataset=dataset,
            )
            db.session.add(room)
            db.session.flush()
            for row_number in range(1, rows_per_room + 1):
                for bench_number in range(1, benches_per_row + 1):
                    for seat_number in range(1, seats_per_bench + 1):
                        db.session.add(
                            Seat(
                                room=room,
                                seat_code=(
                                    f"{room.room_number}-R{row_number}-B{bench_number}-S{seat_number}"
                                ),
                                row_number=row_number,
                                bench_number=bench_number,
                                seat_number=seat_number,
                                accessible=(row_number == 1 or seat_number in {1, seats_per_bench}),
                                active=True,
                            )
                        )

        for index in range(1, max(room_count, math.ceil(student_count / 20)) + 1):
            db.session.add(
                Invigilator(
                    employee_code=f"SYN-{token}-INV{index:03d}",
                    full_name=f"Synthetic Invigilator {index:03d}",
                    department=f"Department {((index - 1) % 5) + 1:02d}",
                    source_type="synthetic",
                    dataset=dataset,
                )
            )

        db.session.add(
            DataImport(
                dataset=dataset,
                import_type="students",
                source_type="synthetic",
                status="completed",
                valid_rows=student_count,
                invalid_rows=0,
            )
        )
        db.session.commit()
        return dataset, exam
    except IntegrityError as exc:
        db.session.rollback()
        raise DataValidationError(
            "The generated dataset conflicts with existing identifiers. Change its name, version, or seed."
        ) from exc
    except Exception:
        db.session.rollback()
        raise


def create_room_with_seats(
    *,
    dataset_id: int,
    room_number: str,
    building: str,
    floor: str | None,
    rows: int,
    benches_per_row: int,
    seats_per_bench: int,
    accessible_rule: str,
) -> Room:
    dataset = db.session.get(Dataset, dataset_id)
    if dataset is None:
        raise DataValidationError("The selected dataset does not exist.")
    room_number = _limited(room_number, "Room number", 50, required=True)
    building = _limited(building, "Building", 100, required=True)
    if db.session.scalar(
        select(Room).where(Room.building == building, Room.room_number == room_number)
    ):
        raise DataValidationError("This building and room number already exists.")

    try:
        room = Room(
            room_number=room_number,
            building=building,
            floor=_limited(floor or "", "Floor", 50) or None,
            rows=rows,
            benches_per_row=benches_per_row,
            seats_per_bench=seats_per_bench,
            capacity=rows * benches_per_row * seats_per_bench,
            source_type=dataset.source_type,
            dataset=dataset,
        )
        db.session.add(room)
        db.session.flush()
        for row_number in range(1, rows + 1):
            for bench_number in range(1, benches_per_row + 1):
                for seat_number in range(1, seats_per_bench + 1):
                    is_front = row_number == 1
                    is_aisle = seat_number in {1, seats_per_bench}
                    accessible = (
                        (accessible_rule == "row_1" and is_front)
                        or (accessible_rule == "aisle" and is_aisle)
                        or (accessible_rule == "row_1_and_aisle" and (is_front or is_aisle))
                        or (accessible_rule == "front_aisle_only" and is_front and is_aisle)
                    )
                    db.session.add(
                        Seat(
                            room=room,
                            seat_code=(
                                f"{room.room_number}-R{row_number}-B{bench_number}-S{seat_number}"
                            ),
                            row_number=row_number,
                            bench_number=bench_number,
                            seat_number=seat_number,
                            accessible=accessible,
                            active=True,
                        )
                    )
        db.session.commit()
        return room
    except IntegrityError as exc:
        db.session.rollback()
        raise DataValidationError("The room or one of its seat codes already exists.") from exc
