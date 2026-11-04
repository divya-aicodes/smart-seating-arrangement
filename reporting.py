"""Generate operational PDF and Excel reports from persisted seating runs."""

from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from collections import Counter
import json

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table as ExcelTable, TableStyleInfo
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    Exam,
    ExamRegistration,
    ExamSession,
    InvigilatorAssignment,
    Room,
    Seat,
    SeatAllocation,
    SeatingRun,
    Student,
)
from pagination import page_navigation, paginate


REPORTABLE_STATUSES = {"completed", "published"}
FORMULA_PREFIXES = ("=", "+", "-", "@")


class ReportNotAvailable(LookupError):
    """Raised when a requested run is missing or has not passed validation."""


def _safe_spreadsheet_text(value: Any) -> str:
    text = str(value if value is not None else "")
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _pdf_text(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def _load_report_run(run_id: int) -> SeatingRun:
    run = db.session.execute(
        select(SeatingRun)
        .where(SeatingRun.id == run_id)
        .options(
            joinedload(SeatingRun.exam).joinedload(Exam.dataset),
            joinedload(SeatingRun.exam).joinedload(Exam.subject),
            joinedload(SeatingRun.session).joinedload(ExamSession.dataset),
        )
    ).unique().scalar_one_or_none()
    if run is None or run.status not in REPORTABLE_STATUSES:
        raise ReportNotAvailable("The requested completed seating run does not exist.")
    if not (run.validation_result or {}).get("overall_valid", False):
        raise ReportNotAvailable("Reports require a seating run with valid hard constraints.")
    return run


def _registration_subjects(session_id: int, student_ids: list[int] | None = None):
    statement = select(ExamRegistration).where(
        ExamRegistration.exam_session_id == session_id,
        ExamRegistration.verification_status == "verified",
    )
    if student_ids is not None:
        statement = statement.where(ExamRegistration.student_id.in_(student_ids or [-1]))
    registrations = db.session.scalars(
        statement.options(joinedload(ExamRegistration.subject))
    ).all()
    return {
        registration.student_id: registration.subject.name
        for registration in registrations
        if registration.subject is not None
    }


def _allocation_statement(run_id: int):
    return (
        select(SeatAllocation)
        .where(SeatAllocation.seating_run_id == run_id)
        .join(SeatAllocation.student)
        .join(SeatAllocation.room)
        .join(SeatAllocation.seat)
        .options(
            joinedload(SeatAllocation.student),
            joinedload(SeatAllocation.seat).joinedload(Seat.room),
            joinedload(SeatAllocation.room),
        )
        .order_by(
            Room.building,
            Room.room_number,
            Seat.row_number,
            Seat.bench_number,
            Seat.seat_number,
        )
    )


def _allocation_row(allocation: SeatAllocation, registration_subjects: dict[int, str], fallback_subject: str):
    student = allocation.student
    seat = allocation.seat
    room = allocation.room
    return {
        "room": f"{room.building} / {room.room_number}",
        "room_id": room.id,
        "room_capacity": room.capacity,
        "seat_code": seat.seat_code,
        "row": seat.row_number,
        "bench": seat.bench_number,
        "seat": seat.seat_number,
        "student_id": student.student_code,
        "student_name": student.full_name or "",
        "class_name": student.class_name,
        "subject": registration_subjects.get(student.id, fallback_subject),
        "accessibility_required": student.accessibility_required,
        "accessible_seat": seat.accessible,
        "subject_relaxed": allocation.subject_relaxed,
    }


def load_report_summary(run_id: int) -> dict[str, Any]:
    run = _load_report_run(run_id)
    allocation_count, room_count, accessibility_count, relaxation_count = db.session.execute(
        select(
            func.count(SeatAllocation.id),
            func.count(func.distinct(SeatAllocation.room_id)),
            func.coalesce(func.sum(case((Student.accessibility_required.is_(True), 1), else_=0)), 0),
            func.coalesce(func.sum(case((SeatAllocation.subject_relaxed.is_(True), 1), else_=0)), 0),
        )
        .join(Student, Student.id == SeatAllocation.student_id)
        .where(SeatAllocation.seating_run_id == run_id)
    ).one()
    validation = run.validation_result or {}
    return {
        "run": run,
        "exam": run.exam,
        "session": run.session,
        "dataset": run.session.dataset,
        "allocation_count": allocation_count,
        "room_count": room_count,
        "accessibility_count": accessibility_count,
        "relaxation_count": relaxation_count,
        "validation": validation,
        "propositions": validation.get("propositions", []),
        "mathematics": run.solver_config or {},
    }


def _add_operational_summaries(report: dict[str, Any]) -> None:
    room_summary = {}
    for row in report["rows"]:
        item = room_summary.setdefault(
            row["room"], {"room": row["room"], "capacity": row["room_capacity"], "allocated": 0}
        )
        item["allocated"] += 1
    for item in room_summary.values():
        item["utilization"] = round(item["allocated"] * 100 / max(1, item["capacity"]), 1)
    subject_counts = Counter(row["subject"] for row in report["rows"])
    assignments = db.session.scalars(
        select(InvigilatorAssignment)
        .where(InvigilatorAssignment.exam_id == report["exam"].id)
        .options(
            joinedload(InvigilatorAssignment.invigilator),
            joinedload(InvigilatorAssignment.room),
        )
        .order_by(InvigilatorAssignment.room_id, InvigilatorAssignment.id)
    ).all()
    report["room_summary"] = list(room_summary.values())
    report["subject_summary"] = [
        {"subject": subject, "allocated": count}
        for subject, count in sorted(subject_counts.items())
    ]
    report["accessible_rows"] = [
        row for row in report["rows"] if row["accessibility_required"]
    ]
    report["invigilators"] = [
        {
            "room": f"{item.room.building} / {item.room.room_number}",
            "employee_code": item.invigilator.employee_code,
            "name": item.invigilator.full_name,
            "role": item.role,
        }
        for item in assignments
    ]


def load_report_data(run_id: int) -> dict[str, Any]:
    report = load_report_summary(run_id)
    run = report["run"]
    allocations = db.session.execute(_allocation_statement(run_id)).unique().scalars().all()
    registration_subjects = _registration_subjects(
        run.exam_session_id, [allocation.student_id for allocation in allocations]
    )
    report["rows"] = [
        _allocation_row(allocation, registration_subjects, run.exam.subject.name)
        for allocation in allocations
    ]
    _add_operational_summaries(report)
    return report


def load_report_page(run_id: int, *, query: str = "", per_page: int = 50) -> dict[str, Any]:
    report = load_report_summary(run_id)
    statement = _allocation_statement(run_id)
    if query:
        pattern = f"%{query[:100]}%"
        statement = statement.where(
            or_(
                Student.student_code.ilike(pattern),
                Student.full_name.ilike(pattern),
                Student.class_name.ilike(pattern),
                Room.building.ilike(pattern),
                Room.room_number.ilike(pattern),
                Seat.seat_code.ilike(pattern),
            )
        )
    allocation_page = paginate(statement, per_page=per_page)
    subjects = _registration_subjects(
        report["run"].exam_session_id,
        [allocation.student_id for allocation in allocation_page.items],
    )
    report["rows"] = [
        _allocation_row(allocation, subjects, report["exam"].subject.name)
        for allocation in allocation_page.items
    ]
    report["allocation_page"] = allocation_page
    report["allocation_navigation"] = page_navigation(allocation_page)
    report["query"] = query
    return report


def build_pdf_report(report: dict[str, Any]) -> BytesIO:
    run = report["run"]
    exam = report["session"]
    dataset = report["dataset"]
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=18 * mm,
        bottomMargin=15 * mm,
        title=f"Seating Run {run.id} Report",
        author="Smart Examination Seating Arrangement",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#17324D"),
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=colors.HexColor("#17324D"),
        spaceBefore=8,
        spaceAfter=5,
    )
    cell_style = ParagraphStyle(
        "Cell",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
    )

    story = [
        Paragraph("Examination seating report", title_style),
        Paragraph(
            f"Persisted run #{run.id} - {run.status.upper()} - "
            f"{(dataset.source_type if dataset else exam.source_type).upper()} data",
            styles["BodyText"],
        ),
        Spacer(1, 7),
    ]

    metadata = [
        ["Dataset", dataset.name if dataset else "Not linked", "Version", dataset.version if dataset else "n.a."],
        ["Exam date", exam.exam_date.isoformat(), "Session", exam.session_name],
        ["Time", f"{exam.start_time.strftime('%H:%M')} - {exam.end_time.strftime('%H:%M')}", "Academic year", exam.academic_year],
        ["Generated by", run.created_by or "system", "Solver", run.solver_version],
        ["Solver outcome", run.solver_status, "Run status", run.status],
    ]
    metadata_table = Table(metadata, colWidths=[28 * mm, 70 * mm, 28 * mm, 70 * mm])
    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8EEF4")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8EEF4")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#BCC8D4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([metadata_table, Spacer(1, 8), Paragraph("Run summary", section_style)])

    summary = [[
        "Allocated students", "Rooms used", "Accessibility requests", "Subject relaxations", "Hard constraints"
    ], [
        report["allocation_count"], report["room_count"], report["accessibility_count"],
        report["relaxation_count"], "VALID"
    ]]
    summary_table = Table(summary, colWidths=[39 * mm] * 5)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#93A4B5")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1DAE3")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([summary_table, Spacer(1, 8), Paragraph("Seat allocations", section_style)])

    headers = ["Room", "Seat", "Row", "Bench", "No.", "Student ID", "Student name", "Class", "Subject", "Access", "Relaxed"]
    allocation_data = [headers]
    for row in report["rows"]:
        allocation_data.append([
            Paragraph(_pdf_text(row["room"]), cell_style),
            Paragraph(_pdf_text(row["seat_code"]), cell_style),
            row["row"], row["bench"], row["seat"],
            Paragraph(_pdf_text(row["student_id"]), cell_style),
            Paragraph(_pdf_text(row["student_name"] or "-"), cell_style),
            Paragraph(_pdf_text(row["class_name"]), cell_style),
            Paragraph(_pdf_text(row["subject"]), cell_style),
            "Yes" if row["accessibility_required"] else "No",
            "Yes" if row["subject_relaxed"] else "No",
        ])
    allocation_table = Table(
        allocation_data,
        repeatRows=1,
        colWidths=[38 * mm, 34 * mm, 10 * mm, 12 * mm, 10 * mm, 25 * mm, 34 * mm, 25 * mm, 39 * mm, 15 * mm, 15 * mm],
    )
    allocation_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("ALIGN", (2, 1), (4, -1), "CENTER"),
        ("ALIGN", (9, 1), (10, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C7D2DC")),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(allocation_table)

    story.extend([Spacer(1, 10), Paragraph("Room capacity and utilization", section_style)])
    room_rows = [["Room", "Capacity", "Allocated", "Utilization"]] + [
        [item["room"], item["capacity"], item["allocated"], f'{item["utilization"]}%']
        for item in report["room_summary"]
    ]
    room_table = Table(room_rows, repeatRows=1, colWidths=[85 * mm, 32 * mm, 32 * mm, 38 * mm])
    room_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2DC")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether(room_table))

    if report["subject_summary"]:
        story.extend([Spacer(1, 8), Paragraph("Subject allocation summary", section_style)])
        subject_rows = [["Subject", "Allocated students"]] + [
            [item["subject"], item["allocated"]] for item in report["subject_summary"]
        ]
        subject_table = Table(subject_rows, repeatRows=1, colWidths=[145 * mm, 42 * mm])
        subject_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2DC")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether(subject_table))

    if report["invigilators"]:
        story.extend([Spacer(1, 8), Paragraph("Invigilator assignments", section_style)])
        invigilator_rows = [["Room", "Employee", "Name", "Role"]] + [
            [item["room"], item["employee_code"], item["name"], item["role"]]
            for item in report["invigilators"]
        ]
        invigilator_table = Table(invigilator_rows, repeatRows=1, colWidths=[58 * mm, 35 * mm, 58 * mm, 36 * mm])
        invigilator_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2DC")),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        story.append(KeepTogether(invigilator_table))

    if report["propositions"]:
        story.extend([Spacer(1, 10), Paragraph("Validation certificate", section_style)])
        validation_rows = [["Proposition", "Type", "Satisfied", "Violations"]]
        for proposition in report["propositions"]:
            validation_rows.append([
                Paragraph(_pdf_text(proposition.get("id", "")), cell_style),
                Paragraph(_pdf_text(proposition.get("type", "")), cell_style),
                "Yes" if proposition.get("satisfied") else "No",
                proposition.get("violation_count", len(proposition.get("violations", []))),
            ])
        validation_table = Table(validation_rows, repeatRows=1, colWidths=[40 * mm, 105 * mm, 25 * mm, 25 * mm])
        validation_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2DC")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(KeepTogether(validation_table))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(14 * mm, 8 * mm, f"Persisted seating run #{run.id}")
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer


def build_excel_report(report: dict[str, Any]) -> BytesIO:
    run = report["run"]
    exam = report["session"]
    dataset = report["dataset"]
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    allocations = workbook.create_sheet("Allocations")
    summary.sheet_view.showGridLines = False
    allocations.sheet_view.showGridLines = False
    workbook.properties.title = f"Seating Run {run.id} Report"
    workbook.properties.creator = "Smart Examination Seating Arrangement"

    navy = "17324D"
    light_blue = "E8EEF4"
    border_color = "BCC8D4"
    white = "FFFFFF"
    thin = Side(style="thin", color=border_color)

    summary["A2"] = "Examination seating report"
    summary["A2"].font = Font(name="Arial", size=16, bold=True, color=navy)
    summary["A3"] = f"Persisted run #{run.id} - {run.status.upper()}"
    summary["A3"].font = Font(name="Arial", size=10, italic=True, color="5D6D7E")
    summary["A4"].border = Border(bottom=Side(style="thin", color=navy))

    metadata = [
        ("Dataset", dataset.name if dataset else "Not linked"),
        ("Dataset version", dataset.version if dataset else "n.a."),
        ("Source type", dataset.source_type.upper() if dataset else exam.source_type.upper()),
        ("Exam date", exam.exam_date),
        ("Session", exam.session_name),
        ("Exam time", f"{exam.start_time.strftime('%H:%M')} - {exam.end_time.strftime('%H:%M')}"),
        ("Academic year", exam.academic_year),
        ("Generated by", run.created_by or "system"),
        ("Solver", run.solver_version),
        ("Solver outcome", run.solver_status),
    ]
    summary["A6"] = "Run information"
    summary["A6"].font = Font(name="Arial", size=11, bold=True, color=navy)
    for row_index, (label, value) in enumerate(metadata, start=7):
        summary.cell(row=row_index, column=1, value=label)
        summary.cell(row=row_index, column=2, value=value)
        summary.cell(row=row_index, column=1).fill = PatternFill("solid", fgColor=light_blue)
        summary.cell(row=row_index, column=1).font = Font(name="Arial", size=10, bold=True, color=navy)
        summary.cell(row=row_index, column=2).font = Font(name="Arial", size=10)
        summary.cell(row=row_index, column=1).border = Border(bottom=thin)
        summary.cell(row=row_index, column=2).border = Border(bottom=thin)
    summary["B10"].number_format = "yyyy-mm-dd"

    headers = [
        "Room", "Seat code", "Row", "Bench", "Seat", "Student ID", "Student name",
        "Class", "Subject", "Accessibility required", "Subject relaxed",
    ]
    allocations.append(headers)
    for row in report["rows"]:
        allocations.append([
            _safe_spreadsheet_text(row["room"]),
            _safe_spreadsheet_text(row["seat_code"]),
            row["row"], row["bench"], row["seat"],
            _safe_spreadsheet_text(row["student_id"]),
            _safe_spreadsheet_text(row["student_name"]),
            _safe_spreadsheet_text(row["class_name"]),
            _safe_spreadsheet_text(row["subject"]),
            "Yes" if row["accessibility_required"] else "No",
            "Yes" if row["subject_relaxed"] else "No",
        ])

    allocation_end = max(2, len(report["rows"]) + 1)
    summary["D6"] = "Run summary"
    summary["D6"].font = Font(name="Arial", size=11, bold=True, color=navy)
    summary_metrics = [
        ("Allocated students", f"=COUNTA(Allocations!A2:A{allocation_end})"),
        ("Rooms used", report["room_count"]),
        ("Accessibility requests", f'=COUNTIF(Allocations!J2:J{allocation_end},"Yes")'),
        ("Subject relaxations", f'=COUNTIF(Allocations!K2:K{allocation_end},"Yes")'),
        ("Hard constraints", "VALID"),
    ]
    for row_index, (label, value) in enumerate(summary_metrics, start=7):
        summary.cell(row=row_index, column=4, value=label)
        summary.cell(row=row_index, column=5, value=value)
        summary.cell(row=row_index, column=4).fill = PatternFill("solid", fgColor=light_blue)
        summary.cell(row=row_index, column=4).font = Font(name="Arial", size=10, bold=True, color=navy)
        summary.cell(row=row_index, column=5).font = Font(name="Arial", size=10, bold=True)
        summary.cell(row=row_index, column=4).border = Border(bottom=thin)
        summary.cell(row=row_index, column=5).border = Border(bottom=thin)

    validation_start = 18
    summary.cell(row=validation_start, column=1, value="Validation certificate")
    summary.cell(row=validation_start, column=1).font = Font(name="Arial", size=11, bold=True, color=navy)
    validation_headers = ["Proposition", "Type", "Satisfied", "Violations"]
    validation_columns = [1, 2, 4, 5]
    for column, label in zip(validation_columns, validation_headers):
        cell = summary.cell(row=validation_start + 1, column=column, value=label)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(name="Arial", size=10, bold=True, color=white)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for offset, proposition in enumerate(report["propositions"], start=2):
        row_index = validation_start + offset
        summary.cell(row=row_index, column=1, value=str(proposition.get("id", "")))
        summary.cell(row=row_index, column=2, value=str(proposition.get("type", "")))
        summary.cell(row=row_index, column=4, value="Yes" if proposition.get("satisfied") else "No")
        summary.cell(
            row=row_index,
            column=5,
            value=proposition.get("violation_count", len(proposition.get("violations", []))),
        )

    allocations.freeze_panes = "A2"
    allocations.auto_filter.ref = f"A1:K{allocation_end}"
    table = ExcelTable(displayName=f"SeatingAllocations{run.id}", ref=f"A1:K{allocation_end}")
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    allocations.add_table(table)
    for cell in allocations[1]:
        cell.font = Font(name="Arial", size=10, bold=True, color=white)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in allocations.iter_rows(min_row=2, max_row=allocation_end):
        for cell in row:
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="center")

    widths = {"A": 28, "B": 25, "C": 8, "D": 9, "E": 8, "F": 18, "G": 24, "H": 18, "I": 28, "J": 22, "K": 18}
    for column, width in widths.items():
        allocations.column_dimensions[column].width = width
    for column, width in {"A": 22, "B": 30, "C": 3, "D": 24, "E": 18}.items():
        summary.column_dimensions[column].width = width
    summary.freeze_panes = "A6"

    def append_register(title, headers, rows, widths=None):
        sheet = workbook.create_sheet(title)
        sheet.sheet_view.showGridLines = False
        sheet.append(headers)
        for values in rows:
            sheet.append([
                _safe_spreadsheet_text(value) if isinstance(value, str) else value
                for value in values
            ])
        for cell in sheet[1]:
            cell.font = Font(name="Arial", size=10, bold=True, color=white)
            cell.fill = PatternFill("solid", fgColor=navy)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for row_values in sheet.iter_rows(min_row=2):
            for cell in row_values:
                cell.font = Font(name="Arial", size=10)
                cell.alignment = Alignment(vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, width in enumerate(widths or [28] * len(headers), start=1):
            sheet.column_dimensions[chr(64 + index)].width = width
        return sheet

    append_register(
        "Room Summary",
        ["Room", "Capacity", "Allocated", "Utilization percent"],
        [
            [item["room"], item["capacity"], item["allocated"], item["utilization"]]
            for item in report["room_summary"]
        ],
        [32, 14, 14, 22],
    )
    append_register(
        "Subject Summary",
        ["Subject", "Allocated students"],
        [[item["subject"], item["allocated"]] for item in report["subject_summary"]],
        [42, 22],
    )
    append_register(
        "Accessible Seating",
        ["Student ID", "Student name", "Room", "Seat", "Accessible seat"],
        [
            [row["student_id"], row["student_name"], row["room"], row["seat_code"], "Yes" if row["accessible_seat"] else "No"]
            for row in report["accessible_rows"]
        ],
        [18, 28, 32, 24, 18],
    )
    append_register(
        "Invigilators",
        ["Room", "Employee code", "Name", "Role"],
        [
            [item["room"], item["employee_code"], item["name"], item["role"]]
            for item in report["invigilators"]
        ],
        [32, 18, 28, 20],
    )
    append_register(
        "Validation",
        ["Proposition", "Type", "Satisfied", "Violations"],
        [
            [
                item.get("id", ""),
                item.get("type", ""),
                "Yes" if item.get("satisfied") else "No",
                item.get("violation_count", len(item.get("violations", []))),
            ]
            for item in report["propositions"]
        ],
        [24, 38, 14, 14],
    )
    append_register(
        "Mathematics",
        ["Metric", "Recorded value"],
        [
            [key.replace("_", " ").title(), json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value]
            for key, value in sorted(report["mathematics"].items())
        ],
        [36, 90],
    )

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer
