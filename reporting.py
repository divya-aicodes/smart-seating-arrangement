"""Generate operational PDF and Excel reports from persisted seating runs."""

from __future__ import annotations

from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

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
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from extensions import db
from models import Exam, ExamRegistration, ExamSession, Seat, SeatAllocation, SeatingRun


REPORTABLE_STATUSES = {"completed", "published"}
FORMULA_PREFIXES = ("=", "+", "-", "@")


class ReportNotAvailable(LookupError):
    """Raised when a requested run is missing or has not passed validation."""


def _safe_spreadsheet_text(value: Any) -> str:
    text = str(value if value is not None else "")
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _pdf_text(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def load_report_data(run_id: int) -> dict[str, Any]:
    statement = (
        select(SeatingRun)
        .where(SeatingRun.id == run_id)
        .options(
            joinedload(SeatingRun.exam).joinedload(Exam.dataset),
            joinedload(SeatingRun.exam).joinedload(Exam.subject),
            joinedload(SeatingRun.session).joinedload(ExamSession.dataset),
            joinedload(SeatingRun.allocations).joinedload(SeatAllocation.student),
            joinedload(SeatingRun.allocations)
            .joinedload(SeatAllocation.seat)
            .joinedload(Seat.room),
            joinedload(SeatingRun.allocations).joinedload(SeatAllocation.room),
        )
    )
    run = db.session.execute(statement).unique().scalar_one_or_none()
    if run is None or run.status not in REPORTABLE_STATUSES:
        raise ReportNotAvailable("The requested completed seating run does not exist.")
    if not (run.validation_result or {}).get("overall_valid", False):
        raise ReportNotAvailable("Reports require a seating run with valid hard constraints.")

    registrations = db.session.scalars(
        select(ExamRegistration)
        .where(
            ExamRegistration.exam_session_id == run.exam_session_id,
            ExamRegistration.verification_status == "verified",
        )
        .options(joinedload(ExamRegistration.subject))
    ).all()
    registration_subjects = {
        registration.student_id: registration.subject.name
        for registration in registrations
        if registration.subject is not None
    }

    allocations = sorted(
        run.allocations,
        key=lambda item: (
            item.room.building.casefold(),
            item.room.room_number.casefold(),
            item.seat.row_number,
            item.seat.bench_number,
            item.seat.seat_number,
        ),
    )
    rows = []
    for allocation in allocations:
        student = allocation.student
        seat = allocation.seat
        room = allocation.room
        rows.append({
            "room": f"{room.building} / {room.room_number}",
            "seat_code": seat.seat_code,
            "row": seat.row_number,
            "bench": seat.bench_number,
            "seat": seat.seat_number,
            "student_id": student.student_code,
            "student_name": student.full_name or "",
            "class_name": student.class_name,
            "subject": registration_subjects.get(student.id, run.exam.subject.name),
            "accessibility_required": student.accessibility_required,
            "accessible_seat": seat.accessible,
            "subject_relaxed": allocation.subject_relaxed,
        })

    room_ids = {allocation.room_id for allocation in allocations}
    validation = run.validation_result or {}
    return {
        "run": run,
        "exam": run.exam,
        "session": run.session,
        "dataset": run.session.dataset,
        "rows": rows,
        "allocation_count": len(rows),
        "room_count": len(room_ids),
        "accessibility_count": sum(row["accessibility_required"] for row in rows),
        "relaxation_count": sum(row["subject_relaxed"] for row in rows),
        "validation": validation,
        "propositions": validation.get("propositions", []),
    }


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

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer
