"""Tests for the mandatory verified-backup-before-anonymization gate.

The security-hardening phase requires that a real (non-dry-run)
``SafeUpdateAgent.apply`` can never modify target data unless:

* ``confirm=True`` (explicit approval),
* the supplied backup path is inside the configured backup directory
  and the file exists on disk,
* a persisted ``BackupRecord`` exists for that path with
  ``verified=True`` (RESTORE VERIFYONLY succeeded) for the same target
  database and (when bound) the same job.

Also covers ``BackupAgent`` metadata persistence: every attempt —
success or failure — writes a ``BackupRecord`` and an audit row, and
gate failures are audited as ``safe_update.backup_gate``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pyodbc
import pytest
from sqlalchemy import select

import app.agents.backup as backup_mod
from app.agents.backup import BackupAgent, BackupError, _normalize_backup_path
from app.agents.safe_update import SafeUpdateAgent, UpdateError
from app.database.session import session_scope
from app.models import Base
from app.models.entities import (
    AuditLog,
    BackupRecord,
    ClassificationResult,
    Connection,
    DiscoveredColumn,
    DiscoveredTable,
    Job,
    Server,
    User,
)
from app.security.passwords import hash_password


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Same isolated environment as the other agent test modules."""
    db = tmp_path / "gate.db"
    backup_dir = tmp_path / "backups"
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
    backup_dir.mkdir(parents=True, exist_ok=True)
    return {"backup_dir": backup_dir, "tmp_path": tmp_path}


def _seed_job_with_approval(env, *, decision: str = "override") -> str:
    """Create user/server/connection/job + one approved column; return job id."""
    with session_scope() as session:
        user = User(username="gate-user", password_hash=hash_password("pw-pw-pw-12345"))
        session.add(user)
        session.flush()
        server = Server(name="s", host="h", port=1433, auth_type="sql")
        session.add(server)
        session.flush()
        conn = Connection(
            server_id=server.id,
            database_name="anonymizer_app",
            encrypted_connection_string="!x",
        )
        session.add(conn)
        session.flush()
        job = Job(
            connection_id=conn.id,
            requested_by_user_id=user.id,
            status="running",
            workflow_state={},
        )
        session.add(job)
        session.flush()
        table = DiscoveredTable(
            job_id=job.id, schema_name="dbo", table_name="T", object_type="BASE TABLE"
        )
        session.add(table)
        session.flush()
        session.add(
            DiscoveredColumn(
                table_id=table.id,
                name="Id",
                ordinal_position=1,
                data_type="int",
                is_nullable=False,
                is_primary_key=True,
            )
        )
        session.add(
            DiscoveredColumn(
                table_id=table.id,
                name="Email",
                ordinal_position=2,
                data_type="nvarchar",
                is_nullable=True,
            )
        )
        cls = ClassificationResult(
            job_id=job.id,
            schema_name="dbo",
            table_name="T",
            column_name="Email",
            sensitivity="Email",
            confidence=0.9,
            reasoning="r",
            suggested_strategy="Hash",
            attempts=1,
            ok=True,
        )
        session.add(cls)
        session.flush()
        from app.models.entities import Approval

        session.add(
            Approval(
                classification_result_id=cls.id,
                approved_by_user_id=user.id,
                decision=decision,
                masking_strategy="Hash",
            )
        )
        session.commit()
        return job.id


def _add_backup_record(
    path: str,
    *,
    verified: bool = True,
    database: str = "anonymizer_app",
    job_id: str | None = None,
) -> None:
    with session_scope() as session:
        session.add(
            BackupRecord(
                database_name=database,
                backup_path=_normalize_backup_path(path),
                verified=verified,
                verification_method="RESTORE VERIFYONLY" if verified else None,
                job_id=job_id,
            )
        )
        session.commit()


def _gate_events() -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.execute(
                select(AuditLog).where(AuditLog.event_type == "safe_update.backup_gate")
            ).scalars()
        )


def _apply_gate_rejection(env, tmp_backup: str, job_id: str, match: str, reason: str) -> None:
    """Drive ``apply()`` to the gate and assert rejection + audit."""
    with session_scope() as session:
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        with pytest.raises(UpdateError, match=match):
            agent.apply(
                job_id,
                target_database="anonymizer_app",
                expected_backup_path=tmp_backup,
                confirm=True,
                user_id="some-user",
            )
    events = _gate_events()
    assert events, "gate failure must be audited"
    assert events[-1].details["reason"] == reason


# ---------------------------------------------------------------------------
# BackupAgent metadata persistence (mocked pyodbc)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, fail_on_call: int | None = None, error: Exception | None = None):
        self.calls: list[str] = []
        self._fail_on_call = fail_on_call
        self._error = error

    def execute(self, sql, *args):
        self.calls.append(sql)
        if self._fail_on_call == len(self.calls):
            raise self._error

    def nextset(self) -> bool:
        return False

    def close(self) -> None:
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _conn_details():
    from app.schemas.discovery import ConnectionDetails

    return ConnectionDetails(
        host="127.0.0.1",
        port=1433,
        auth_type="sql",
        username="sa",
        password="CHANGE_ME_SA_PASSWORD",
        database="anonymizer_app",
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=5,
    )


def _audit_events(event_type: str) -> list[AuditLog]:
    with session_scope() as session:
        return list(
            session.execute(select(AuditLog).where(AuditLog.event_type == event_type)).scalars()
        )


def test_backup_success_persists_verified_record_and_audit(env, monkeypatch):
    cursor = _FakeCursor()
    monkeypatch.setattr(backup_mod.pyodbc, "connect", lambda *a, **k: _FakeConn(cursor))

    agent = BackupAgent(backup_dir=env["backup_dir"], keep=3)
    result = agent.backup(_conn_details(), job_id=None, created_by_user_id="user-123")

    assert result.verified is True
    assert result.backup_path == _normalize_backup_path(result.backup_path)

    # Both statements were issued against the server.
    assert any(sql.startswith("BACKUP DATABASE [anonymizer_app]") for sql in cursor.calls)
    assert any(sql.startswith("RESTORE VERIFYONLY") for sql in cursor.calls)

    with session_scope() as session:
        record = session.execute(
            select(BackupRecord).where(
                BackupRecord.backup_path == _normalize_backup_path(result.backup_path)
            )
        ).scalar_one()
    assert record.verified is True
    assert record.database_name == "anonymizer_app"
    assert record.verification_method == "RESTORE VERIFYONLY"
    assert record.error is None

    complete = _audit_events("backup.complete")
    assert len(complete) == 1
    assert complete[0].user_id == "user-123"
    assert complete[0].details["verified"] is True


def test_backup_connect_failure_records_unverified_row(env, monkeypatch):
    def _raise(*a, **k):
        raise pyodbc.Error("Login failed for user 'sa'.")

    monkeypatch.setattr(backup_mod.pyodbc, "connect", _raise)

    agent = BackupAgent(backup_dir=env["backup_dir"], keep=3)
    with pytest.raises(BackupError, match="Cannot connect"):
        agent.backup(_conn_details(), created_by_user_id="user-123")

    with session_scope() as session:
        records = list(session.execute(select(BackupRecord)).scalars())
    assert len(records) == 1
    assert records[0].verified is False
    assert "connect" in (records[0].error or "")
    # The original driver text is preserved (redaction only strips secrets).
    assert "Login failed" in (records[0].error or "")

    failed = _audit_events("backup.failed")
    assert len(failed) == 1
    assert failed[0].details["stage"] == "connect"
    assert failed[0].user_id == "user-123"


def test_backup_verify_failure_never_marked_verified(env, monkeypatch):
    # Call 1 = BACKUP DATABASE (ok), call 2 = RESTORE VERIFYONLY (fails).
    cursor = _FakeCursor(fail_on_call=2, error=pyodbc.Error("The backup set is corrupt."))
    monkeypatch.setattr(backup_mod.pyodbc, "connect", lambda *a, **k: _FakeConn(cursor))

    agent = BackupAgent(backup_dir=env["backup_dir"], keep=3)
    with pytest.raises(BackupError, match="VERIFYONLY"):
        agent.backup(_conn_details())

    with session_scope() as session:
        records = list(session.execute(select(BackupRecord)).scalars())
    assert len(records) == 1
    assert records[0].verified is False
    assert "verify" in (records[0].error or "")

    failed = _audit_events("backup.failed")
    assert failed[-1].details["stage"] == "verify"


def test_backup_statement_failure_records_stage(env, monkeypatch):
    cursor = _FakeCursor(fail_on_call=1, error=pyodbc.Error("BACKUP DATABASE failed: disk full."))
    monkeypatch.setattr(backup_mod.pyodbc, "connect", lambda *a, **k: _FakeConn(cursor))

    agent = BackupAgent(backup_dir=env["backup_dir"], keep=3)
    with pytest.raises(BackupError, match="BACKUP DATABASE"):
        agent.backup(_conn_details())

    with session_scope() as session:
        records = list(session.execute(select(BackupRecord)).scalars())
    assert len(records) == 1
    assert records[0].verified is False
    assert _audit_events("backup.failed")[-1].details["stage"] == "backup"


# ---------------------------------------------------------------------------
# SafeUpdateAgent.apply() — the backup gate
# ---------------------------------------------------------------------------


def test_gate_rejects_path_outside_backup_dir(env, tmp_path):
    job_id = _seed_job_with_approval(env)
    outside = str(tmp_path / "elsewhere" / "x.bak")
    _apply_gate_rejection(
        env, outside, job_id, match="not under", reason="backup_outside_backup_dir"
    )


def test_gate_rejects_missing_backup_file(env):
    job_id = _seed_job_with_approval(env)
    missing = str(env["backup_dir"] / "anonymizer_app_20260101T000000Z.bak")
    _apply_gate_rejection(
        env, missing, job_id, match="does not exist", reason="backup_file_missing"
    )


def test_gate_rejects_unrecorded_backup_file(env):
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "anonymizer_app_20260102T000000Z.bak"
    bak.parent.mkdir(parents=True, exist_ok=True)
    bak.write_bytes(b"x" * 10)
    _apply_gate_rejection(
        env, str(bak), job_id, match="no backup metadata", reason="backup_record_missing"
    )


def test_gate_rejects_unverified_backup(env):
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "anonymizer_app_20260103T000000Z.bak"
    bak.write_bytes(b"x" * 10)
    _add_backup_record(str(bak), verified=False)
    _apply_gate_rejection(env, str(bak), job_id, match="not verified", reason="backup_unverified")


def test_gate_rejects_backup_for_other_database(env):
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "other_20260104T000000Z.bak"
    bak.write_bytes(b"x" * 10)
    _add_backup_record(str(bak), verified=True, database="some_other_db")
    _apply_gate_rejection(
        env, str(bak), job_id, match="different database", reason="backup_database_mismatch"
    )


def test_gate_rejects_backup_bound_to_other_job(env):
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "anonymizer_app_20260105T000000Z.bak"
    bak.write_bytes(b"x" * 10)
    _add_backup_record(str(bak), verified=True, job_id="a-different-job")
    _apply_gate_rejection(
        env, str(bak), job_id, match="different job", reason="backup_job_mismatch"
    )


def test_gate_binds_unbound_verified_backup_to_job(env):
    """The happy path of the gate itself: a verified, unbound record is
    associated with the executing job and the gate passes."""
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "anonymizer_app_20260106T000000Z.bak"
    bak.write_bytes(b"x" * 10)
    _add_backup_record(str(bak), verified=True)

    with session_scope() as session:
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        # Only exercise the gate; the execution path needs a real server.
        agent._require_verified_backup(
            job_id=job_id,
            target_database="anonymizer_app",
            expected_backup_path=str(bak),
            user_id="user-123",
        )
        record = session.execute(
            select(BackupRecord).where(BackupRecord.backup_path == _normalize_backup_path(str(bak)))
        ).scalar_one()
        assert record.job_id == job_id

    assert _gate_events() == []  # no failure audited on the happy path


def test_gate_accepts_backup_already_bound_to_same_job(env):
    job_id = _seed_job_with_approval(env)
    bak = env["backup_dir"] / "anonymizer_app_20260107T000000Z.bak"
    bak.write_bytes(b"x" * 10)
    _add_backup_record(str(bak), verified=True, job_id=job_id)

    with session_scope() as session:
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        agent._require_verified_backup(
            job_id=job_id,
            target_database="anonymizer_app",
            expected_backup_path=str(bak),
            user_id=None,
        )


def test_dry_run_skips_backup_gate(env):
    """Dry-run must remain executable without any backup (no target is
    touched), preserving the operator preview workflow."""
    job_id = _seed_job_with_approval(env)
    with session_scope() as session:
        from app.agents.masking_orchestrator import MaskingOrchestrator

        MaskingOrchestrator(session).prepare(job_id)
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        agent._count_rows = MagicMock(return_value=1)  # type: ignore[attr-defined]

        # The dry-run path only issues a COUNT(*) via the cursor.
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        conn = MagicMock()
        conn.cursor.return_value = cursor

        import app.agents.safe_update as su

        original_connect = su.pyodbc.connect
        su.pyodbc.connect = MagicMock(return_value=conn)  # type: ignore[attr-defined]
        try:
            results = agent.apply(
                job_id,
                target_database="anonymizer_app",
                expected_backup_path=str(env["backup_dir"] / "nope.bak"),
                confirm=False,
                dry_run=True,
            )
        finally:
            su.pyodbc.connect = original_connect  # type: ignore[attr-defined]
    assert results  # completed without a backup record
