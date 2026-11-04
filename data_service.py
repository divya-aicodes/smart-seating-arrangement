"""Real-data import and deterministic synthetic-data generation services."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
import re
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from exam_service import (
    AcademicValidationError,
    create_exam_paper,
    create_exam_session,
    register_students_for_session,
)
from extensions import db
from models import (
    DataImport,
    Dataset,
    Exam,
    ImportRowError,
    ImportStagingStudent,
    Invigilator,
    Room,
    Seat,
    Student,
    Subject,
)


MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5_000
MAX_BULK_IMPORT_BYTES = 256 * 1024 * 1024
MAX_BULK_IMPORT_ROWS = 500_000
DEFAULT_IMPORT_CHUNK_SIZE = 5_000


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


def _upload_checksum(file_storage) -> tuple[str, int]:
    stream = getattr(file_storage, "stream", file_storage)
    if not hasattr(stream, "seek"):
        raise DataValidationError("The uploaded CSV stream must support safe retry processing.")
    stream.seek(0)
    digest = hashlib.sha256()
    total_bytes = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise DataValidationError("The uploaded CSV could not be read as bytes.")
        total_bytes += len(chunk)
        if total_bytes > MAX_BULK_IMPORT_BYTES:
            raise DataValidationError("The uploaded CSV exceeds the 256 MiB bulk-import limit.")
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest(), total_bytes


def _safe_raw_row(raw_row: dict[str | None, Any]) -> dict[str, str]:
    """Bound row-level diagnostic data so malformed input cannot inflate error storage."""
    return {
        str(key if key is not None else "extra_values")[:100]: str(value or "")[:500]
        for key, value in raw_row.items()
    }


def _normalize_staging_row(
    raw_row: dict[str | None, Any],
    headers: dict[str, str],
    line_number: int,
    seen_ids: set[str],
) -> dict[str, Any]:
    if None in raw_row:
        raise DataValidationError("The row contains more values than the CSV header.")
    student_code = _limited(
        raw_row.get(headers["id"], ""), "Student ID", 64, required=True
    )
    normalized_id = student_code.casefold()
    if normalized_id in seen_ids:
        raise DataValidationError(f"Duplicate student ID '{student_code}' in this file.")
    seen_ids.add(normalized_id)

    class_name = _limited(
        raw_row.get(headers["class"], ""), "Class", 100, required=True
    )
    subject_name = _limited(
        raw_row.get(headers["subject"], ""), "Subject", 150, required=True
    )
    true_values = {"Y", "YES", "TRUE", "1", "T"}
    false_values = {"", "N", "NO", "FALSE", "0", "F"}
    special_raw = raw_row.get(headers.get("special_needs", ""), "").strip().upper()
    if special_raw not in true_values | false_values:
        raise DataValidationError("Special_Needs must be Y/N, True/False, or 1/0.")

    department_name = _limited(
        raw_row.get(headers.get("department", ""), ""), "Department", 100
    ) or None
    full_name = _limited(
        raw_row.get(headers.get("name", ""), ""), "Name", 150
    ) or None
    subject_code = _subject_code(
        raw_row.get(headers.get("subject_code", ""), ""), subject_name
    )
    canonical = {
        "student_code": student_code,
        "student_code_normalized": normalized_id,
        "full_name": full_name,
        "class_name": class_name,
        "department_name": department_name,
        "subject_code": subject_code,
        "subject_name": subject_name,
        "accessibility_required": special_raw in true_values,
    }
    canonical["row_checksum"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    canonical["row_number"] = line_number
    return canonical


def _flush_staging_batches(
    import_record: DataImport,
    valid_batch: list[dict[str, Any]],
    error_batch: list[dict[str, Any]],
) -> None:
    if db.session.get_bind().dialect.name == "postgresql":
        raw_connection = db.session.connection().connection.driver_connection
        now = datetime.now(timezone.utc)
        if valid_batch:
            columns = (
                "data_import_id", "row_number", "student_code",
                "student_code_normalized", "full_name", "class_name",
                "department_name", "subject_code", "subject_name",
                "accessibility_required", "row_checksum", "created_at", "updated_at",
            )
            with raw_connection.cursor() as cursor:
                with cursor.copy(
                    f"COPY import_staging_students ({', '.join(columns)}) FROM STDIN"
                ) as copy:
                    for row in valid_batch:
                        copy.write_row(tuple(row.get(column) for column in columns[:-2]) + (now, now))
        if error_batch:
            columns = (
                "data_import_id", "row_number", "error_code", "message",
                "raw_data", "created_at", "updated_at",
            )
            with raw_connection.cursor() as cursor:
                with cursor.copy(
                    f"COPY import_row_errors ({', '.join(columns)}) FROM STDIN"
                ) as copy:
                    for row in error_batch:
                        copy.write_row((
                            row["data_import_id"],
                            row["row_number"],
                            row["error_code"],
                            row["message"],
                            json.dumps(row.get("raw_data") or {}, ensure_ascii=False),
                            now,
                            now,
                        ))
    else:
        if valid_batch:
            db.session.execute(insert(ImportStagingStudent), valid_batch)
        if error_batch:
            db.session.execute(insert(ImportRowError), error_batch)
    import_record.valid_rows += len(valid_batch)
    import_record.invalid_rows += len(error_batch)
    import_record.processed_rows += len(valid_batch) + len(error_batch)
    db.session.commit()
    valid_batch.clear()
    error_batch.clear()


def stage_student_import(
    file_storage,
    *,
    dataset_name: str,
    dataset_version: str,
    exam_date: date,
    start_time: time,
    end_time: time,
    session_name: str,
    academic_year: str,
    chunk_size: int = DEFAULT_IMPORT_CHUNK_SIZE,
) -> tuple[DataImport, bool]:
    """Stream, validate, and persist CSV rows without touching operational student data."""
    original_filename = (getattr(file_storage, "filename", None) or "students.csv")[:255]
    if not original_filename.casefold().endswith(".csv"):
        raise DataValidationError("Choose a file with the .csv extension.")
    if not 100 <= chunk_size <= 20_000:
        raise DataValidationError("Import chunk size must be between 100 and 20,000 rows.")
    dataset_name = _limited(dataset_name, "Dataset name", 150, required=True)
    dataset_version = _limited(dataset_version, "Dataset version", 50, required=True)
    session_name = _limited(session_name, "Session name", 80, required=True)
    academic_year = _limited(academic_year, "Academic year", 20, required=True)
    if end_time <= start_time:
        raise DataValidationError("Exam end time must be later than start time.")

    checksum, _total_bytes = _upload_checksum(file_storage)
    options = {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "exam_date": exam_date.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "session_name": session_name,
        "academic_year": academic_year,
    }
    idempotency_key = hashlib.sha256(
        (checksum + json.dumps(options, sort_keys=True, separators=(",", ":"))).encode("utf-8")
    ).hexdigest()
    existing = db.session.scalar(
        select(DataImport).where(DataImport.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing, True

    import_record = DataImport(
        import_type="students",
        source_type="real",
        original_filename=original_filename,
        status="processing",
        checksum_sha256=checksum,
        idempotency_key=idempotency_key,
        total_rows=0,
        processed_rows=0,
        valid_rows=0,
        invalid_rows=0,
        chunk_size=chunk_size,
        import_options=options,
    )
    db.session.add(import_record)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        existing = db.session.scalar(
            select(DataImport).where(DataImport.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing, True
        raise DataValidationError("The import could not be initialized safely.") from exc

    valid_batch: list[dict[str, Any]] = []
    error_batch: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    text_stream = None
    try:
        binary_stream = getattr(file_storage, "stream", file_storage)
        binary_stream.seek(0)
        text_stream = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text_stream)
        headers = _normalized_headers(reader.fieldnames)
        if not {"id", "class", "subject"}.issubset(headers):
            raise DataValidationError(
                "CSV headers must include ID, Class, and Subject. Name, Department, "
                "Subject_Code, and Special_Needs are optional."
            )

        for line_number, raw_row in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in raw_row.values()):
                continue
            import_record.total_rows += 1
            if import_record.total_rows > MAX_BULK_IMPORT_ROWS:
                raise DataValidationError(
                    f"A maximum of {MAX_BULK_IMPORT_ROWS:,} rows can be staged at once."
                )
            try:
                normalized = _normalize_staging_row(
                    raw_row, headers, line_number, seen_ids
                )
                normalized["data_import_id"] = import_record.id
                valid_batch.append(normalized)
            except DataValidationError as exc:
                error_batch.append({
                    "data_import_id": import_record.id,
                    "row_number": line_number,
                    "error_code": "invalid_student_row",
                    "message": str(exc)[:500],
                    "raw_data": _safe_raw_row(raw_row),
                })

            if len(valid_batch) + len(error_batch) >= chunk_size:
                _flush_staging_batches(import_record, valid_batch, error_batch)

        _flush_staging_batches(import_record, valid_batch, error_batch)
        if import_record.total_rows == 0:
            raise DataValidationError("The CSV does not contain any student records.")
        import_record.status = "validated" if import_record.valid_rows else "failed"
        import_record.error_report = {
            "message": (
                "Validation completed. Review row errors before committing valid records."
                if import_record.invalid_rows
                else "Validation completed with no row errors."
            )
        }
        db.session.commit()
        return import_record, False
    except UnicodeDecodeError as exc:
        _record_failed_import(import_record.id, "The uploaded CSV must be UTF-8 encoded.")
        raise DataValidationError("The uploaded CSV must be UTF-8 encoded.") from exc
    except csv.Error as exc:
        message = f"Malformed CSV: {exc}"
        _record_failed_import(import_record.id, message)
        raise DataValidationError(message) from exc
    except DataValidationError as exc:
        _record_failed_import(import_record.id, str(exc))
        raise
    except Exception:
        _record_failed_import(import_record.id, "Unexpected staging failure")
        raise
    finally:
        if text_stream is not None:
            try:
                text_stream.detach()
            except (ValueError, OSError):
                pass


def _staging_chunks(import_id: int, chunk_size: int):
    """Read bounded keyset pages so writes do not share an open server-side cursor."""
    last_row_number = 0
    while True:
        chunk = db.session.scalars(
            select(ImportStagingStudent)
            .where(
                ImportStagingStudent.data_import_id == import_id,
                ImportStagingStudent.row_number > last_row_number,
            )
            .order_by(ImportStagingStudent.row_number)
            .limit(chunk_size)
        ).all()
        if not chunk:
            return
        yield chunk
        last_row_number = chunk[-1].row_number


def commit_staged_student_import(import_id: int) -> tuple[Dataset, Exam, DataImport]:
    """Commit valid staged rows to operational tables in one database transaction."""
    import_record = db.session.get(DataImport, import_id)
    if import_record is None:
        raise DataValidationError("The selected import does not exist.")
    if import_record.status == "completed" and import_record.dataset is not None:
        exam = db.session.scalar(
            select(Exam).where(Exam.dataset_id == import_record.dataset_id)
        )
        if exam is None:
            raise DataValidationError("The completed import has no linked exam.")
        return import_record.dataset, exam, import_record
    if import_record.status not in {"validated", "committing"}:
        raise DataValidationError("Only a validated import can be committed.")
    if import_record.valid_rows < 1:
        raise DataValidationError("The import contains no valid student records.")

    options = import_record.import_options or {}
    required_options = {
        "dataset_name", "dataset_version", "exam_date", "start_time", "end_time",
        "session_name", "academic_year",
    }
    if not required_options.issubset(options):
        raise DataValidationError("The staged import metadata is incomplete.")
    if db.session.scalar(
        select(Dataset).where(
            Dataset.name == options["dataset_name"],
            Dataset.version == options["dataset_version"],
        )
    ):
        raise DataValidationError("A dataset with this name and version already exists.")

    import_record.status = "committing"
    db.session.commit()
    try:
        dataset = Dataset(
            name=options["dataset_name"],
            version=options["dataset_version"],
            source_type="real",
            description=f"Imported from {import_record.original_filename or 'students.csv'}",
            status="active",
        )
        db.session.add(dataset)
        db.session.flush()

        subject_rows = db.session.execute(
            select(
                ImportStagingStudent.subject_code,
                ImportStagingStudent.subject_name,
                ImportStagingStudent.department_name,
            )
            .where(ImportStagingStudent.data_import_id == import_id)
            .distinct()
        ).all()
        subjects: dict[str, Subject] = {}
        for code, name, department_name in subject_rows:
            if code in subjects and subjects[code].name.casefold() != name.casefold():
                raise DataValidationError(
                    f"Subject code '{code}' is mapped to multiple subject names."
                )
            subject = db.session.scalar(select(Subject).where(Subject.code == code))
            if subject is None:
                subject = Subject(
                    code=code,
                    name=name,
                    department=department_name,
                    source_type="real",
                    dataset=dataset,
                )
                db.session.add(subject)
            elif subject.name.casefold() != name.casefold():
                raise DataValidationError(
                    f"Existing subject code '{code}' belongs to '{subject.name}', not '{name}'."
                )
            subjects[code] = subject
        db.session.flush()

        session = create_exam_session(
            dataset=dataset,
            exam_date=date.fromisoformat(options["exam_date"]),
            start_time=time.fromisoformat(options["start_time"]),
            end_time=time.fromisoformat(options["end_time"]),
            session_name=options["session_name"],
            academic_year=options["academic_year"],
            status="scheduled",
        )
        papers = {
            code: create_exam_paper(session=session, subject=subject)
            for code, subject in subjects.items()
        }
        exam = papers[
            db.session.scalar(
                select(ImportStagingStudent.subject_code)
                .where(ImportStagingStudent.data_import_id == import_id)
                .order_by(ImportStagingStudent.row_number)
                .limit(1)
            )
        ]

        for staged_chunk in _staging_chunks(import_id, import_record.chunk_size):
            codes = [row.student_code for row in staged_chunk]
            existing_students = {
                student.student_code: student
                for student in db.session.scalars(
                    select(Student).where(Student.student_code.in_(codes))
                ).all()
            }
            registration_entries = []
            for row in staged_chunk:
                student = existing_students.get(row.student_code)
                if student is None:
                    student = Student(student_code=row.student_code, source_type="real")
                    db.session.add(student)
                elif student.source_type != "real":
                    raise DataValidationError(
                        f"Student ID '{row.student_code}' already belongs to synthetic data."
                    )
                student.full_name = row.full_name
                student.class_name = row.class_name
                student.department = row.department_name
                student.accessibility_required = row.accessibility_required
                student.active = True
                student.dataset = dataset
                registration_entries.append((student, papers[row.subject_code]))
            db.session.flush()
            for offset in range(0, len(registration_entries), 500):
                register_students_for_session(
                    registration_entries[offset:offset + 500],
                    verification_source="csv_subject_code",
                )

        import_record.dataset = dataset
        import_record.status = "completed"
        import_record.committed_at = datetime.now(timezone.utc)
        import_record.error_report = {
            "message": "Valid staged records were committed successfully.",
            "invalid_rows_retained": import_record.invalid_rows,
        }
        db.session.commit()
        return dataset, exam, import_record
    except (DataValidationError, AcademicValidationError, IntegrityError) as exc:
        db.session.rollback()
        import_record = db.session.get(DataImport, import_id)
        import_record.status = "validated"
        import_record.error_report = {"message": str(exc)[:1000], "commit_failed": True}
        db.session.commit()
        if isinstance(exc, (DataValidationError, AcademicValidationError)):
            raise DataValidationError(str(exc)) from exc
        raise DataValidationError("The import violates an existing database constraint.") from exc
    except Exception:
        db.session.rollback()
        import_record = db.session.get(DataImport, import_id)
        import_record.status = "validated"
        import_record.error_report = {
            "message": "Unexpected commit failure; staged rows were retained for retry.",
            "commit_failed": True,
        }
        db.session.commit()
        raise


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
    import_record, _reused = stage_student_import(
        file_storage,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        exam_date=exam_date,
        start_time=start_time,
        end_time=end_time,
        session_name=session_name,
        academic_year=academic_year,
    )
    if import_record.invalid_rows:
        raise DataValidationError(
            f"Import #{import_record.id} contains {import_record.invalid_rows:,} invalid "
            "row(s); review the error report before committing valid records."
        )
    return commit_staged_student_import(import_record.id)


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

        session = create_exam_session(
            dataset=dataset,
            exam_date=exam_date,
            start_time=time(9, 0),
            end_time=time(12, 0),
            session_name="Synthetic Morning Session",
            academic_year=f"{exam_date.year}-{str(exam_date.year + 1)[-2:]}",
            status="scheduled",
        )
        papers = [create_exam_paper(session=session, subject=subject) for subject in subjects]
        exam = papers[0]

        registration_entries = []
        for index in range(1, student_count + 1):
            class_number = ((index - 1) % class_count) + 1
            subject_index = rng.randrange(subject_count)
            subject = subjects[subject_index]
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
            registration_entries.append((student, papers[subject_index]))

        db.session.flush()
        for offset in range(0, len(registration_entries), 500):
            register_students_for_session(
                registration_entries[offset:offset + 500],
                verification_source="synthetic_generator",
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
    except (IntegrityError, AcademicValidationError) as exc:
        db.session.rollback()
        if isinstance(exc, AcademicValidationError):
            raise DataValidationError(str(exc)) from exc
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
