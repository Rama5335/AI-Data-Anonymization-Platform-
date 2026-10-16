"""Tests for ``POST /updates/rollback`` (restore from verified backup).

The rollback endpoint is admin-gated and must refuse to touch the
target unless the requested backup exists under the backup directory
and is recorded as *verified* for the requested database. Every attempt
is audited with the caller's identity (start / complete / failed).
"""

from __future__ import annotations

import pyodbc
import pytest
from sqlalchemy import select

from app.database.session import session_scope
from app.main import app
from app.models import Base
from app.models.entities import AuditLog, BackupRecord
from app.security.passwords import hash_password
from fastapi.testclient import TestClient

client = TestClient(app)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app.models.entities import User

    db = tmp_path / "rollback.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())

    with session_scope() as session:
        admin = User(
            username="root",
            password_hash=hash_password("CHANGE_ME_ADMIN_PASSWORD"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.commit()
        session.refresh(admin)
        admin_id = admin.id
    return {"backup_dir": backup_dir, "admin_id": admin_id}


def _headers(env) -> dict[str, str]:
    from app.core.config import get_settings
    from app.security.tokens import create_access_token

    token = create_access_token(
        subject=env["admin_id"],
        secret=get_settings().jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
    )
    return {"Authorization": f"Bearer {token}"}


def _conn_payload() -> dict:
    return {
        "host": "127.0.0.1",
        "port": 1433,
        "auth_type": "sql",
        "username": "sa",
        "password": "CHANGE_ME_SA_PASSWORD",
        "database": "anonymizer_app",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": True,
        "timeout_seconds": 5,
    }


def _make_backup_file(env, name: str = "anonymizer_app_20260101T000000Z.bak"):
    bak = env["backup_dir"] / name
    bak.write_bytes(b"x" * 10)
    return bak


def _add_record(path, *, verified=True, database="anonymizer_app", job_id=None):
    from app.agents.backup import _normalize_backup_path

    with session_scope() as session:
        session.add(
            BackupRecord(
                database_name=database,
                backup_path=_normalize_backup_path(str(path)),
                verified=verified,
                verification_method="RESTORE VERIFYONLY" if verified else None,
                job_id=job_id,
            )
        )
        session.commit()


def _rollback_events(event_type: str) -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.execute(select(AuditLog).where(AuditLog.event_type == event_type)).scalars()
        )


def _post(env, backup_path: str, database: str = "anonymizer_app"):
    return client.post(
        "/updates/rollback",
        headers=_headers(env),
        json={"connection": _conn_payload(), "backup_path": backup_path, "database": database},
    )


# ---------------------------------------------------------------------------
# authorization
# ---------------------------------------------------------------------------


def test_rollback_requires_auth(env):
    r = client.post(
        "/updates/rollback",
        json={"connection": _conn_payload(), "backup_path": "x.bak", "database": "d"},
    )
    assert r.status_code == 401


def test_rollback_forbidden_for_non_admin(env, monkeypatch, tmp_path):
    from app.models.entities import User

    with session_scope() as session:
        u = User(username="alice", password_hash=hash_password("alice-pwd-1234"), role="user")
        session.add(u)
        session.commit()
        session.refresh(u)
        uid = u.id
    from app.core.config import get_settings
    from app.security.tokens import create_access_token

    token = create_access_token(
        subject=uid,
        secret=get_settings().jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
    )
    r = client.post(
        "/updates/rollback",
        headers={"Authorization": f"Bearer {token}"},
        json={"connection": _conn_payload(), "backup_path": "x.bak", "database": "d"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# pre-flight refusals (target untouched)
# ---------------------------------------------------------------------------


def test_rollback_rejects_unsafe_database_name(env):
    bak = _make_backup_file(env)
    _add_record(bak)
    r = _post(env, str(bak), database="db; DROP DATABASE foo")
    assert r.status_code == 422
    # Refused before any restore attempt: no audit rows for this db name.
    assert _rollback_events("rollback.start") == []


def test_rollback_rejects_path_outside_backup_dir(env, tmp_path):
    r = _post(env, str(tmp_path / "outside.bak"))
    assert r.status_code == 422
    assert _rollback_events("rollback.start") == []


def test_rollback_rejects_missing_file(env):
    r = _post(env, str(env["backup_dir"] / "ghost.bak"))
    assert r.status_code == 409
    assert _rollback_events("rollback.start") == []


def test_rollback_rejects_unrecorded_file(env):
    bak = _make_backup_file(env)
    r = _post(env, str(bak))
    assert r.status_code == 409
    assert "not created or verified" in r.json()["detail"]


def test_rollback_rejects_unverified_backup(env):
    bak = _make_backup_file(env)
    _add_record(bak, verified=False)
    r = _post(env, str(bak))
    assert r.status_code == 409
    assert "not verified" in r.json()["detail"]


def test_rollback_rejects_backup_for_other_database(env):
    bak = _make_backup_file(env)
    _add_record(bak, verified=True, database="another_db")
    r = _post(env, str(bak))
    assert r.status_code == 409
    assert "different database" in r.json()["detail"]


# ---------------------------------------------------------------------------
# restore execution (mocked pyodbc)
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self, fail_on: str | None = None):
        self.executed: list[str] = []
        self._fail_on = fail_on

    def execute(self, sql, *args):
        self.executed.append(sql)
        if self._fail_on is not None and self._fail_on in sql:
            raise pyodbc.Error("RESTORE DATABASE is not permitted.")

    def nextset(self) -> bool:
        return False

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_rollback_success_restores_and_audits(env, monkeypatch):
    bak = _make_backup_file(env)
    _add_record(bak, verified=True)

    cursor = _RecordingCursor()
    conn = _FakeConn(cursor)
    # ``rollback_from_backup`` imports pyodbc lazily; patch the module
    # attribute so the lazy import sees the fake.
    monkeypatch.setattr(pyodbc, "connect", lambda *a, **k: conn)

    r = _post(env, str(bak))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["database"] == "anonymizer_app"

    # The restore sequence ran in the documented order.
    assert any("SINGLE_USER" in sql for sql in cursor.executed)
    assert any(sql.startswith("RESTORE DATABASE [anonymizer_app]") for sql in cursor.executed)
    assert any("MULTI_USER" in sql for sql in cursor.executed)
    restore_idx = next(i for i, s in enumerate(cursor.executed) if s.startswith("RESTORE"))
    assert restore_idx > 0  # SINGLE_USER came first

    starts = _rollback_events("rollback.start")
    completes = _rollback_events("rollback.complete")
    assert len(starts) == 1 and len(completes) == 1
    assert starts[0].user_id == env["admin_id"]
    assert completes[0].user_id == env["admin_id"]
    assert completes[0].details["initiated_by"] == "root"

    # The failure audit must not exist on success.
    assert _rollback_events("rollback.failed") == []


def test_rollback_connect_failure_audited_and_redacted(env, monkeypatch):
    bak = _make_backup_file(env)
    _add_record(bak, verified=True)

    def _raise(*a, **k):
        raise pyodbc.Error("Login failed for user 'sa'. Password=hunter2seekrit.")

    monkeypatch.setattr(pyodbc, "connect", _raise)

    r = _post(env, str(bak))
    assert r.status_code == 503
    detail = r.json()["detail"]
    # Driver text is returned redacted: no password material leaks.
    assert "hunter2seekrit" not in detail

    failed = _rollback_events("rollback.failed")
    assert len(failed) == 1
    assert failed[0].details["stage"] == "connect"
    assert failed[0].user_id == env["admin_id"]
    import json as _json

    assert "hunter2seekrit" not in _json.dumps(failed[0].details)


def test_rollback_restore_failure_returns_to_multi_user_and_audits(env, monkeypatch):
    bak = _make_backup_file(env)
    _add_record(bak, verified=True)

    cursor = _RecordingCursor(fail_on="RESTORE DATABASE")
    monkeypatch.setattr(pyodbc, "connect", lambda *a, **k: _FakeConn(cursor))

    r = _post(env, str(bak))
    assert r.status_code == 503

    # Best-effort MULTI_USER after the failed restore.
    assert any("MULTI_USER" in sql for sql in cursor.executed)

    failed = _rollback_events("rollback.failed")
    assert len(failed) == 1
    assert failed[0].details["stage"] == "restore"
    assert _rollback_events("rollback.complete") == []
