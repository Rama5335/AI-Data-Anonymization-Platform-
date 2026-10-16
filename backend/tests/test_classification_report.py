"""Tests for the classification report builder."""

from app.agents.report import build_job_report, render_markdown, report_to_dict
from app.database.session import session_scope
from app.models.entities import (
    ClassificationResult,
    Connection,
    DiscoveredColumn,
    DiscoveredTable,
    Job,
    Server,
    User,
)
from app.models import Base


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "report.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=")
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        u = User(username="r", password_hash="!x", is_active=True)
        session.add(u)
        session.flush()
        sv = Server(name="ephemeral-discovery", host="h", port=1, auth_type="sql")
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
        # Two classified, one not.
        session.add(
            ClassificationResult(
                job_id=j.id,
                schema_name="dbo",
                table_name="T",
                column_name="Email",
                sensitivity="Email",
                confidence=0.9,
                reasoning="looks like email",
                suggested_strategy="Fake Email",
                attempts=1,
                ok=True,
            )
        )
        session.add(
            ClassificationResult(
                job_id=j.id,
                schema_name="dbo",
                table_name="T",
                column_name="Id",
                sensitivity="Non Sensitive",
                confidence=0.95,
                reasoning="integer id",
                suggested_strategy="Preserve Format",
                attempts=1,
                ok=True,
            )
        )
        session.commit()
        return j.id


def test_report_aggregates_sensitivity_counts(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        report = build_job_report(session, job_id)
    assert report.total_columns == 3
    assert report.sensitive_columns == 1  # Email
    assert report.failed_columns == 0
    assert report.sensitivity_counts == {"Email": 1, "Non Sensitive": 1}
    assert report.strategy_counts == {"Fake Email": 1, "Preserve Format": 1}


def test_report_marks_unclassified_as_unknown(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        report = build_job_report(session, job_id)
    phone = next(c for c in report.tables[0].columns if c.column_name == "Phone")
    assert phone.sensitivity == "Unknown"
    assert phone.ok is False
    assert phone.error == "not classified"


def test_report_to_dict_shape(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        report = build_job_report(session, job_id)
    d = report_to_dict(report)
    assert d["job_id"] == job_id
    assert d["totals"]["tables"] == 1
    assert d["totals"]["columns"] == 3
    assert d["totals"]["sensitive_columns"] == 1
    assert d["totals"]["failed_columns"] == 0
    assert d["totals"]["pending_columns"] == 1
    assert "Email" in d["sensitivity_counts"]


def test_report_markdown_renders(monkeypatch, tmp_path):
    job_id = _seed(monkeypatch, tmp_path)
    with session_scope() as session:
        report = build_job_report(session, job_id)
    md = render_markdown(report)
    assert "Classification report" in md
    assert "Email" in md
    assert "Fake Email" in md
    assert "sensitivity" in md.lower()


def test_report_empty_job(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        report = build_job_report(session, "00000000-0000-0000-0000-000000000000")
    assert report.tables == []
    assert report.total_columns == 0
