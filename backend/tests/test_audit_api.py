"""Tests for the audit API surface and the audit trail guarantees.

* Reading ``/audit`` (list + per-job) is admin-only.
* The ``limit`` parameter is bounded.
* Login success/failure and user creation are audited without ever
  recording password material.
* Audit ``details`` are sanitized centrally (no secrets persisted).
* The audit log is append-only: the API exposes no mutation route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.audit import AuditAgent
from app.database.session import session_scope
from app.main import app
from app.models import Base
from app.models.entities import AuditLog
from app.security.passwords import hash_password

client = TestClient(app)


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app.models.entities import User as UserCls

    db = tmp_path / "audit.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())

    with session_scope() as session:
        admin = UserCls(
            username="root",
            password_hash=hash_password("CHANGE_ME_ADMIN_PASSWORD"),
            role="admin",
            is_active=True,
        )
        user = UserCls(
            username="alice",
            password_hash=hash_password("alice-pwd-12345"),
            role="user",
            is_active=True,
        )
        session.add_all([admin, user])
        session.commit()
        session.refresh(admin)
        session.refresh(user)
        ids = {"admin": admin.id, "user": user.id}
    return ids


def _headers(env, who: str = "admin") -> dict[str, str]:
    from app.core.config import get_settings
    from app.security.tokens import create_access_token

    token = create_access_token(
        subject=env[who],
        secret=get_settings().jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# authorization + bounds
# ---------------------------------------------------------------------------


def test_audit_requires_auth(env):
    assert client.get("/audit").status_code == 401
    assert client.get("/audit/jobs/anything").status_code == 401


def test_audit_forbidden_for_non_admin(env):
    assert client.get("/audit", headers=_headers(env, "user")).status_code == 403
    assert client.get("/audit/jobs/x", headers=_headers(env, "user")).status_code == 403


def test_audit_limit_bounds(env):
    admin = _headers(env)
    assert client.get("/audit?limit=0", headers=admin).status_code == 422
    assert client.get("/audit?limit=1001", headers=admin).status_code == 422
    assert client.get("/audit?limit=1000", headers=admin).status_code == 200
    assert client.get("/audit/jobs/x?limit=0", headers=admin).status_code == 422


def test_audit_is_append_only_no_mutation_routes(env):
    """The application must never expose a way to edit or delete audit rows."""
    mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}
    paths = client.get("/openapi.json").json()["paths"]
    audit_paths = {p: ops for p, ops in paths.items() if p.startswith("/audit")}
    assert audit_paths, "expected /audit routes to exist"
    for path, ops in audit_paths.items():
        offending = set(ops) & mutation_methods
        assert not offending, f"audit route {path} exposes mutation methods {offending}"


# ---------------------------------------------------------------------------
# content guarantees
# ---------------------------------------------------------------------------


def test_audit_rows_returned_for_admin(env):
    with session_scope() as session:
        AuditAgent(session).write(
            event_type="test.event", outcome="success", details={"table": "T"}
        )
    r = client.get("/audit", headers=_headers(env))
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["event_type"] == "test.event" for i in items)


def test_audit_details_are_sanitized_before_persist(env):
    """Secret-shaped keys/values never reach the audit table."""
    with session_scope() as session:
        AuditAgent(session).write(
            event_type="test.secret",
            outcome="failure",
            details={
                "password": "hunter2",
                "connection_string": "DRIVER={ODBC};PWD=hunter2;UID=sa",
                "error": "Login failed. PWD=hunter2seekrit",
                "table": "Customers",  # operational text is preserved
            },
        )
    with session_scope() as session:
        row = session.execute(
            select(AuditLog).where(AuditLog.event_type == "test.secret")
        ).scalar_one()
    assert row.details["password"] == "[REDACTED]"
    assert row.details["connection_string"] == "[REDACTED]"
    assert "hunter2seekrit" not in row.details["error"]
    assert row.details["table"] == "Customers"


def test_login_failures_are_audited_without_secrets(env):
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong-pwd-99999"})
    assert r.status_code == 401

    r2 = client.post("/auth/login", json={"username": "alice", "password": "alice-pwd-12345"})
    assert r2.status_code == 200

    with session_scope() as session:
        rows = list(
            session.execute(select(AuditLog).where(AuditLog.event_type == "auth.login")).scalars()
        )
    outcomes = sorted(row.outcome for row in rows)
    assert outcomes == ["failure", "success"]
    for row in rows:
        assert row.details["username"] == "alice"
        # The password (attempted or correct) is never recorded.
        assert "wrong-pwd-99999" not in str(row.details)
        assert "alice-pwd-12345" not in str(row.details)
        assert "password" not in row.details


def test_user_creation_is_audited(env):
    r = client.post(
        "/users",
        headers=_headers(env),
        json={"username": "bobby", "password": "bobby-pwd-1234", "role": "user"},
    )
    assert r.status_code == 201
    with session_scope() as session:
        row = session.execute(
            select(AuditLog).where(AuditLog.event_type == "auth.user_create")
        ).scalar_one()
    assert row.details["username"] == "bobby"
    assert row.details["role"] == "user"
    assert "bobby-pwd-1234" not in str(row.details)


def test_per_job_endpoint_filters_by_job(env):
    with session_scope() as session:
        AuditAgent(session).write(event_type="e.other", outcome="ok", job_id=None)
        AuditAgent(session).write(event_type="e.job", outcome="ok", job_id="job-123")
    r = client.get("/audit/jobs/job-123", headers=_headers(env))
    assert r.status_code == 200
    events = {i["event_type"] for i in r.json()["items"]}
    assert events == {"e.job"}
