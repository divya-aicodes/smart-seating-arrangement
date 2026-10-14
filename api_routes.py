"""Versioned REST API for persisted seating data."""

from __future__ import annotations

from datetime import date
from typing import Any

from flask import Blueprint, jsonify, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from data_service import DataValidationError, generate_synthetic_dataset
from database_service import PersistenceError, publish_seating_run
from extensions import db
from models import (
    Dataset,
    Exam,
    ExamRegistration,
    Room,
    Seat,
    SeatAllocation,
    SeatingRun,
)
from security import record_audit


api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

REPORTABLE_STATUSES = {"completed", "published"}


def _error(code: str, message: str, status: int, *, details: Any = None):
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status


def _iso(value):
    return value.isoformat() if value is not None else None


def _pagination_args() -> tuple[int, int] | tuple[None, None]:
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "20"))
    except (TypeError, ValueError):
        return None, None
    if page < 1 or not 1 <= per_page <= 100:
        return None, None
    return page, per_page


def _page_response(statement, serializer):
    page, per_page = _pagination_args()
    if page is None:
        return _error(
            "invalid_pagination",
            "page must be at least 1 and per_page must be between 1 and 100.",
            400,
        )
    result = db.paginate(
        statement,
        page=page,
        per_page=per_page,
        max_per_page=100,
        error_out=False,
    )
    return jsonify({
        "data": [serializer(item) for item in result.items],
        "pagination": {
            "page": result.page,
            "per_page": result.per_page,
            "total": result.total,
            "pages": result.pages,
            "has_next": result.has_next,
            "has_previous": result.has_prev,
        },
    })


def _enum_query(name: str, allowed: set[str]):
    value = request.args.get(name)
    if value is None or value == "":
        return None, None
    if value not in allowed:
        return None, _error(
            "invalid_filter",
            f"{name} must be one of: {', '.join(sorted(allowed))}.",
            400,
        )
    return value, None


def _positive_id_query(name: str):
    value = request.args.get(name)
    if value is None or value == "":
        return None, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    if parsed < 1:
        return None, _error("invalid_filter", f"{name} must be a positive integer.", 400)
    return parsed, None


def _dataset_json(dataset: Dataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "version": dataset.version,
        "source_type": dataset.source_type,
        "generation_seed": dataset.generation_seed,
        "description": dataset.description,
        "status": dataset.status,
        "created_at": _iso(dataset.created_at),
        "updated_at": _iso(dataset.updated_at),
    }


def _exam_json(exam: Exam) -> dict[str, Any]:
    return {
        "id": exam.id,
        "dataset_id": exam.dataset_id,
        "subject": {
            "id": exam.subject.id,
            "code": exam.subject.code,
            "name": exam.subject.name,
        },
        "exam_date": _iso(exam.exam_date),
        "start_time": _iso(exam.start_time),
        "end_time": _iso(exam.end_time),
        "session_name": exam.session_name,
        "academic_year": exam.academic_year,
        "status": exam.status,
        "source_type": exam.source_type,
    }


def _room_json(room: Room) -> dict[str, Any]:
    return {
        "id": room.id,
        "dataset_id": room.dataset_id,
        "room_number": room.room_number,
        "building": room.building,
        "floor": room.floor,
        "layout": {
            "rows": room.rows,
            "benches_per_row": room.benches_per_row,
            "seats_per_bench": room.seats_per_bench,
        },
        "capacity": room.capacity,
        "active": room.active,
        "source_type": room.source_type,
    }


def _run_json(run: SeatingRun, *, allocation_count: int | None = None) -> dict[str, Any]:
    payload = {
        "id": run.id,
        "exam_id": run.exam_id,
        "status": run.status,
        "solver_version": run.solver_version,
        "solver_config": run.solver_config,
        "validation_result": run.validation_result,
        "generated_at": _iso(run.generated_at),
        "published_at": _iso(run.published_at),
        "created_by": run.created_by,
        "failure_reason": run.failure_reason,
    }
    if allocation_count is not None:
        payload["allocation_count"] = allocation_count
    return payload


def _json_body():
    if not request.is_json:
        return None, _error(
            "unsupported_media_type",
            "Use Content-Type application/json for this endpoint.",
            415,
        )
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error("invalid_json", "The request body must be a JSON object.", 400)
    return payload, None


def _json_int(payload: dict[str, Any], name: str, default: int, minimum: int, maximum: int):
    value = payload.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DataValidationError(f"{name} must be a whole number.")
    if not minimum <= value <= maximum:
        raise DataValidationError(f"{name} must be between {minimum} and {maximum}.")
    return value


@api_bp.get("")
def api_index():
    return jsonify({
        "name": "Smart Seating Arrangement API",
        "version": "v1",
        "documentation": url_for("api.openapi_document", _external=True),
        "health": url_for("api.health", _external=True),
    })


@api_bp.get("/health")
def health():
    try:
        db.session.execute(select(1))
    except Exception:
        db.session.rollback()
        return _error("database_unavailable", "The database is not ready.", 503)
    return jsonify({"status": "ok", "database": "reachable"})


@api_bp.get("/openapi.json")
def openapi_document():
    """Expose a compact OpenAPI contract without exposing application records."""
    return jsonify({
        "openapi": "3.1.0",
        "info": {
            "title": "Smart Seating Arrangement API",
            "version": "1.0.0",
            "description": (
                "Access to persisted institutional and synthetic seating data. "
                "State-changing requests require an X-CSRF-Token header."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "paths": {
            "/health": {"get": {"summary": "Check API and database readiness"}},
            "/datasets": {"get": {"summary": "List persisted datasets"}},
            "/datasets/{dataset_id}": {"get": {"summary": "Get a persisted dataset"}},
            "/datasets/synthetic": {
                "post": {"summary": "Generate and persist a deterministic synthetic dataset"}
            },
            "/exams": {"get": {"summary": "List persisted exams"}},
            "/exams/{exam_id}": {"get": {"summary": "Get a persisted exam"}},
            "/rooms": {"get": {"summary": "List persisted rooms"}},
            "/rooms/{room_id}": {"get": {"summary": "Get a persisted room"}},
            "/seating-runs": {"get": {"summary": "List persisted seating runs"}},
            "/seating-runs/{run_id}": {"get": {"summary": "Get a seating run"}},
            "/seating-runs/{run_id}/allocations": {
                "get": {"summary": "List persisted allocations for a validated run"}
            },
            "/seating-runs/{run_id}/publish": {
                "post": {"summary": "Publish a completed, validated seating run"}
            },
        },
    })


@api_bp.get("/datasets")
def datasets():
    statement = select(Dataset).order_by(Dataset.created_at.desc(), Dataset.id.desc())
    source_type, error = _enum_query("source_type", {"real", "synthetic"})
    if error:
        return error
    status, error = _enum_query("status", {"draft", "active", "archived"})
    if error:
        return error
    if source_type:
        statement = statement.where(Dataset.source_type == source_type)
    if status:
        statement = statement.where(Dataset.status == status)
    return _page_response(statement, _dataset_json)


@api_bp.get("/datasets/<int:dataset_id>")
def dataset_detail(dataset_id: int):
    dataset = db.session.get(Dataset, dataset_id)
    if dataset is None:
        return _error("not_found", "The requested dataset does not exist.", 404)
    payload = _dataset_json(dataset)
    payload["counts"] = {
        "students": db.session.scalar(
            select(func.count()).select_from(Dataset).join(Dataset.students).where(Dataset.id == dataset_id)
        ) or 0,
        "exams": db.session.scalar(
            select(func.count()).select_from(Exam).where(Exam.dataset_id == dataset_id)
        ) or 0,
        "rooms": db.session.scalar(
            select(func.count()).select_from(Room).where(Room.dataset_id == dataset_id)
        ) or 0,
    }
    return jsonify({"data": payload})


@api_bp.post("/datasets/synthetic")
def create_synthetic_dataset():
    payload, error = _json_body()
    if error:
        return error
    try:
        raw_date = str(payload.get("exam_date", "")).strip()
        if not raw_date:
            raise DataValidationError("exam_date is required in YYYY-MM-DD format.")
        try:
            exam_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise DataValidationError("exam_date must use YYYY-MM-DD format.") from exc

        dataset, exam = generate_synthetic_dataset(
            dataset_name=str(payload.get("dataset_name", "")),
            dataset_version=str(payload.get("dataset_version", "1")),
            seed=_json_int(payload, "seed", 42, 0, 2_147_483_647),
            student_count=_json_int(payload, "student_count", 60, 1, 5_000),
            class_count=_json_int(payload, "class_count", 3, 1, 100),
            subject_count=_json_int(payload, "subject_count", 3, 1, 100),
            room_count=_json_int(payload, "room_count", 2, 1, 100),
            rows_per_room=_json_int(payload, "rows_per_room", 5, 1, 100),
            benches_per_row=_json_int(payload, "benches_per_row", 3, 1, 100),
            seats_per_bench=_json_int(payload, "seats_per_bench", 2, 1, 10),
            exam_date=exam_date,
        )
        record_audit(
            "synthetic_dataset_generated_api",
            "dataset",
            dataset.id,
            after_values={"exam_id": exam.id, "seed": dataset.generation_seed},
        )
    except DataValidationError as exc:
        return _error("validation_error", str(exc), 422)

    response = jsonify({"data": {"dataset": _dataset_json(dataset), "exam": _exam_json(exam)}})
    response.status_code = 201
    response.headers["Location"] = url_for("api.dataset_detail", dataset_id=dataset.id)
    return response


@api_bp.get("/exams")
def exams():
    statement = (
        select(Exam)
        .options(joinedload(Exam.subject))
        .order_by(Exam.exam_date.desc(), Exam.start_time.desc(), Exam.id.desc())
    )
    dataset_id, error = _positive_id_query("dataset_id")
    if error:
        return error
    status, error = _enum_query("status", {"draft", "scheduled", "completed", "cancelled"})
    if error:
        return error
    if dataset_id:
        statement = statement.where(Exam.dataset_id == dataset_id)
    if status:
        statement = statement.where(Exam.status == status)
    return _page_response(statement, _exam_json)


@api_bp.get("/exams/<int:exam_id>")
def exam_detail(exam_id: int):
    exam = db.session.scalar(
        select(Exam).where(Exam.id == exam_id).options(joinedload(Exam.subject))
    )
    if exam is None:
        return _error("not_found", "The requested exam does not exist.", 404)
    payload = _exam_json(exam)
    payload["registration_count"] = db.session.scalar(
        select(func.count()).select_from(ExamRegistration).where(ExamRegistration.exam_id == exam.id)
    ) or 0
    return jsonify({"data": payload})


@api_bp.get("/rooms")
def rooms():
    statement = select(Room).order_by(Room.building, Room.room_number, Room.id)
    dataset_id, error = _positive_id_query("dataset_id")
    if error:
        return error
    active, error = _enum_query("active", {"true", "false"})
    if error:
        return error
    if dataset_id:
        statement = statement.where(Room.dataset_id == dataset_id)
    if active:
        statement = statement.where(Room.active.is_(active == "true"))
    return _page_response(statement, _room_json)


@api_bp.get("/rooms/<int:room_id>")
def room_detail(room_id: int):
    room = db.session.get(Room, room_id)
    if room is None:
        return _error("not_found", "The requested room does not exist.", 404)
    payload = _room_json(room)
    payload["active_seat_count"] = db.session.scalar(
        select(func.count()).select_from(Seat).where(Seat.room_id == room.id, Seat.active.is_(True))
    ) or 0
    return jsonify({"data": payload})


@api_bp.get("/seating-runs")
def seating_runs():
    statement = select(SeatingRun).order_by(SeatingRun.created_at.desc(), SeatingRun.id.desc())
    status, error = _enum_query(
        "status", {"pending", "running", "completed", "failed", "published"}
    )
    if error:
        return error
    if status:
        statement = statement.where(SeatingRun.status == status)
    else:
        statement = statement.where(SeatingRun.status.in_(REPORTABLE_STATUSES))
    return _page_response(statement, _run_json)


def _visible_run(run_id: int):
    run = db.session.get(SeatingRun, run_id)
    if run is None:
        return None, _error("not_found", "The requested seating run does not exist.", 404)
    return run, None


@api_bp.get("/seating-runs/<int:run_id>")
def seating_run_detail(run_id: int):
    run, error = _visible_run(run_id)
    if error:
        return error
    allocation_count = db.session.scalar(
        select(func.count()).select_from(SeatAllocation).where(SeatAllocation.seating_run_id == run.id)
    ) or 0
    return jsonify({"data": _run_json(run, allocation_count=allocation_count)})


@api_bp.get("/seating-runs/<int:run_id>/allocations")
def seating_run_allocations(run_id: int):
    run, error = _visible_run(run_id)
    if error:
        return error
    if run.status not in REPORTABLE_STATUSES or not (run.validation_result or {}).get("overall_valid"):
        return _error(
            "run_not_reportable",
            "Allocations are available only for completed, valid seating runs.",
            409,
        )
    registrations = db.session.scalars(
        select(ExamRegistration)
        .where(ExamRegistration.exam_id == run.exam_id)
        .options(joinedload(ExamRegistration.subject))
    ).all()
    subjects = {
        registration.student_id: registration.subject.name
        for registration in registrations
        if registration.subject is not None
    }
    statement = (
        select(SeatAllocation)
        .where(SeatAllocation.seating_run_id == run.id)
        .options(
            joinedload(SeatAllocation.student),
            joinedload(SeatAllocation.seat),
            joinedload(SeatAllocation.room),
        )
        .join(SeatAllocation.room)
        .join(SeatAllocation.seat)
        .order_by(Room.building, Room.room_number, Seat.row_number, Seat.bench_number, Seat.seat_number)
    )

    def serialize(allocation: SeatAllocation):
        return {
            "id": allocation.id,
            "student": {
                "id": allocation.student.id,
                "student_code": allocation.student.student_code,
                "full_name": allocation.student.full_name,
                "class_name": allocation.student.class_name,
                "accessibility_required": allocation.student.accessibility_required,
            },
            "subject": subjects.get(allocation.student_id, run.exam.subject.name),
            "room": {
                "id": allocation.room.id,
                "building": allocation.room.building,
                "room_number": allocation.room.room_number,
            },
            "seat": {
                "id": allocation.seat.id,
                "seat_code": allocation.seat.seat_code,
                "row": allocation.seat.row_number,
                "bench": allocation.seat.bench_number,
                "seat": allocation.seat.seat_number,
                "accessible": allocation.seat.accessible,
            },
            "subject_relaxed": allocation.subject_relaxed,
            "hard_conflict": allocation.hard_conflict,
        }

    return _page_response(statement, serialize)


@api_bp.post("/seating-runs/<int:run_id>/publish")
def publish_run(run_id: int):
    try:
        run = publish_seating_run(run_id)
        record_audit(
            "seating_run_published_api",
            "seating_run",
            run.id,
            after_values={"status": run.status},
        )
    except PersistenceError as exc:
        return _error("invalid_state_transition", str(exc), 409)
    return jsonify({"data": _run_json(run, allocation_count=len(run.allocations))})
