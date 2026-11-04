"""Session authentication and one-time institution bootstrap routes."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import (
    AcademicYear,
    AuditLog,
    BackgroundJob,
    DataImport,
    Dataset,
    Department,
    Exam,
    ExamSession,
    Institution,
    Invigilator,
    Room,
    SeatingRun,
    Student,
    Subject,
    User,
)
from security import record_audit


auth_bp = Blueprint("auth", __name__)
LOGIN_FAILURE_LIMIT = 5
LOCKOUT_MINUTES = 15


def _safe_next_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return None
    return value


def password_validation_error(password: str) -> str | None:
    if len(password) < 12:
        return "Password must contain at least 12 characters."
    if not any(character.isupper() for character in password):
        return "Password must include an uppercase letter."
    if not any(character.islower() for character in password):
        return "Password must include a lowercase letter."
    if not any(character.isdigit() for character in password):
        return "Password must include a number."
    return None


def _utc_aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:80]
        password = request.form.get("password", "")
        user = db.session.scalar(select(User).where(func.lower(User.username) == username.casefold()))
        now = datetime.now(timezone.utc)
        locked_until = _utc_aware(user.locked_until) if user else None
        if user and locked_until and locked_until > now:
            record_audit(
                "login_blocked",
                "user",
                user.id,
                actor=user.username,
                institution_id=user.institution_id,
                actor_user_id=user.id,
            )
            flash("This account is temporarily locked. Try again later.", "error")
        elif user and user.active and user.check_password(password):
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = now
            db.session.commit()
            login_user(user, remember=False, fresh=True)
            g.institution_id = user.institution_id
            record_audit("login_succeeded", "user", user.id)
            return redirect(_safe_next_url(request.args.get("next")) or url_for("index"))
        else:
            if user:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= LOGIN_FAILURE_LIMIT:
                    user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                db.session.commit()
                record_audit(
                    "login_failed",
                    "user",
                    user.id,
                    actor=user.username,
                    institution_id=user.institution_id,
                    actor_user_id=user.id,
                )
            flash("Invalid username or password.", "error")
    return render_template("login.html")


@auth_bp.post("/logout")
def logout():
    if current_user.is_authenticated:
        user_id = current_user.id
        record_audit("logout", "user", user_id)
        logout_user()
    return redirect(url_for("auth.login"))


def _claim_unscoped_records(institution_id: int) -> None:
    for model in (
        Department,
        AcademicYear,
        Dataset,
        Student,
        Subject,
        ExamSession,
        Exam,
        Room,
        Invigilator,
        DataImport,
        SeatingRun,
        BackgroundJob,
        AuditLog,
    ):
        db.session.execute(
            update(model)
            .where(model.institution_id.is_(None))
            .values(institution_id=institution_id)
        )


@auth_bp.route("/setup", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def setup():
    user_count = db.session.scalar(
        select(func.count(User.id)).execution_options(include_all_tenants=True)
    ) or 0
    if user_count:
        abort(404)
    setup_token = str(current_app.config.get("INITIAL_SETUP_TOKEN", ""))
    if current_app.config["IS_PRODUCTION"] and not setup_token:
        abort(503, description="INITIAL_SETUP_TOKEN must be configured before first-admin setup.")
    if request.method == "POST":
        if setup_token and not secrets.compare_digest(
            request.form.get("setup_token", ""), setup_token
        ):
            flash("The setup token is invalid.", "error")
            return render_template("setup.html", token_required=True), 403
        institution_name = request.form.get("institution_name", "").strip()
        institution_code = request.form.get("institution_code", "").strip().upper()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip() or None
        password = request.form.get("password", "")
        error = password_validation_error(password)
        if not institution_name or len(institution_name) > 180:
            error = "Institution name is required and must be 180 characters or fewer."
        elif not institution_code or len(institution_code) > 50:
            error = "Institution code is required and must be 50 characters or fewer."
        elif not username or len(username) > 80:
            error = "Username is required and must be 80 characters or fewer."
        if error:
            flash(error, "error")
            return render_template("setup.html", token_required=bool(setup_token)), 422
        try:
            institution = Institution(code=institution_code, name=institution_name)
            db.session.add(institution)
            db.session.flush()
            user = User(
                institution_id=institution.id,
                username=username,
                email=email,
                role="admin",
                active=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if request.form.get("claim_unscoped") == "yes":
                _claim_unscoped_records(institution.id)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("The institution code, username, or email is already registered.", "error")
            return render_template("setup.html", token_required=bool(setup_token)), 409
        login_user(user, fresh=True)
        g.institution_id = institution.id
        record_audit("initial_admin_created", "user", user.id)
        return redirect(url_for("index"))
    return render_template("setup.html", token_required=bool(setup_token))
