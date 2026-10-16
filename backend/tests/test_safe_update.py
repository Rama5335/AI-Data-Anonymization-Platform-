"""Tests for the Safe Update agent (Phase 11) and Audit agent (Phase 12)."""

from unittest.mock import MagicMock

import pytest

from app.agents.audit import AuditAgent
from app.agents.masking_orchestrator import MaskingOrchestrator
from app.agents.safe_update import (
    SafeUpdateAgent,
    UpdateError,
    _q,
)
from app.database.session import session_scope
from app.models import Base
from app.models.entities import (
    ClassificationResult,
    Connection,
    DiscoveredColumn,
    DiscoveredTable,
    Job,
    Server,
    User,
)


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "upd.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="u", password_hash="!x", is_active=True)
        session.add(u)
        session.flush()
        sv = Server(name="s", host="h", port=1, auth_type="sql")
        session.add(sv)
        session.flush()
        c = Connection(server_id=sv.id, database_name="d", encrypted_connection_string="!x")
        session.add(c)
        session.flush()
        j = Job(connection_id=c.id, requested_by_user_id=u.id, status="running", workflow_state={})
        session.add(j)
        session.flush()
        t = DiscoveredTable(
            job_id=j.id, schema_name="dbo", table_name="T", object_type="BASE TABLE"
        )
        session.add(t)
        session.flush()
        for i, n in enumerate(["Id", "Email"], start=1):
            session.add(
                DiscoveredColumn(
                    table_id=t.id,
                    name=n,
                    ordinal_position=i,
                    data_type="nvarchar" if n == "Email" else "int",
                    is_nullable=True,
                    is_primary_key=(n == "Id"),
                )
            )
        cls = ClassificationResult(
            job_id=j.id,
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
                approved_by_user_id=u.id,
                decision="override",
                masking_strategy="Hash",
            )
        )
        session.commit()
        return j.id


# ---------- helpers ----------


def test_q_brackets_simple_identifier():
    assert _q("Customers") == "[Customers]"


def test_q_rejects_unsafe_identifier():
    with pytest.raises(UpdateError, match="unsafe identifier"):
        _q("Customers; DROP TABLE x")


# ---------- agent safety gates ----------


def test_apply_requires_confirm(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        with pytest.raises(UpdateError, match="confirm=True"):
            agent.apply(
                job_id,
                target_database="d",
                expected_backup_path=str(tmp_path / "fake.bak"),
                confirm=False,
            )


def test_apply_requires_expected_backup_path(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        with pytest.raises(UpdateError, match="expected_backup_path"):
            agent.apply(
                job_id,
                target_database="d",
                expected_backup_path="",
                confirm=True,
            )


def test_plan_with_no_approvals_fails(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="x", password_hash="!x", is_active=True)
        session.add(u)
        session.flush()
        sv = Server(name="s", host="h", port=1, auth_type="sql")
        session.add(sv)
        session.flush()
        c = Connection(server_id=sv.id, database_name="d", encrypted_connection_string="!x")
        session.add(c)
        session.flush()
        j = Job(connection_id=c.id, requested_by_user_id=u.id, status="running", workflow_state={})
        session.add(j)
        session.flush()
        # Prepare a masking job but DO NOT add any approvals.
        MaskingOrchestrator(session).prepare(j.id)
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        with pytest.raises(UpdateError, match="No approved columns"):
            agent.plan(j.id, target_database="d")


# ---------- dry-run path ----------


def test_apply_dry_run_does_not_execute(monkeypatch, tmp_path):
    """Dry-run must not write to the database or wipe the masking job."""
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        MaskingOrchestrator(session).prepare(job_id)
        # The plan() path tries to call _count_rows via pyodbc, which we
        # don't have. Stub it.
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        agent._count_rows = MagicMock(return_value=5)  # type: ignore[attr-defined]
        plans = agent.plan(job_id, target_database="d", batch_size=2)
    assert plans
    # In dry-run mode the apply should not raise even without a real
    # MSSQL connection; we short-circuit before opening one.
    # Actually, _masked_update_batches opens pyodbc. Patch the open
    # call so dry-run completes without a real DB.
    with session_scope() as session:
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        agent._count_rows = MagicMock(return_value=3)  # type: ignore[attr-defined]
        # Patch pyodbc.connect inside the agent's apply call by
        # monkey-patching at module level.
        import app.agents.safe_update as su

        su.pyodbc.connect = MagicMock()  # type: ignore[attr-defined]
        try:
            results = agent.apply(
                job_id,
                target_database="d",
                expected_backup_path=str(tmp_path / "fake.bak"),
                confirm=False,
                dry_run=True,
            )
        finally:
            # Restore by re-importing.
            import importlib

            importlib.reload(su)
    assert results
    # The masking job should NOT be wiped (dry-run is reversible).
    with session_scope() as session:
        from app.models.entities import MaskingJob

        mj = session.execute(
            __import__("sqlalchemy").select(MaskingJob).where(MaskingJob.job_id == job_id)
        ).scalar_one()
        assert mj.wiped_at is None


# ---------- audit agent ----------


def test_audit_write_appends_row(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        AuditAgent(session).write(
            event_type="test.event",
            outcome="ok",
            details={"k": "v"},
        )
        rows = AuditAgent(session).all()
    assert len(rows) == 1
    assert rows[0].event_type == "test.event"
    assert rows[0].outcome == "ok"
    assert rows[0].details == {"k": "v"}


def test_audit_for_job_filters(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="a", password_hash="!x", is_active=True)
        session.add(u)
        session.flush()
        sv = Server(name="s", host="h", port=1, auth_type="sql")
        session.add(sv)
        session.flush()
        c = Connection(server_id=sv.id, database_name="d", encrypted_connection_string="!x")
        session.add(c)
        session.flush()
        j1 = Job(connection_id=c.id, requested_by_user_id=u.id, status="running", workflow_state={})
        j2 = Job(connection_id=c.id, requested_by_user_id=u.id, status="running", workflow_state={})
        session.add_all([j1, j2])
        session.flush()
        audit = AuditAgent(session)
        audit.write(event_type="e1", outcome="ok", job_id=j1.id, details={"n": 1})
        audit.write(event_type="e2", outcome="ok", job_id=j2.id, details={"n": 2})
        audit.write(event_type="e3", outcome="ok", job_id=j1.id, details={"n": 3})
        rows = audit.for_job(j1.id)
    assert len(rows) == 2
    assert {r.event_type for r in rows} == {"e1", "e3"}
