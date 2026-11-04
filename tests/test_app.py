"""
Integration tests for Flask routes, CSV handling, and results rendering.
"""

import unittest
import json
import io
import os
import re
from datetime import date, time
from openpyxl import load_workbook
from pypdf import PdfReader
from werkzeug.datastructures import FileStorage

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app, parse_csv_students, recommend_room_layouts, MAX_UPLOAD_BYTES
from data_service import stage_student_import
from extensions import db
from models import (
    AuditLog,
    DataImport,
    Dataset,
    Exam,
    ExamRegistration,
    ExamSession,
    ImportRowError,
    ImportStagingStudent,
    Room,
    SeatingRun,
    Student,
)


class TestFlaskIntegration(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_index_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create a defensible seating plan", response.data)
        self.assertIn(b"Pigeonhole Principle", response.data)
        self.assertLess(
            response.data.index(b"Add student records"),
            response.data.index(b"Describe the physical room setup"),
        )
        self.assertIn(b"data-layout-recommendations", response.data)
        self.assertIn(b"Maximum seats in one room", response.data)
        self.assertIn(b"Maximum rooms available", response.data)
        self.assertNotIn(b"Paste CSV text", response.data)
        self.assertNotIn(b"student_data_text", response.data)

    def test_sample_csv_downloads(self):
        for preset in ["balanced", "pigeonhole", "multiroom"]:
            res = self.client.get(f"/sample-csv?preset={preset}")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.content_type, "text/csv; charset=utf-8")
            self.assertIn(
                b"ID,Name,Class,Department,Subject_Code,Subject,Special_Needs",
                res.data,
            )
            self.assertEqual(len(res.data.decode("utf-8").strip().splitlines()), 1)

    def test_real_csv_import_persists_connected_records(self):
        csv_bytes = (
            b"ID,Name,Class,Department,Subject_Code,Subject,Special_Needs\n"
            b"R001,Asha Rao,CSE-A,CSE,DM101,Discrete Mathematics,Y\n"
            b"R002,Vikram Sen,CSE-B,CSE,PHY101,Physics,N\n"
        )
        res = self.client.post(
            "/data/import-students",
            data={
                "dataset_name": "Real December Import",
                "dataset_version": "v1",
                "exam_date": "2026-12-01",
                "start_time": "09:00",
                "end_time": "12:00",
                "session_name": "Morning",
                "academic_year": "2026-27",
                "csv_file": (io.BytesIO(csv_bytes), "students.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Dataset.query.count(), 0)
        self.assertEqual(Student.query.count(), 0)
        staged = DataImport.query.one()
        self.assertEqual(staged.status, "validated")
        self.assertEqual(staged.processed_rows, 2)
        self.assertEqual(staged.valid_rows, 2)
        self.assertEqual(staged.invalid_rows, 0)
        self.assertEqual(ImportStagingStudent.query.count(), 2)

        committed = self.client.post(f"/data/imports/{staged.id}/commit")
        self.assertEqual(committed.status_code, 302)
        dataset = Dataset.query.one()
        self.assertEqual(dataset.source_type, "real")
        self.assertEqual(Student.query.count(), 2)
        self.assertEqual(ExamSession.query.count(), 1)
        self.assertEqual(Exam.query.count(), 2)
        self.assertEqual(ExamRegistration.query.count(), 2)
        self.assertTrue(all(
            registration.verification_status == "verified"
            and registration.subject_id == registration.exam.subject_id
            for registration in ExamRegistration.query.all()
        ))
        self.assertEqual(DataImport.query.one().status, "completed")

        data_page = self.client.get("/data")
        self.assertEqual(data_page.status_code, 200)
        self.assertIn(b"Recently stored students", data_page.data)
        self.assertIn(b"R001", data_page.data)
        self.assertIn(b"Asha Rao", data_page.data)
        self.assertIn(b"R002", data_page.data)
        self.assertIn(b"students.csv", data_page.data)

    def test_real_import_rejects_rows_wider_than_header(self):
        csv_bytes = (
            b"ID,Class,Subject\n"
            b"R001,CSE-A,Discrete Mathematics,unexpected\n"
        )
        res = self.client.post(
            "/data/import-students",
            data={
                "dataset_name": "Malformed Import",
                "dataset_version": "v1",
                "exam_date": "2026-12-01",
                "start_time": "09:00",
                "end_time": "12:00",
                "session_name": "Morning",
                "academic_year": "2026-27",
                "csv_file": (io.BytesIO(csv_bytes), "students.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(Dataset.query.count(), 0)
        self.assertEqual(DataImport.query.one().status, "failed")
        self.assertEqual(ImportRowError.query.count(), 1)

    def test_staged_import_keeps_invalid_rows_visible_and_commits_only_after_confirmation(self):
        csv_bytes = (
            b"ID,Name,Class,Department,Subject_Code,Subject,Special_Needs\n"
            b"R001,Asha Rao,CSE-A,CSE,DM101,Discrete Mathematics,N\n"
            b"R002,Vikram Sen,CSE-B,CSE,DM101,Discrete Mathematics,MAYBE\n"
        )
        response = self.client.post(
            "/data/import-students",
            data={
                "dataset_name": "Review Import",
                "dataset_version": "v1",
                "exam_date": "2026-12-01",
                "start_time": "09:00",
                "end_time": "12:00",
                "session_name": "Morning",
                "academic_year": "2026-27",
                "csv_file": (io.BytesIO(csv_bytes), "review.csv"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        staged = DataImport.query.one()
        self.assertEqual(staged.status, "validated")
        self.assertEqual((staged.valid_rows, staged.invalid_rows), (1, 1))
        self.assertEqual(Student.query.count(), 0)

        status_response = self.client.get(f"/api/v1/imports/{staged.id}")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()["data"]
        self.assertEqual(status_payload["progress_percentage"], 100.0)
        self.assertEqual(status_payload["successful_rows"], 1)
        self.assertEqual(status_payload["failed_rows"], 1)

        api_errors = self.client.get(f"/api/v1/imports/{staged.id}/errors")
        self.assertEqual(api_errors.status_code, 200)
        api_error_payload = api_errors.get_json()
        self.assertEqual(api_error_payload["pagination"]["total"], 1)
        self.assertEqual(api_error_payload["data"][0]["raw_data"]["ID"], "R002")

        error_report = self.client.get(f"/data/imports/{staged.id}/errors.csv")
        self.assertEqual(error_report.status_code, 200)
        self.assertIn(b"invalid_student_row", error_report.data)
        self.assertIn(b"Special_Needs", error_report.data)

        committed = self.client.post(f"/data/imports/{staged.id}/commit")
        self.assertEqual(committed.status_code, 302)
        self.assertEqual(Student.query.count(), 1)
        self.assertEqual(DataImport.query.one().status, "completed")

    def test_staged_import_is_idempotent_for_same_file_and_metadata(self):
        csv_bytes = b"ID,Class,Subject\nR001,CSE-A,Discrete Mathematics\n"

        def upload():
            return self.client.post(
                "/data/import-students",
                data={
                    "dataset_name": "Idempotent Import",
                    "dataset_version": "v1",
                    "exam_date": "2026-12-01",
                    "start_time": "09:00",
                    "end_time": "12:00",
                    "session_name": "Morning",
                    "academic_year": "2026-27",
                    "csv_file": (io.BytesIO(csv_bytes), "students.csv"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(upload().status_code, 302)
        self.assertEqual(upload().status_code, 302)
        self.assertEqual(DataImport.query.count(), 1)
        self.assertEqual(ImportStagingStudent.query.count(), 1)

    def test_staged_import_persists_progress_across_configurable_chunks(self):
        rows = "\n".join(
            f"R{index:04d},CSE-{index % 4},Discrete Mathematics,N"
            for index in range(1, 251)
        )
        upload = FileStorage(
            stream=io.BytesIO(
                ("ID,Class,Subject,Special_Needs\n" + rows).encode("utf-8")
            ),
            filename="chunked.csv",
            content_type="text/csv",
        )
        staged, reused = stage_student_import(
            upload,
            dataset_name="Chunked Import",
            dataset_version="v1",
            exam_date=date(2026, 12, 1),
            start_time=time(9, 0),
            end_time=time(12, 0),
            session_name="Morning",
            academic_year="2026-27",
            chunk_size=100,
        )
        self.assertFalse(reused)
        self.assertEqual(staged.status, "validated")
        self.assertEqual(staged.total_rows, 250)
        self.assertEqual(staged.processed_rows, 250)
        self.assertEqual(staged.valid_rows, 250)
        self.assertEqual(ImportStagingStudent.query.count(), 250)

    def test_synthetic_generation_is_persisted_and_connected_to_frontend(self):
        res = self.client.post(
            "/data/generate-synthetic",
            data={
                "dataset_name": "Synthetic Integration",
                "dataset_version": "seed-99",
                "seed": "99",
                "student_count": "6",
                "class_count": "3",
                "subject_count": "2",
                "room_count": "1",
                "rows_per_room": "2",
                "benches_per_row": "3",
                "seats_per_bench": "4",
                "exam_date": "2026-12-02",
            },
        )
        self.assertEqual(res.status_code, 302)
        dataset = Dataset.query.one()
        exam = Exam.query.order_by(Exam.id).first()
        exam_session = ExamSession.query.one()
        room = Room.query.one()
        self.assertEqual(dataset.source_type, "synthetic")
        self.assertEqual(dataset.generation_seed, 99)
        self.assertEqual(Student.query.count(), 6)
        self.assertEqual(room.seats_per_bench, 4)

        index_res = self.client.get("/")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn(b"Synthetic Integration", index_res.data)
        self.assertIn(room.room_number.encode(), index_res.data)

        assign_res = self.client.post(
            "/assign",
            data={
                "data_mode": "database",
                "exam_session_id": str(exam_session.id),
                "room_ids": str(room.id),
                "invigilator_ratio": "20",
                "adjacency_rule": "orthogonal",
            },
        )
        self.assertEqual(assign_res.status_code, 200)
        self.assertIn(b"Persisted seating run", assign_res.data)
        self.assertEqual(SeatingRun.query.filter_by(status="completed").count(), 1)

        run = SeatingRun.query.filter_by(status="completed").one()
        first_student = Student.query.order_by(Student.id).first()
        first_student.student_code = '=HYPERLINK("https://example.test")'
        db.session.commit()
        reports_res = self.client.get("/reports")
        self.assertEqual(reports_res.status_code, 200)
        self.assertIn(f"#{run.id}".encode(), reports_res.data)

        detail_res = self.client.get(f"/reports/runs/{run.id}")
        self.assertEqual(detail_res.status_code, 200)
        self.assertIn(b"Persisted allocations", detail_res.data)

        pdf_res = self.client.get(f"/reports/runs/{run.id}.pdf")
        self.assertEqual(pdf_res.status_code, 200)
        self.assertEqual(pdf_res.content_type, "application/pdf")
        pdf_reader = PdfReader(io.BytesIO(pdf_res.data))
        pdf_text = "\n".join(page.extract_text() or "" for page in pdf_reader.pages)
        self.assertIn("Examination seating report", pdf_text)
        self.assertIn("Synthetic Integration", pdf_text)

        excel_res = self.client.get(f"/reports/runs/{run.id}.xlsx")
        self.assertEqual(excel_res.status_code, 200)
        workbook = load_workbook(io.BytesIO(excel_res.data), data_only=False)
        self.assertEqual(workbook.sheetnames, ["Summary", "Allocations"])
        self.assertEqual(workbook["Summary"]["A2"].value, "Examination seating report")
        self.assertTrue(workbook["Summary"]["E7"].value.startswith("=COUNTA"))
        self.assertEqual(workbook["Allocations"].max_row, 7)
        self.assertTrue(workbook["Allocations"]["F2"].value.startswith("'="))
        self.assertEqual(
            AuditLog.query.filter_by(action="seating_report_exported").count(), 2
        )

    def test_database_run_rejects_rooms_from_another_dataset(self):
        generation_form = {
            "dataset_version": "v1",
            "seed": "11",
            "student_count": "4",
            "class_count": "2",
            "subject_count": "1",
            "room_count": "1",
            "rows_per_room": "1",
            "benches_per_row": "2",
            "seats_per_bench": "2",
            "exam_date": "2026-12-02",
        }
        self.client.post(
            "/data/generate-synthetic",
            data={**generation_form, "dataset_name": "Dataset A", "seed": "11"},
        )
        self.client.post(
            "/data/generate-synthetic",
            data={**generation_form, "dataset_name": "Dataset B", "seed": "12"},
        )
        dataset_a = Dataset.query.filter_by(name="Dataset A").one()
        dataset_b = Dataset.query.filter_by(name="Dataset B").one()
        session_a = ExamSession.query.filter_by(dataset_id=dataset_a.id).one()
        room_b = Room.query.filter_by(dataset_id=dataset_b.id).one()

        res = self.client.post(
            "/assign",
            data={
                "data_mode": "database",
                "exam_session_id": str(session_a.id),
                "room_ids": str(room_b.id),
                "invigilator_ratio": "20",
                "adjacency_rule": "orthogonal",
            },
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"same real or synthetic dataset", res.data)
        self.assertEqual(SeatingRun.query.count(), 0)

    def test_assign_route_balanced(self):
        form_data = {
            "num_rooms": "1",
            "rows": "3",
            "cols": "2",
            "seats_per_bench": "2",
            "accessible_rule": "row_1_and_aisle",
            "invigilator_ratio": "20",
            "adjacency_rule": "orthogonal",
            "csv_file": (io.BytesIO(
                b"ID,Class,Subject,Special_Needs\n"
                b"S1,ClassA,DiscreteMath,Y\n"
                b"S2,ClassB,Algorithms,N\n"
                b"S3,ClassA,DiscreteMath,N\n"
                b"S4,ClassB,Algorithms,N\n"
                b"S5,ClassA,DiscreteMath,N\n"
                b"S6,ClassB,Algorithms,N\n"
            ), "students.csv")
        }
        res = self.client.post("/assign", data=form_data)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Seating plan complete", res.data)
        self.assertIn(b"Step 1", res.data)
        self.assertIn(b"Handshaking Lemma", res.data)
        self.assertIn(b"Chromatic Polynomial", res.data)
        self.assertIn(b"Principle of Inclusion-Exclusion", res.data)

    def test_manual_planner_accepts_three_and_four_seat_shared_benches(self):
        for seats_per_bench in (3, 4):
            with self.subTest(seats_per_bench=seats_per_bench):
                students = "\n".join(
                    f"S{index},Class{index},Subject{index},N"
                    for index in range(1, seats_per_bench + 1)
                )
                response = self.client.post(
                    "/assign",
                    data={
                        "data_mode": "manual",
                        "num_rooms": "1",
                        "rows": "1",
                        "cols": "1",
                        "seats_per_bench": str(seats_per_bench),
                        "accessible_rule": "row_1_and_aisle",
                        "invigilator_ratio": "20",
                        "adjacency_rule": "orthogonal",
                        "csv_file": (io.BytesIO(
                            ("ID,Class,Subject,Special_Needs\n" + students).encode("utf-8")
                        ), "students.csv"),
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Seating plan complete", response.data)
                self.assertIn(f"Seat {seats_per_bench}".encode(), response.data)

    def test_assign_route_pigeonhole_dense(self):
        # 10 students of same class in a 12-seat room (alpha=6) -> triggers PHP alert
        students = "\n".join([f"S{i},HeavyClass,Math,N" for i in range(1, 10)])
        form_data = {
            "num_rooms": "1",
            "rows": "3",
            "cols": "2",
            "seats_per_bench": "2",
            "accessible_rule": "row_1",
            "csv_file": (io.BytesIO(
                f"ID,Class,Subject,Special_Needs\n{students}".encode("utf-8")
            ), "students.csv")
        }
        res = self.client.post("/assign", data=form_data)
        self.assertEqual(res.status_code, 422)
        self.assertIn(b"Pigeonhole", res.data)
        self.assertIn(b"No seating plan was generated", res.data)
        self.assertNotIn(b"Recommended layouts for this CSV", res.data)

    def test_layout_recommendation_api_returns_one_click_field_values(self):
        students = []
        for class_name, count in {
            "Electronics": 36,
            "Computer Science": 32,
            "Mechanical": 27,
            "Civil": 25,
        }.items():
            students.extend(
                f"{class_name}-{index},{class_name},Mathematics,N"
                for index in range(count)
            )
        csv_text = "ID,Class,Subject,Special_Needs\n" + "\n".join(students)

        response = self.client.post(
            "/api/layout-recommendations",
            data={
                "csv_file": (io.BytesIO(csv_text.encode("utf-8")), "students.csv"),
                "adjacency_rule": "orthogonal",
                "accessible_rule": "row_1_and_aisle",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["student_count"], 120)
        self.assertEqual(payload["largest_class"], 36)
        self.assertEqual(payload["limits"], {
            "max_seats_per_room": 40,
            "max_rooms": 10,
        })
        self.assertEqual(
            payload["recommendations"][0],
            {
                "rooms": 4,
                "rows": 4,
                "benches": 2,
                "seats_per_bench": 4,
                "capacity_per_room": 32,
                "capacity": 128,
                "spare_seats": 8,
                "independence_bound": 64,
                "accessible_seats": 80,
                "student_count": 120,
                "largest_class": 36,
                "accessibility_count": 0,
                "adjacency": "orthogonal",
            },
        )

    def test_recommended_multi_room_layout_completes_assignment(self):
        students = []
        for class_name, count in {
            "Electronics": 36,
            "Computer Science": 32,
            "Mechanical": 27,
            "Civil": 25,
        }.items():
            students.extend(
                f"{class_name}-{index},{class_name},Mathematics,N"
                for index in range(count)
            )

        response = self.client.post(
            "/assign",
            data={
                "data_mode": "manual",
                "num_rooms": "4",
                "rows": "4",
                "cols": "2",
                "seats_per_bench": "4",
                "accessible_rule": "row_1_and_aisle",
                "invigilator_ratio": "20",
                "adjacency_rule": "orthogonal",
                "csv_file": (io.BytesIO(
                    ("ID,Class,Subject,Special_Needs\n" + "\n".join(students)).encode("utf-8")
                ), "students.csv"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Seating plan complete", response.data)
        self.assertNotIn(b"Application Error", response.data)

    def test_layout_recommendations_respect_college_room_limits(self):
        students = "\n".join(
            f"S{index},Class{index % 4},Mathematics,N"
            for index in range(1, 81)
        )
        response = self.client.post(
            "/api/layout-recommendations",
            data={
                "csv_file": (io.BytesIO(
                    ("ID,Class,Subject,Special_Needs\n" + students).encode("utf-8")
                ), "students.csv"),
                "max_seats_per_room": "24",
                "max_rooms": "5",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["recommendations"])
        self.assertTrue(all(
            item["capacity_per_room"] <= 24 and item["rooms"] <= 5
            for item in payload["recommendations"]
        ))

    def test_layout_recommendations_explain_impossible_college_limits(self):
        students = "\n".join(
            f"S{index},Class{index % 4},Mathematics,N"
            for index in range(1, 81)
        )
        response = self.client.post(
            "/api/layout-recommendations",
            data={
                "csv_file": (io.BytesIO(
                    ("ID,Class,Subject,Special_Needs\n" + students).encode("utf-8")
                ), "students.csv"),
                "max_seats_per_room": "20",
                "max_rooms": "2",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "No verified layout fits within 20 seats per room and 2 available room(s)",
            response.get_json()["error"]["message"],
        )

    def test_layout_recommendation_api_rejects_invalid_csv(self):
        response = self.client.post(
            "/api/layout-recommendations",
            data={
                "csv_file": (
                    io.BytesIO(b"Name,Subject\nAsha,Math\n"),
                    "students.csv",
                )
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_student_data")

    def test_layout_recommendations_follow_csv_distribution(self):
        students = []
        for class_name, count in {
            "Electronics": 36,
            "Computer Science": 32,
            "Mechanical": 27,
            "Civil": 25,
        }.items():
            students.extend(
                {"id": f"{class_name}-{index}", "class": class_name, "special_needs": False}
                for index in range(count)
            )

        recommendations = recommend_room_layouts(students, diagonal=False)
        four_seat = recommendations[0]
        self.assertEqual(four_seat["student_count"], 120)
        self.assertEqual(four_seat["largest_class"], 36)
        self.assertEqual(four_seat["rooms"], 2)
        self.assertEqual(four_seat["rows"], 4)
        self.assertEqual(four_seat["benches"], 4)
        self.assertEqual(four_seat["seats_per_bench"], 4)
        self.assertEqual(four_seat["capacity"], 128)
        self.assertEqual(four_seat["spare_seats"], 8)
        self.assertGreaterEqual(four_seat["independence_bound"], 36)

    def test_duplicate_student_ids_are_rejected(self):
        csv_text = (
            "ID,Class,Subject,Special_Needs\n"
            "S1,ClassA,Math,N\n"
            "s1,ClassB,Physics,N\n"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate student ID"):
            parse_csv_students(csv_text)

    def test_csv_parser_accepts_text_and_byte_streams(self):
        csv_text = "ID,Class,Subject,Special_Needs\nS1,ClassA,Math,Y\n"
        from_text = parse_csv_students(io.StringIO(csv_text))
        from_bytes = parse_csv_students(io.BytesIO(csv_text.encode("utf-8")))
        self.assertEqual(from_text, from_bytes)
        self.assertTrue(from_text[0]["special_needs"])

    def test_invalid_room_configuration_returns_400(self):
        res = self.client.post("/assign", data={
            "num_rooms": "0",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Number of rooms must be between 1 and 10", res.data)

    def test_untrusted_write_origin_is_blocked(self):
        res = self.client.post(
            "/assign",
            data={},
            headers={"Origin": "https://untrusted.example"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", res.headers)

    def test_allowlisted_origin_receives_exact_cors_header(self):
        previous = app.config["CORS_ALLOWED_ORIGINS"]
        app.config["CORS_ALLOWED_ORIGINS"] = {"https://frontend.example"}
        try:
            res = self.client.get("/", headers={"Origin": "https://frontend.example"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(
                res.headers.get("Access-Control-Allow-Origin"),
                "https://frontend.example",
            )
        finally:
            app.config["CORS_ALLOWED_ORIGINS"] = previous

    def test_oversized_request_returns_413(self):
        res = self.client.post(
            "/assign",
            data={
                "csv_file": (
                    io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)),
                    "students.csv",
                )
            },
        )
        self.assertEqual(res.status_code, 413)
        self.assertIn(b"Upload Too Large", res.data)

    def test_export_csv_route(self):
        sample_alloc = [
            {
                "room_id": 1,
                "seat_id": "R1-r1-c1-s1",
                "seat_label": "Room 1 [Row 1, Bench 1, Seat 1]",
                "student_id": "STU001",
                "class": "ClassA",
                "subject": "Discrete Math",
                "special_needs": "Yes",
                "relaxed": "No"
            }
        ]
        res = self.client.post("/export-csv", data={"allocation_json": json.dumps(sample_alloc)})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "text/csv; charset=utf-8")
        self.assertIn(b"STU001", res.data)
        self.assertIn(b"Discrete Math", res.data)

    def test_export_csv_prevents_spreadsheet_formula_injection(self):
        sample_alloc = [{"student_id": "=HYPERLINK(\"https://example.test\")"}]
        res = self.client.post(
            "/export-csv",
            data={"allocation_json": json.dumps(sample_alloc)},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"'=HYPERLINK", res.data)


class TestPublicAccessSecurity(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.app.config["WTF_CSRF_ENABLED"] = False

    def csrf_token(self, path="/"):
        response = self.client.get(path)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def test_project_pages_open_without_login(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/data").status_code, 200)
        self.assertEqual(self.client.get("/reports").status_code, 200)
        self.assertEqual(self.client.get("/login").status_code, 404)

    def test_optimizer_post_still_requires_csrf_token(self):
        response = self.client.post(
            "/assign",
            data={"data_mode": "manual"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Security Token Invalid", response.data)

    def test_layout_recommendation_post_requires_csrf_token(self):
        response = self.client.post(
            "/api/layout-recommendations",
            data={},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "csrf_failed")

    def test_public_pages_are_not_cached(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers.get("Cache-Control"), "no-store")
        self.assertEqual(self.client.get("/reports/runs/999.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()
