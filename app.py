"""
Smart Examination Seating Arrangement - Flask Web Application
Discrete Mathematics Course Project Optimizer
"""

import csv
import io
import json
import math
import os
import secrets
from collections import Counter
from datetime import timedelta
from urllib.parse import urlsplit
# pyrefly: ignore [missing-import]
from flask import Flask, render_template, request, redirect, url_for, flash, Response, abort, jsonify
from flask_wtf.csrf import CSRFError
from sqlalchemy import select
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import csrf, db, migrate

from seating.feasibility_check import build_seat_adjacency_graph, check_feasibility
from seating.forbidden_positions import assign_special_needs_students
from seating.graph_coloring import (
    build_student_conflict_graph,
    verify_handshaking_lemma,
    color_conflict_graph,
    get_small_worked_chromatic_polynomial
)
from seating.seat_mapper import map_students_to_seats
from seating.validator import validate_seating_arrangement
from seating.counting import compute_combinatorial_counts

app = Flask(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_STUDENTS = 5_000


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cors_origins(raw_value):
    """Parse exact HTTP(S) origins and reject wildcard or path-based entries."""
    origins = set()
    for raw_origin in raw_value.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            "*" in origin
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins without "
                "wildcards, credentials, paths, queries, or fragments."
            )
        origins.add(origin)
    return origins


def _parse_allowed_hosts(raw_value):
    """Parse hostnames used to defend production Host-header handling."""
    hosts = set()
    for raw_host in raw_value.split(","):
        host = raw_host.strip().casefold()
        if not host:
            continue
        if (
            "*" in host
            or "://" in host
            or "/" in host
            or "?" in host
            or "#" in host
            or any(char.isspace() for char in host)
        ):
            raise RuntimeError(
                "ALLOWED_HOSTS must contain exact hostnames without schemes, ports, "
                "wildcards, paths, queries, or fragments."
            )
        hosts.add(host)
    return hosts


is_production = os.environ.get("APP_ENV", "development").strip().lower() == "production"
configured_secret = os.environ.get("SECRET_KEY")
if is_production and (
    not configured_secret
    or configured_secret == "[REDACTED_SECRET]"
    or len(configured_secret) < 32
):
    raise RuntimeError(
        "SECRET_KEY must be a non-placeholder value of at least 32 characters in production."
    )

session_cookie_secure = _env_flag("SESSION_COOKIE_SECURE", is_production)
session_cookie_samesite = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax").strip().title()
if session_cookie_samesite not in {"Lax", "Strict", "None"}:
    raise RuntimeError("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.")
if is_production and not session_cookie_secure:
    raise RuntimeError("SESSION_COOKIE_SECURE cannot be disabled in production.")

cors_origins = _parse_cors_origins(os.environ.get("CORS_ALLOWED_ORIGINS", ""))
cors_allow_credentials = _env_flag("CORS_ALLOW_CREDENTIALS", False)
if cors_allow_credentials and (
    not session_cookie_secure or session_cookie_samesite != "None"
):
    raise RuntimeError(
        "Cross-origin credentialed sessions require SESSION_COOKIE_SECURE=true and "
        "SESSION_COOKIE_SAMESITE=None."
    )
allowed_hosts = _parse_allowed_hosts(os.environ.get("ALLOWED_HOSTS", ""))
if is_production and not allowed_hosts:
    raise RuntimeError("ALLOWED_HOSTS must contain at least one public hostname in production.")

app.config.update(
    SECRET_KEY=configured_secret or secrets.token_urlsafe(32),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_NAME="smart_seating_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=session_cookie_samesite,
    SESSION_COOKIE_SECURE=session_cookie_secure,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    WTF_CSRF_ENABLED=True,
    WTF_CSRF_TIME_LIMIT=7200,
    WTF_CSRF_SSL_STRICT=is_production,
    CORS_ALLOWED_ORIGINS=cors_origins,
    CORS_ALLOW_CREDENTIALS=cors_allow_credentials,
    ALLOWED_HOSTS=allowed_hosts,
    PREFERRED_URL_SCHEME="https" if is_production else "http",
)

configured_database_url = os.environ.get("DATABASE_URL")
if is_production and not configured_database_url:
    raise RuntimeError("DATABASE_URL must be configured when APP_ENV=production.")
database_url = (configured_database_url or "sqlite:///smart_seating.db").strip()
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
if is_production and not database_url.startswith("postgresql+psycopg://"):
    raise RuntimeError("Production deployments must use PostgreSQL through Psycopg 3.")

engine_options = {"pool_pre_ping": True}
if database_url.startswith("postgresql+psycopg://"):
    engine_options.update({"pool_recycle": 300, "pool_size": 5, "max_overflow": 10})

app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS=engine_options,
)
if _env_flag("TRUST_PROXY_HEADERS", False):
    # Enable only when all traffic arrives through a trusted reverse proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
# SQLite stores its file in Flask's instance directory. Vercel production uses
# PostgreSQL, and its serverless filesystem is read-only outside temporary
# storage, so do not create the local-only directory during a deployment import.
if database_url.startswith("sqlite"):
    os.makedirs(app.instance_path, exist_ok=True)
db.init_app(app)
migrate.init_app(app, db)

# Importing models registers all tables with SQLAlchemy and Flask-Migrate.
import models as _models  # noqa: E402,F401
from database_service import complete_seating_run, create_seating_run  # noqa: E402
from data_routes import data_bp  # noqa: E402
from report_routes import report_bp  # noqa: E402
from api_routes import api_bp  # noqa: E402
from models import Exam, ExamRegistration, Room, Seat, Student  # noqa: E402
from security import record_audit  # noqa: E402

app.register_blueprint(data_bp)
app.register_blueprint(report_bp)
app.register_blueprint(api_bp)


def _allowed_request_origins():
    """Return exact origins allowed to make browser requests to this deployment."""
    return set(app.config.get("CORS_ALLOWED_ORIGINS", set())) | {request.host_url.rstrip("/")}


@app.before_request
def reject_untrusted_hosts():
    """Reject forged Host headers when an explicit deployment allowlist is configured."""
    allowed_hosts = app.config.get("ALLOWED_HOSTS", set())
    if not allowed_hosts:
        return None
    hostname = urlsplit(f"//{request.host}").hostname
    if not hostname or hostname.casefold() not in allowed_hosts:
        abort(400, description="This request host is not allowed.")


@app.before_request
def reject_untrusted_write_origins():
    """Block cross-origin state-changing requests unless the origin is allowlisted."""
    origin = request.headers.get("Origin", "").rstrip("/")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
        if origin not in _allowed_request_origins():
            abort(403, description="This request origin is not allowed.")


# Register CSRF after the origin guard so explicitly untrusted origins are rejected first.
csrf.init_app(app)


@app.after_request
def apply_security_headers(response):
    """Apply defensive browser headers and narrowly scoped CORS response headers."""
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin and origin in _allowed_request_origins():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers.add("Vary", "Origin")
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRF-Token"
        if app.config.get("CORS_ALLOW_CREDENTIALS", False):
            response.headers["Access-Control-Allow-Credentials"] = "true"

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), geolocation=(), microphone=(), payment=()"
    )
    response.headers.setdefault("Cache-Control", "no-store")
    if is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


def _read_csv_text(file_stream_or_text):
    if isinstance(file_stream_or_text, str):
        text = file_stream_or_text
    else:
        content = file_stream_or_text.read()
        if isinstance(content, bytes):
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV must be encoded as UTF-8.") from exc
        elif isinstance(content, str):
            text = content
        else:
            raise ValueError("CSV input must be text or a readable file.")

    if len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ValueError("Student CSV data exceeds the 2 MiB limit.")
    return text.strip()


def parse_csv_students(file_stream_or_text):
    """
    Parses student CSV input. Accepts either a file-like stream or raw string.
    Expected columns: id, class, subject, special_needs (Y/N or True/False)
    """
    stream = io.StringIO(_read_csv_text(file_stream_or_text))
    try:
        rows = list(csv.reader(stream))
    except csv.Error as exc:
        raise ValueError(f"Malformed CSV data: {exc}") from exc

    if not rows:
        raise ValueError("CSV input is completely empty.")
        
    # Check header
    header = [h.strip().lower() for h in rows[0]]
    id_idx, class_idx, subj_idx, sn_idx = -1, -1, -1, -1
    
    for i, col in enumerate(header):
        if col in ["id", "student_id", "roll_no", "studentid", "student id"]:
            id_idx = i
        elif col in ["class", "section", "class/section", "dept", "department"]:
            class_idx = i
        elif col in ["subject", "course", "paper", "exam"]:
            subj_idx = i
        elif col in ["special_needs", "special needs", "accessible", "disability", "pwd", "accessible_required"]:
            sn_idx = i
            
    if id_idx == -1 or class_idx == -1:
        raise ValueError(
            "Malformed CSV header. Required columns include ID and Class. "
            "Optional columns are Subject and Special_Needs."
        )
            
    students = []
    seen_ids = set()
    true_values = {"Y", "YES", "TRUE", "1", "T"}
    false_values = {"", "N", "NO", "FALSE", "0", "F"}
    
    for line_num, row in enumerate(rows[1:], start=2):
        if not row or not any(field.strip() for field in row):
            continue  # skip empty lines
            
        stu_id = row[id_idx].strip() if id_idx < len(row) else ""
        if not stu_id:
            raise ValueError(f"Line {line_num}: Student ID is required.")

        normalized_id = stu_id.casefold()
        if normalized_id in seen_ids:
            raise ValueError(f"Line {line_num}: Duplicate student ID '{stu_id}'.")
        seen_ids.add(normalized_id)

        if len(stu_id) > 64:
            raise ValueError(f"Line {line_num}: Student ID must be 64 characters or fewer.")
            
        stu_class = row[class_idx].strip() if class_idx < len(row) else ""
        if not stu_class:
            raise ValueError(f"Line {line_num}: Class/section is required.")
        if len(stu_class) > 100:
            raise ValueError(f"Line {line_num}: Class/section must be 100 characters or fewer.")

        stu_subj = row[subj_idx].strip() if subj_idx != -1 and subj_idx < len(row) and row[subj_idx].strip() else "Discrete Mathematics"
        if len(stu_subj) > 150:
            raise ValueError(f"Line {line_num}: Subject must be 150 characters or fewer.")
        
        # Special needs flag
        sn_raw = row[sn_idx].strip().upper() if sn_idx != -1 and sn_idx < len(row) else "N"
        if sn_raw not in true_values | false_values:
            raise ValueError(
                f"Line {line_num}: Special_Needs must be Y/N, Yes/No, True/False, or 1/0."
            )
        special_needs = sn_raw in true_values
        
        students.append({
            "id": stu_id,
            "class": stu_class,
            "subject": stu_subj,
            "special_needs": special_needs
        })

        if len(students) > MAX_STUDENTS:
            raise ValueError(f"A maximum of {MAX_STUDENTS:,} students is allowed per run.")
        
    if not students:
        raise ValueError("No valid student records found in CSV.")
        
    return students


def parse_bounded_int(field_name, default, minimum, maximum, label):
    raw_value = request.form.get(field_name, str(default)).strip()
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return value


def parse_choice(field_name, default, allowed, label):
    value = request.form.get(field_name, default).strip()
    if value not in allowed:
        raise ValueError(f"Invalid {label} selection.")
    return value


def _accessible_seats_per_room(rows, benches, seats_per_bench, rule):
    columns = benches * seats_per_bench
    aisle_per_row = benches * (1 if seats_per_bench == 1 else 2)
    if rule == "row_1":
        return columns
    if rule == "aisle":
        return rows * aisle_per_row
    if rule == "front_aisle_only":
        return aisle_per_row
    return columns + max(0, rows - 1) * aisle_per_row


def build_manual_room_configs(
    num_rooms,
    rows,
    benches,
    seats_per_bench,
    accessible_rule,
):
    """Build the same manual seat geometry used by recommendations and assignment."""
    room_configs = []
    for room_id in range(1, num_rooms + 1):
        accessible_seats = []
        for row_number in range(1, rows + 1):
            for bench_number in range(1, benches + 1):
                for seat_number in range(1, seats_per_bench + 1):
                    seat_id = (
                        f"R{room_id}-r{row_number}-c{bench_number}-s{seat_number}"
                    )
                    is_front = row_number == 1
                    is_aisle = seat_number in {1, seats_per_bench}
                    if (
                        (accessible_rule == "row_1" and is_front)
                        or (accessible_rule == "aisle" and is_aisle)
                        or (
                            accessible_rule == "row_1_and_aisle"
                            and (is_front or is_aisle)
                        )
                        or (
                            accessible_rule == "front_aisle_only"
                            and is_front and is_aisle
                        )
                    ):
                        accessible_seats.append(seat_id)
        room_configs.append({
            "room_id": room_id,
            "room_label": f"Manual Room {room_id}",
            "rows": rows,
            "cols": benches,
            "seats_per_bench": seats_per_bench,
            "accessible_seats": accessible_seats,
            "unavailable_seats": [],
        })
    return room_configs


def _layout_is_solvable(
    students,
    rooms,
    rows,
    benches,
    seats_per_bench,
    diagonal,
    accessible_rule,
):
    """Run the actual hard-constraint pipeline before recommending a layout."""
    room_configs = build_manual_room_configs(
        rooms,
        rows,
        benches,
        seats_per_bench,
        accessible_rule,
    )
    graph = build_seat_adjacency_graph(room_configs, diagonal=diagonal)
    special_students = [student for student in students if student.get("special_needs")]
    try:
        special_assignments, _, _, _ = assign_special_needs_students(
            special_students,
            graph,
        )
    except ValueError:
        return False

    seat_assignments, _, _, unassigned = map_students_to_seats(
        students,
        graph,
        already_assigned_special=special_assignments,
        diagonal=diagonal,
    )
    if unassigned or len(seat_assignments) != len(students):
        return False
    return validate_seating_arrangement(
        seat_assignments,
        graph,
        students,
    )["overall_valid"]


def recommend_room_layouts(
    students,
    diagonal=False,
    accessible_rule="row_1_and_aisle",
    max_seats_per_room=300,
    max_rooms=10,
):
    """Return practical layouts that pass bounds and the actual allocation pipeline."""
    if not students:
        return []

    student_count = len(students)
    largest_class = max(Counter(s.get("class", "Unknown") for s in students).values())
    accessibility_count = sum(bool(s.get("special_needs")) for s in students)
    recommendations = []

    for seats_per_bench in (4, 3, 2):
        candidates = []
        for rooms in range(1, max_rooms + 1):
            for rows in range(2, 13):
                for benches in range(2, 7):
                    columns = benches * seats_per_bench
                    capacity_per_room = rows * columns
                    if capacity_per_room > max_seats_per_room:
                        continue
                    capacity = rooms * capacity_per_room
                    if capacity < student_count:
                        continue
                    if diagonal:
                        alpha = rooms * math.ceil(rows / 2) * math.ceil(columns / 2)
                    else:
                        alpha = rooms * math.ceil((rows * columns) / 2)
                    accessible = rooms * _accessible_seats_per_room(
                        rows, benches, seats_per_bench, accessible_rule
                    )
                    if alpha < largest_class or accessible < accessibility_count:
                        continue
                    score = (
                        capacity - student_count,
                        abs(rows - 5),
                        abs(benches - 3),
                        rooms,
                    )
                    candidates.append((score, rooms, rows, benches, capacity, alpha, accessible))

        candidates.sort(key=lambda candidate: candidate[0])
        selected = next((
            candidate
            for candidate in candidates[:24]
            if _layout_is_solvable(
                students,
                candidate[1],
                candidate[2],
                candidate[3],
                seats_per_bench,
                diagonal,
                accessible_rule,
            )
        ), None)
        if selected:
            _, rooms, rows, benches, capacity, alpha, accessible = selected
            recommendations.append({
                "rooms": rooms,
                "rows": rows,
                "benches": benches,
                "seats_per_bench": seats_per_bench,
                "capacity_per_room": rows * benches * seats_per_bench,
                "capacity": capacity,
                "spare_seats": capacity - student_count,
                "independence_bound": alpha,
                "accessible_seats": accessible,
                "student_count": student_count,
                "largest_class": largest_class,
                "accessibility_count": accessibility_count,
                "adjacency": "orthogonal and diagonal" if diagonal else "orthogonal",
            })
    return recommendations


def render_assignment_error(
    title,
    message,
    status_code,
    feasibility_report=None,
    details=None,
):
    return render_template(
        "results.html",
        error_occurred=True,
        error_title=title,
        error_message=message,
        error_details=details or [],
        feasibility_report=feasibility_report,
        flat_allocation_table=[],
    ), status_code


@app.errorhandler(RequestEntityTooLarge)
def handle_oversized_request(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": {
            "code": "request_too_large",
            "message": "The submitted request exceeds the 2 MiB limit.",
        }}), 413
    return render_assignment_error(
        "Upload Too Large",
        "The submitted request exceeds the 2 MiB limit. Reduce the CSV size and try again.",
        413,
    )


@app.errorhandler(403)
def handle_forbidden_request(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": {
            "code": "forbidden",
            "message": error.description or "This request is not allowed.",
        }}), 403
    return render_assignment_error(
        "Request Blocked",
        error.description or "This request is not allowed.",
        403,
    )


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": {
            "code": "csrf_failed",
            "message": "A valid CSRF token is required for this request.",
            "details": error.description,
        }}), 400
    return render_assignment_error(
        "Security Token Invalid",
        "The form expired or its security token was missing. Refresh the page and try again.",
        400,
    )


@app.route("/", methods=["GET"])
def index():
    """Render persisted exam and room choices plus an optional manual workflow."""
    all_exams = db.session.scalars(
        select(Exam)
        .where(Exam.status.in_(["draft", "scheduled"]))
        .order_by(Exam.exam_date, Exam.start_time)
    ).all()
    available_rooms = db.session.scalars(
        select(Room).where(Room.active.is_(True)).order_by(Room.building, Room.room_number)
    ).all()
    room_dataset_ids = {
        room.dataset_id for room in available_rooms if room.dataset_id is not None
    }
    available_exams = [
        exam for exam in all_exams if exam.dataset_id in room_dataset_ids
    ]
    return render_template(
        "index.html",
        available_exams=available_exams,
        available_rooms=available_rooms,
    )


@app.route("/sample-csv", methods=["GET"])
def sample_csv():
    """Download a header-only import template; no static student records are supplied."""
    data = [[
        "ID", "Name", "Class", "Department", "Subject_Code", "Subject", "Special_Needs"
    ]]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(data)
    
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=student_import_template.csv"}
    )


@app.route("/api/layout-recommendations", methods=["POST"])
def layout_recommendations():
    """Validate temporary CSV data and return feasible room-layout choices."""
    try:
        adjacency_rule = parse_choice(
            "adjacency_rule",
            "orthogonal",
            {"orthogonal", "diagonal"},
            "adjacency rule",
        )
        accessible_rule = parse_choice(
            "accessible_rule",
            "row_1_and_aisle",
            {"row_1", "aisle", "row_1_and_aisle", "front_aisle_only"},
            "accessible-seat rule",
        )
        max_seats_per_room = parse_bounded_int(
            "max_seats_per_room",
            40,
            4,
            300,
            "Maximum seats per room",
        )
        max_rooms = parse_bounded_int(
            "max_rooms",
            10,
            1,
            10,
            "Maximum rooms available",
        )
        csv_file = request.files.get("csv_file")
        manual_text = request.form.get("student_data_text", "").strip()
        if csv_file and csv_file.filename:
            students = parse_csv_students(csv_file)
        elif manual_text:
            students = parse_csv_students(manual_text)
        else:
            raise ValueError("Upload a student CSV or paste CSV data to calculate layouts.")

        recommendations = recommend_room_layouts(
            students,
            diagonal=adjacency_rule == "diagonal",
            accessible_rule=accessible_rule,
            max_seats_per_room=max_seats_per_room,
            max_rooms=max_rooms,
        )
        if not recommendations:
            raise ValueError(
                f"No verified layout fits within {max_seats_per_room} seats per room "
                f"and {max_rooms} available room(s). Increase one of these limits or "
                "adjust the adjacency rule."
            )

        return jsonify({
            "student_count": len(students),
            "largest_class": max(Counter(s["class"] for s in students).values()),
            "accessibility_count": sum(bool(s["special_needs"]) for s in students),
            "limits": {
                "max_seats_per_room": max_seats_per_room,
                "max_rooms": max_rooms,
            },
            "recommendations": recommendations,
        })
    except ValueError as error:
        return jsonify({
            "error": {
                "code": "invalid_student_data",
                "message": str(error),
            }
        }), 400


def load_database_assignment_inputs():
    try:
        exam_id = int(request.form.get("exam_id", ""))
    except ValueError as exc:
        raise ValueError("Select a persisted exam session.") from exc
    room_id_values = request.form.getlist("room_ids")
    try:
        room_ids = list(dict.fromkeys(int(value) for value in room_id_values))
    except ValueError as exc:
        raise ValueError("One or more selected rooms are invalid.") from exc
    if not room_ids:
        raise ValueError("Select at least one persisted room.")

    exam = db.session.get(Exam, exam_id)
    if exam is None or exam.status not in {"draft", "scheduled"}:
        raise ValueError("The selected exam session is unavailable.")

    registrations = db.session.scalars(
        select(ExamRegistration)
        .join(ExamRegistration.student)
        .where(
            ExamRegistration.exam_id == exam.id,
            ExamRegistration.status.in_(["registered", "eligible"]),
            Student.active.is_(True),
        )
        .order_by(Student.student_code)
    ).all()
    if not registrations:
        raise ValueError("The selected exam has no eligible registered students.")

    room_lookup = {
        room.id: room
        for room in db.session.scalars(
            select(Room).where(Room.id.in_(room_ids), Room.active.is_(True))
        ).all()
    }
    if len(room_lookup) != len(room_ids):
        raise ValueError("One or more selected rooms are unavailable.")
    if exam.dataset_id is None:
        raise ValueError("The selected exam is not linked to a versioned dataset.")
    mismatched_rooms = [
        room.room_number
        for room in room_lookup.values()
        if room.dataset_id != exam.dataset_id or room.source_type != exam.source_type
    ]
    if mismatched_rooms:
        raise ValueError(
            "Selected rooms must belong to the same real or synthetic dataset as the exam."
        )

    students = []
    database_student_ids = {}
    for registration in registrations:
        student = registration.student
        subject = registration.subject or exam.subject
        students.append({
            "id": student.student_code,
            "class": student.class_name,
            "subject": subject.name,
            "special_needs": student.accessibility_required,
        })
        database_student_ids[student.student_code] = student.id

    room_configs = []
    database_seat_ids = {}
    for room_id in room_ids:
        room = room_lookup[room_id]
        stored_seats = {
            (seat.row_number, seat.bench_number, seat.seat_number): seat
            for seat in room.seats
        }
        accessible_seats = []
        unavailable_seats = []
        active_seat_count = 0
        for row_number in range(1, room.rows + 1):
            for bench_number in range(1, room.benches_per_row + 1):
                for seat_number in range(1, room.seats_per_bench + 1):
                    graph_id = (
                        f"R{room.id}-r{row_number}-c{bench_number}-s{seat_number}"
                    )
                    stored = stored_seats.get((row_number, bench_number, seat_number))
                    if stored is None or not stored.active:
                        unavailable_seats.append(graph_id)
                        continue
                    database_seat_ids[graph_id] = stored.id
                    active_seat_count += 1
                    if stored.accessible:
                        accessible_seats.append(graph_id)
        if active_seat_count == 0:
            raise ValueError(f"Room {room.room_number} has no active persisted seats.")
        room_configs.append({
            "room_id": room.id,
            "room_label": f"{room.building} / {room.room_number}",
            "rows": room.rows,
            "cols": room.benches_per_row,
            "seats_per_bench": room.seats_per_bench,
            "accessible_seats": accessible_seats,
            "unavailable_seats": unavailable_seats,
        })

    return exam, students, room_configs, database_student_ids, database_seat_ids


@app.route("/assign", methods=["POST"])
def assign():
    """
    Main seating assignment controller running Steps 1 through 6:
    1. Feasibility pre-check (Pigeonhole Principle)
    2. Forbidden positions (Special-needs seating)
    3. Graph coloring & Handshaking lemma (NetworkX)
    4. Seat mapping & soft-constraint relaxation
    5. Propositional logic validation
    6. Combinatorial counting (Product Rule & PIE)
    """
    try:
        data_mode = parse_choice(
            "data_mode", "manual", {"database", "manual"}, "data source"
        )
        students_per_invigilator = parse_bounded_int(
            "invigilator_ratio", 20, 5, 50, "Invigilator ratio"
        )
        adjacency_rule = parse_choice(
            "adjacency_rule", "orthogonal", {"orthogonal", "diagonal"}, "adjacency rule"
        )
        diagonal = adjacency_rule == "diagonal"
        accessible_rule = "row_1_and_aisle"

        persisted_exam = None
        database_student_ids = {}
        database_seat_ids = {}
        if data_mode == "database":
            (
                persisted_exam,
                students,
                room_configs,
                database_student_ids,
                database_seat_ids,
            ) = load_database_assignment_inputs()
        else:
            num_rooms = parse_bounded_int("num_rooms", 1, 1, 10, "Number of rooms")
            rows = parse_bounded_int("rows", 4, 1, 20, "Rows per room")
            cols = parse_bounded_int("cols", 3, 1, 15, "Benches per row")
            seats_per_bench = parse_bounded_int(
                "seats_per_bench", 2, 1, 4, "Seats per bench"
            )
            accessible_rule = parse_choice(
                "accessible_rule",
                "row_1_and_aisle",
                {"row_1", "aisle", "row_1_and_aisle", "front_aisle_only"},
                "accessible-seat rule",
            )
            room_configs = build_manual_room_configs(
                num_rooms,
                rows,
                cols,
                seats_per_bench,
                accessible_rule,
            )

            csv_file = request.files.get("csv_file")
            manual_text = request.form.get("student_data_text", "").strip()
            if csv_file and csv_file.filename:
                students = parse_csv_students(csv_file)
            elif manual_text:
                students = parse_csv_students(manual_text)
            else:
                raise ValueError("Upload a student CSV or enter CSV data for a manual run.")

        num_rooms = len(room_configs)
            
        # --- Step 1: Feasibility Pre-Check (Pigeonhole Principle) ---
        feasibility_report, G_seats = check_feasibility(students, room_configs, diagonal=diagonal)
        if feasibility_report["hard_violation_guaranteed"]:
            return render_assignment_error(
                "Infeasible Seating Configuration",
                "No seating plan was generated because one or more hard constraints cannot be satisfied.",
                422,
                feasibility_report,
                [item["message"] for item in feasibility_report["diagnostics"]],
            )
            
        # --- Step 2: Forbidden-Positions Handling for Special-Needs ---
        special_students = [s for s in students if s.get("special_needs", False)]
        try:
            special_assignments, used_seats, special_ids, forbidden_math = assign_special_needs_students(
                special_students, G_seats
            )
        except ValueError as ve:
            return render_assignment_error(
                "Accessible Seating Infeasibility",
                str(ve),
                422,
                feasibility_report,
            )
            
        # --- Step 3: Conflict Graph Construction & Coloring (NetworkX) ---
        G_conflict = build_student_conflict_graph(students)
        handshaking_report = verify_handshaking_lemma(G_conflict)
        student_coloring, chromatic_number, color_partitions = color_conflict_graph(G_conflict)
        chromatic_worked_example = get_small_worked_chromatic_polynomial()
        
        # --- Step 4: Seat Mapping with Soft Subject Constraint ---
        seat_assignments, student_to_seat, relaxed_constraints, unassigned_students = map_students_to_seats(
            students,
            G_seats,
            already_assigned_special=special_assignments,
            diagonal=diagonal
        )

        if unassigned_students:
            unassigned_ids = [student["id"] for student in unassigned_students]
            return render_assignment_error(
                "Hard-Constraint Assignment Failed",
                "No invalid fallback was produced. The current solver could not place every student "
                "without breaking a hard class-adjacency or capacity constraint.",
                422,
                feasibility_report,
                [f"Unassigned student: {student_id}" for student_id in unassigned_ids[:25]],
            )
        
        # --- Step 5: Explicit Propositional Logic Validation ---
        validation_certificate = validate_seating_arrangement(seat_assignments, G_seats, students)

        if not validation_certificate["overall_valid"]:
            failed = [
                proposition
                for proposition in validation_certificate["propositions"]
                if "HARD" in proposition["type"] and not proposition["satisfied"]
            ]
            return render_assignment_error(
                "Final Validation Failed",
                "The generated arrangement failed hard-constraint validation and was not published.",
                422,
                feasibility_report,
                [f"{item['id']}: {item['violation_count']} violation(s)" for item in failed],
            )
        
        # Extract relaxed_constraints from validation_certificate for the UI
        # Map them to the format expected by results.html
        relaxed_constraints = []
        for v in validation_certificate["propositions"][1]["violations"]:
            relaxed_constraints.append({
                "seat_a": v["seat_u"],
                "seat_b": v["seat_v"],
                "seat_a_label": v["seat_u_label"],
                "seat_b_label": v["seat_v_label"],
                "student_a": v["student_u"],
                "student_b": v["student_v"],
                "subject": v["subject"]
            })
        
        # --- Step 6: Combinatorial Counting (Product Rule & PIE) ---
        counting_report = compute_combinatorial_counts(students, G_seats)

        seating_run_id = None
        if persisted_exam is not None:
            run = create_seating_run(
                persisted_exam.id,
                {
                    "adjacency_rule": adjacency_rule,
                    "room_ids": [room["room_id"] for room in room_configs],
                    "invigilator_ratio": students_per_invigilator,
                },
                solver_version="greedy-v1-strict",
                created_by="local-user",
            )
            persisted_allocations = []
            for graph_seat_id, student in seat_assignments.items():
                persisted_allocations.append({
                    "student_id": database_student_ids[student["id"]],
                    "seat_id": database_seat_ids[graph_seat_id],
                    "hard_conflict": False,
                    "subject_relaxed": bool(student.get("has_subject_relaxation", False)),
                })
            complete_seating_run(run.id, persisted_allocations, validation_certificate)
            seating_run_id = run.id
            record_audit(
                "seating_run_completed",
                "seating_run",
                run.id,
                after_values={
                    "exam_id": persisted_exam.id,
                    "allocation_count": len(persisted_allocations),
                },
            )
        else:
            record_audit(
                "manual_optimization_completed",
                "manual_seating_run",
                "not-persisted",
                after_values={"student_count": len(students), "room_count": num_rooms},
            )
        
        # --- Invigilator Placeholder Calculation ---
        # Group assigned students by room
        room_allocations = {}
        for room_config in room_configs:
            r_id = room_config["room_id"]
            room_rows = room_config["rows"]
            room_cols = room_config["cols"]
            room_seats_per_bench = room_config["seats_per_bench"]
            room_allocations[r_id] = {
                "room_id": r_id,
                "room_label": room_config.get("room_label", f"Room {r_id}"),
                "rows": room_rows,
                "cols": room_cols,
                "seats_per_bench": room_seats_per_bench,
                "seats_grid": [],
                "student_count": 0,
                "special_needs_count": 0,
                "invigilators_needed": 0,
                "invigilators_list": [],
                "class_violations": 0,
                "subject_violations": 0
            }
            
            # Construct visual grid structure for Jinja template
            for r in range(1, room_rows + 1):
                row_benches = []
                for c in range(1, room_cols + 1):
                    bench_seats = []
                    for s in range(1, room_seats_per_bench + 1):
                        seat_id = f"R{r_id}-r{r}-c{c}-s{s}"
                        node_data = G_seats.nodes.get(seat_id, {})
                        assigned_student = seat_assignments.get(seat_id)
                        available = seat_id in G_seats
                        
                        if assigned_student:
                            room_allocations[r_id]["student_count"] += 1
                            if assigned_student.get("special_needs"):
                                room_allocations[r_id]["special_needs_count"] += 1
                                
                        bench_seats.append({
                            "seat_id": seat_id,
                            "label": node_data.get("seat_label", seat_id),
                            "row": r,
                            "bench": c,
                            "seat_num": s,
                            "accessible": node_data.get("accessible", False),
                            "available": available,
                            "student": assigned_student
                        })
                    row_benches.append({"bench_num": c, "seats": bench_seats})
                room_allocations[r_id]["seats_grid"].append({"row_num": r, "benches": row_benches})
                
            # Invigilators needed
            stu_in_room = room_allocations[r_id]["student_count"]
            needed = max(1, math.ceil(stu_in_room / students_per_invigilator)) if stu_in_room > 0 else 0
            room_allocations[r_id]["invigilators_needed"] = needed
            room_allocations[r_id]["invigilators_list"] = [
                f"Invigilator #{i} (Room {r_id} Post)"
                for i in range(1, needed + 1)
            ]
            
        # Tally up violations per room based on validation_certificate edges
        # We count the number of edge pairs (divided by 2 since undirected edges are stored, 
        # wait, validator iterates over G_seats.edges() which are undirected, so each edge is counted once)
        for v in validation_certificate["propositions"][0]["violations"]:
            room_id = G_seats.nodes[v["seat_u"]].get("room_id", 1)
            if room_id in room_allocations:
                room_allocations[room_id]["class_violations"] += 1
                
        for v in validation_certificate["propositions"][1]["violations"]:
            room_id = G_seats.nodes[v["seat_u"]].get("room_id", 1)
            if room_id in room_allocations:
                room_allocations[room_id]["subject_violations"] += 1
            
        # Format printable flat allocation list
        flat_allocation_table = []
        for seat_id, stu in seat_assignments.items():
            relaxed_str = "No"
            if stu.get("has_class_conflict"):
                relaxed_str = "Yes (Class)"
            elif stu.get("has_subject_relaxation"):
                relaxed_str = "Yes (Subject)"
                
            flat_allocation_table.append({
                "room_id": stu.get("room_id", 1),
                "seat_id": seat_id,
                "seat_label": stu.get("seat_label", seat_id),
                "student_id": stu["id"],
                "class": stu.get("class", ""),
                "subject": stu.get("subject", ""),
                "special_needs": "Yes" if stu.get("special_needs") else "No",
                "relaxed": relaxed_str
            })
            
        flat_allocation_table.sort(key=lambda x: (x["room_id"], x["seat_id"]))
        
        return render_template(
            "results.html",
            error_occurred=False,
            students_count=len(students),
            rooms_count=num_rooms,
            room_allocations=room_allocations,
            flat_allocation_table=flat_allocation_table,
            feasibility_report=feasibility_report,
            forbidden_math=forbidden_math,
            handshaking_report=handshaking_report,
            chromatic_number=chromatic_number,
            chromatic_worked_example=chromatic_worked_example,
            validation_certificate=validation_certificate,
            relaxed_constraints=relaxed_constraints,
            counting_report=counting_report,
            unassigned_students=unassigned_students,
            diagonal_enabled=diagonal,
            invigilator_ratio=students_per_invigilator,
            data_mode=data_mode,
            seating_run_id=seating_run_id,
        )
        
    except RequestEntityTooLarge:
        raise
    except ValueError as exc:
        return render_assignment_error("Invalid Input", str(exc), 400)
    except Exception:
        app.logger.exception("Unexpected seating assignment failure")
        return render_assignment_error(
            "Application Error",
            "The seating run could not be completed. Check the input and try again.",
            500,
        )


@app.route("/export-csv", methods=["POST"])
def export_csv():
    """Generates and downloads the allocated seating plan as CSV."""
    rows_data = request.form.get("allocation_json")
    if not rows_data:
        return "No data to export", 400
        
    try:
        allocations = json.loads(rows_data)
    except (TypeError, json.JSONDecodeError):
        return "Invalid allocation JSON", 400

    if not isinstance(allocations, list) or len(allocations) > MAX_STUDENTS:
        return "Allocation data must be a list within the supported size limit", 400

    record_audit(
        "allocation_exported",
        "seating_allocation",
        "csv",
        after_values={"row_count": len(allocations)},
    )

    def safe_csv_value(value):
        text = str(value if value is not None else "")
        if text.startswith(("=", "+", "-", "@")):
            return "'" + text
        return text
        
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Room No", "Seat ID", "Seat Location", "Student ID", "Class / Section", "Subject", "Special Needs", "Relaxed Constraint"])
    
    for item in allocations:
        if not isinstance(item, dict):
            return "Each allocation row must be an object", 400
        writer.writerow([
            safe_csv_value(item.get("room_id", 1)),
            safe_csv_value(item.get("seat_id", "")),
            safe_csv_value(item.get("seat_label", "")),
            safe_csv_value(item.get("student_id", "")),
            safe_csv_value(item.get("class", "")),
            safe_csv_value(item.get("subject", "")),
            safe_csv_value(item.get("special_needs", "No")),
            safe_csv_value(item.get("relaxed", "No"))
        ])
        
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=smart_exam_seating_allocation.csv"}
    )


if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", "5000"))
    except ValueError:
        port = 5000
    app.run(debug=_env_flag("FLASK_DEBUG", False), port=port)
