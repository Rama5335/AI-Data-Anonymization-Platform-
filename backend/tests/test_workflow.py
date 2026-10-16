"""Tests for the workflow orchestrator (Phase 13)."""

import pytest

from app.agents.workflow import (
    Phase,
    WorkflowError,
    WorkflowOrchestrator,
    allowed_transitions,
)
from app.database.session import session_scope
from app.models import Base
from app.models.entities import Connection, Job, Server, User


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "wf.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed_job(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="w", password_hash="!x", is_active=True)
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
        job_id = j.id
        session.commit()
        return job_id


def test_legal_transitions_allowed():
    transitions = allowed_transitions()
    assert Phase.PENDING in transitions
    assert Phase.WAITING_FOR_APPROVAL in transitions
    # Approval is the one "pause": you can only enter it from
    # CLASSIFY and exit it to READY_FOR_BACKUP or FAILED.
    assert Phase.READY_FOR_BACKUP in transitions[Phase.WAITING_FOR_APPROVAL]


def test_terminal_states_have_no_outgoing():
    transitions = allowed_transitions()
    assert transitions[Phase.COMPLETED] == set()
    assert transitions[Phase.FAILED] == set()


def test_illegal_transition_raises(monkeypatch, tmp_path):
    job_id = _seed_job(monkeypatch, tmp_path)
    with session_scope() as session:
        orch = WorkflowOrchestrator(session)
        with pytest.raises(WorkflowError, match="Illegal transition"):
            # Skip from PENDING straight to UPDATE.
            orch.transition(job_id, Phase.UPDATE)


def test_full_happy_path(monkeypatch, tmp_path):
    job_id = _seed_job(monkeypatch, tmp_path)
    with session_scope() as session:
        orch = WorkflowOrchestrator(session)
        for phase in [
            Phase.PENDING,  # initial
            Phase.DISCOVERY,
            Phase.SCHEMA,
            Phase.SAMPLE,
            Phase.CLASSIFY,
            Phase.WAITING_FOR_APPROVAL,
            Phase.READY_FOR_BACKUP,
            Phase.BACKUP,
            Phase.MASK,
            Phase.UPDATE,
            Phase.AUDIT,
            Phase.COMPLETED,
        ][
            1:
        ]:  # skip PENDING; we don't transition INTO pending
            st = orch.transition(job_id, phase)
            assert st.phase is phase
    with session_scope() as session:
        from app.models.entities import Job as _J

        job = session.get(_J, job_id)
        assert job.status == "completed"
        assert job.workflow_state["phase"] == "completed"


def test_stale_detection(monkeypatch, tmp_path):
    job_id = _seed_job(monkeypatch, tmp_path)
    with session_scope() as session:
        orch = WorkflowOrchestrator(session)
        orch.transition(job_id, Phase.DISCOVERY)
        orch.transition(job_id, Phase.SCHEMA)
        orch.transition(job_id, Phase.SAMPLE)
        orch.transition(job_id, Phase.CLASSIFY)
        orch.transition(job_id, Phase.WAITING_FOR_APPROVAL)
    with session_scope() as session:
        # Force the workflow to look stale.
        from app.models.entities import Job as _J
        from datetime import datetime, timedelta, timezone

        job = session.get(_J, job_id)
        job.workflow_state = {
            "phase": "waiting_for_approval",
            "updated_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
        }
        session.commit()
    with session_scope() as session:
        st = WorkflowOrchestrator(session).current(job_id)
    assert st.is_stale is True
    assert st.phase is Phase.WAITING_FOR_APPROVAL


def test_unknown_phase_404(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        with pytest.raises(WorkflowError, match="not found"):
            WorkflowOrchestrator(session).current("00000000-0000-0000-0000-000000000000")
