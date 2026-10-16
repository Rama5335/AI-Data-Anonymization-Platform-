"""Tests for the Approval agent and API."""

import pytest
from fastapi.testclient import TestClient

from app.agents.approval import ApprovalAgent
from app.database.session import session_scope
from app.main import app
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


def get_settings_for_tests():
    from app.core.config import get_settings

    return get_settings()


client = TestClient(app)


def _prepare(monkeypatch, tmp_path):
    db = tmp_path / "approvals.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed_classifications(monkeypatch, tmp_path):
    from app.security.passwords import hash_password

    _prepare(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="alice", password_hash=hash_password("alice-pwd-1"), is_active=True)
        u2 = User(username="bob", password_hash=hash_password("bob-pwd-1"), is_active=False)
        session.add_all([u, u2])
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
        for i, (n, dt) in enumerate(
            [("Email", "nvarchar"), ("Id", "int"), ("Phone", "nvarchar")], start=1
        ):
            session.add(
                DiscoveredColumn(
                    table_id=t.id,
                    name=n,
                    ordinal_position=i,
                    data_type=dt,
                    is_nullable=True,
                )
            )
        ids = {}
        for n, s, conf, strat, ok in [
            ("Email", "Email", 0.9, "Fake Email", True),
            ("Id", "Non Sensitive", 0.95, "Preserve Format", True),
            ("Phone", "Phone", 0.8, "Masked Phone", True),
        ]:
            r = ClassificationResult(
                job_id=j.id,
                schema_name="dbo",
                table_name="T",
                column_name=n,
                sensitivity=s,
                confidence=conf,
                reasoning="r",
                suggested_strategy=strat,
                attempts=1,
                ok=ok,
            )
            session.add(r)
            session.flush()
            ids[n] = r.id
        session.commit()
        return j.id, u.id, ids


def test_submit_approve_creates_row(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        d = agent.submit(
            classification_id=ids["Email"],
            approver_user_id=user_id,
            decision="approve",
        )
    assert d.decision == "approve"
    assert d.excluded is False


def test_submit_override_requires_strategy(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        with pytest.raises(ValueError, match="requires a non-empty masking_strategy"):
            agent.submit(
                classification_id=ids["Email"],
                approver_user_id=user_id,
                decision="override",
            )


def test_submit_rejects_inactive_user(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        # bob is the inactive user
        bob = session.query(User).filter_by(username="bob").one()
        agent = ApprovalAgent(session)
        with pytest.raises(ValueError, match="not active"):
            agent.submit(
                classification_id=ids["Email"],
                approver_user_id=bob.id,
                decision="approve",
            )


def test_pending_excludes_already_approved(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        agent.submit(classification_id=ids["Email"], approver_user_id=user_id, decision="approve")
        pending = agent.pending_for_job(job_id)
    pending_names = {r.column_name for r in pending}
    assert pending_names == {"Id", "Phone"}


def test_approve_then_override_upserts(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        d1 = agent.submit(
            classification_id=ids["Email"],
            approver_user_id=user_id,
            decision="approve",
        )
        d2 = agent.submit(
            classification_id=ids["Email"],
            approver_user_id=user_id,
            decision="override",
            masking_strategy="Hash",
            comment="changed my mind",
        )
    assert d1.id == d2.id  # same row, not duplicated
    assert d2.decision == "override"
    assert d2.masking_strategy == "Hash"
    assert d2.comment == "changed my mind"


def test_approve_endpoint_roundtrip(monkeypatch, tmp_path):
    """The approval POST now requires a JWT.

    We bypass the HTTP login (covered separately in
    ``test_auth.py``) by directly issuing a JWT for the seeded user.
    """
    from app.security.tokens import create_access_token

    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    settings = get_settings_for_tests()
    token = create_access_token(
        subject=user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    r = client.post(
        "/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "classification_id": ids["Phone"],
            "decision": "override",
            "masking_strategy": "Hash",
            "comment": "phone numbers should be hashed in this table",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "override"
    assert body["masking_strategy"] == "Hash"
    assert body["excluded"] is False
    assert body["approved_by_user_id"] == user_id

    # Pending list should now omit Phone.
    # (This route now requires authentication, so the same token is used.)
    r2 = client.get(
        f"/approvals/jobs/{job_id}/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert {it["column_name"] for it in items} == {"Id", "Email"}


def test_approve_endpoint_requires_auth(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    r = client.post(
        "/approvals",
        json={
            "classification_id": ids["Phone"],
            "decision": "approve",
        },
    )
    assert r.status_code == 401


def test_approval_rejects_invalid_decision(monkeypatch, tmp_path):
    from app.security.tokens import create_access_token

    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    settings = get_settings_for_tests()
    token = create_access_token(
        subject=user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    r = client.post(
        "/approvals",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "classification_id": "00000000-0000-0000-0000-000000000000",
            "decision": "MAYBE",
        },
    )
    assert r.status_code == 422


def test_approval_rejects_excluded_and_approve(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        with pytest.raises(ValueError, match="Cannot both approve and exclude"):
            agent.submit(
                classification_id=ids["Email"],
                approver_user_id=user_id,
                decision="approve",
                excluded=True,
            )


def test_approvals_for_job_lists_everything(monkeypatch, tmp_path):
    job_id, user_id, ids = _seed_classifications(monkeypatch, tmp_path)
    with session_scope() as session:
        agent = ApprovalAgent(session)
        agent.submit(classification_id=ids["Email"], approver_user_id=user_id, decision="approve")
        agent.submit(classification_id=ids["Phone"], approver_user_id=user_id, decision="reject")
        rows = agent.approvals_for_job(job_id)
    assert len(rows) == 2
    assert {r.classification_result_id for r in rows} == {ids["Email"], ids["Phone"]}
