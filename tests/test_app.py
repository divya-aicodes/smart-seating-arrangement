"""
Integration tests for Flask routes, CSV handling, and results rendering.
"""

import unittest
import json
import io
import os
import re
from openpyxl import load_workbook
from pypdf import PdfReader

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app, parse_csv_students, MAX_UPLOAD_BYTES
from extensions import db
from models import AuditLog, DataImport, Dataset, Exam, Room, SeatingRun, Student


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
        dataset = Dataset.query.one()
        self.assertEqual(dataset.source_type, "real")
        self.assertEqual(Student.query.count(), 2)
        self.assertEqual(Exam.query.count(), 1)
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
                "seats_per_bench": "2",
                "exam_date": "2026-12-02",
            },
        )
        self.assertEqual(res.status_code, 302)
        dataset = Dataset.query.one()
        exam = Exam.query.one()
        room = Room.query.one()
        self.assertEqual(dataset.source_type, "synthetic")
        self.assertEqual(dataset.generation_seed, 99)
        self.assertEqual(Student.query.count(), 6)

        index_res = self.client.get("/")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn(b"Synthetic Integration", index_res.data)
        self.assertIn(room.room_number.encode(), index_res.data)

        assign_res = self.client.post(
            "/assign",
            data={
                "data_mode": "database",
                "exam_id": str(exam.id),
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
        exam_a = Exam.query.filter_by(dataset_id=dataset_a.id).one()
        room_b = Room.query.filter_by(dataset_id=dataset_b.id).one()

        res = self.client.post(
            "/assign",
            data={
                "data_mode": "database",
                "exam_id": str(exam_a.id),
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
            "student_data_text": (
                "ID,Class,Subject,Special_Needs\n"
                "S1,ClassA,DiscreteMath,Y\n"
                "S2,ClassB,Algorithms,N\n"
                "S3,ClassA,DiscreteMath,N\n"
                "S4,ClassB,Algorithms,N\n"
                "S5,ClassA,DiscreteMath,N\n"
                "S6,ClassB,Algorithms,N\n"
            )
        }
        res = self.client.post("/assign", data=form_data)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Seating plan complete", res.data)
        self.assertIn(b"Step 1", res.data)
        self.assertIn(b"Handshaking Lemma", res.data)
        self.assertIn(b"Chromatic Polynomial", res.data)
        self.assertIn(b"Principle of Inclusion-Exclusion", res.data)

    def test_assign_route_pigeonhole_dense(self):
        # 10 students of same class in a 12-seat room (alpha=6) -> triggers PHP alert
        students = "\n".join([f"S{i},HeavyClass,Math,N" for i in range(1, 10)])
        form_data = {
            "num_rooms": "1",
            "rows": "3",
            "cols": "2",
            "seats_per_bench": "2",
            "accessible_rule": "row_1",
            "student_data_text": f"ID,Class,Subject,Special_Needs\n{students}"
        }
        res = self.client.post("/assign", data=form_data)
        self.assertEqual(res.status_code, 422)
        self.assertIn(b"Pigeonhole", res.data)
        self.assertIn(b"No seating plan was generated", res.data)

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
            "student_data_text": "ID,Class\nS1,ClassA\n",
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
            data={"student_data_text": "x" * (MAX_UPLOAD_BYTES + 1)},
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
            data={"data_mode": "manual", "student_data_text": "ID,Class\nS1,A\n"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Security Token Invalid", response.data)

    def test_public_pages_are_not_cached(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers.get("Cache-Control"), "no-store")
        self.assertEqual(self.client.get("/reports/runs/999.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()
