import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from flask import g

from app import app
from extensions import db
from models import AuditLog, Dataset, Institution, User


class SecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        first = Institution(code="ONE", name="Institution One")
        second = Institution(code="TWO", name="Institution Two")
        self.admin = User(
            institution=first, username="admin-one", role="admin", active=True
        )
        self.viewer = User(
            institution=first, username="viewer-one", role="viewer", active=True
        )
        self.planner = User(
            institution=first, username="planner-one", role="planner", active=True
        )
        self.other_admin = User(
            institution=second, username="admin-two", role="admin", active=True
        )
        for user in (self.admin, self.viewer, self.planner, self.other_admin):
            user.set_password("TestingPass123")
        db.session.add_all([
            first, second, self.admin, self.viewer, self.planner, self.other_admin
        ])
        db.session.flush()
        db.session.add_all([
            Dataset(
                institution_id=first.id,
                name="Visible dataset",
                version="1",
                source_type="real",
                status="active",
            ),
            Dataset(
                institution_id=second.id,
                name="Other institution dataset",
                version="1",
                source_type="real",
                status="active",
            ),
        ])
        db.session.commit()
        self.first_id = first.id
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.app.config["WTF_CSRF_ENABLED"] = False

    def login_as(self, user):
        if hasattr(g, "institution_id"):
            del g.institution_id
        with self.client.session_transaction() as session:
            session.clear()
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        if hasattr(g, "_login_user"):
            del g._login_user

    def test_tenant_filter_hides_other_institution_records(self):
        self.login_as(self.admin)
        response = self.client.get("/api/v1/datasets?per_page=100")
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.get_json()["data"]]
        self.assertEqual(names, ["Visible dataset"])
        self.assertEqual(self.client.get("/api/v1/datasets/2").status_code, 404)

    def test_viewer_can_read_but_cannot_write_or_open_administration(self):
        self.login_as(self.viewer)
        self.assertEqual(self.client.get("/api/v1/datasets").status_code, 200)
        forbidden = self.client.post("/api/v1/datasets/synthetic", json={})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.get_json()["error"]["code"], "forbidden")
        self.assertEqual(self.client.get("/administration").status_code, 403)

        self.login_as(self.planner)
        planner_write = self.client.post("/api/v1/datasets/synthetic", json={})
        self.assertEqual(planner_write.status_code, 422)
        self.assertEqual(self.client.get("/administration").status_code, 403)

    def test_login_creates_authenticated_audit_and_rejects_external_next_url(self):
        with self.client.session_transaction() as session:
            session.clear()
        if hasattr(g, "_login_user"):
            del g._login_user
        response = self.client.post(
            "/login?next=https://attacker.example/steal",
            data={"username": "admin-one", "password": "TestingPass123"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/"))
        event = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "login_succeeded")
        )
        self.assertEqual(event.actor_user_id, self.admin.id)
        self.assertEqual(event.institution_id, self.first_id)

    def test_admin_can_provision_scoped_planner_account(self):
        self.login_as(self.admin)
        response = self.client.post(
            "/administration/users",
            data={
                "username": "new-planner",
                "email": "planner@example.test",
                "role": "planner",
                "password": "ProvisionedPass123",
            },
        )
        self.assertEqual(response.status_code, 302)
        account = db.session.scalar(
            db.select(User).where(User.username == "new-planner")
        )
        self.assertEqual(account.institution_id, self.first_id)
        self.assertEqual(account.role, "planner")
        page = self.client.get("/administration/users")
        self.assertIn(b"new-planner", page.data)
        self.assertNotIn(b"admin-two", page.data)
        event = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "user_created")
        )
        self.assertEqual(event.actor_user_id, self.admin.id)

    def test_audit_records_authenticated_actor_and_institution(self):
        self.login_as(self.admin)
        response = self.client.post(
            "/api/v1/datasets/synthetic",
            json={
                "dataset_name": "Audited dataset",
                "dataset_version": "1",
                "seed": 7,
                "student_count": 2,
                "class_count": 2,
                "subject_count": 2,
                "room_count": 1,
                "rows_per_room": 1,
                "benches_per_row": 1,
                "seats_per_bench": 2,
                "exam_date": "2030-01-01",
            },
        )
        self.assertEqual(response.status_code, 201)
        event = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "synthetic_dataset_generated_api")
        )
        self.assertEqual(event.actor_user_id, self.admin.id)
        self.assertEqual(event.institution_id, self.first_id)
        self.assertIn("admin-one", event.actor)
        self.assertIsNotNone(event.request_id)


if __name__ == "__main__":
    unittest.main()
