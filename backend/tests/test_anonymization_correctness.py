"""Anonymization-correctness tests.

The security-hardening phase requires that:

* excluded / rejected / unapproved columns NEVER appear in the update
  plan or the masking column-spec map — they cannot be modified;
* the generated UPDATE SQL is always parameterized (no string-built
  values) and only touches bracket-quoted identifiers;
* the post-execution batch validation detects a row that did not
  receive its expected masked value and rolls the transaction back.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.masking_orchestrator import MaskingOrchestrator
from app.agents.safe_update import SafeUpdateAgent, UpdateError, _masked_update_batches, _q
from app.database.session import session_scope
from app.models import Base
from app.models.entities import (
    Approval,
    ClassificationResult,
    Connection,
    DiscoveredColumn,
    DiscoveredTable,
    Job,
    Server,
    User,
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    db = tmp_path / "correctness.db"
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


def _seed(env, *, approvals: list[dict]) -> str:
    """Create a job with columns ``Id``/``Email``/``Phone``/``Notes`` and
    one classification per entry in ``approvals``.

    Each ``approvals`` entry: ``{"column": str, "decision": str,
    "excluded": bool, "strategy": str}``. Columns without an entry are
    discovered + classified but never approved.
    """
    with session_scope() as session:
        user = User(username="u", password_hash=hash_pw())
        session.add(user)
        session.flush()
        server = Server(name="s", host="h", port=1433, auth_type="sql")
        session.add(server)
        session.flush()
        conn = Connection(server_id=server.id, database_name="d", encrypted_connection_string="!x")
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
        for i, (name, dtype) in enumerate(
            [("Id", "int"), ("Email", "nvarchar"), ("Phone", "nvarchar"), ("Notes", "nvarchar")],
            start=1,
        ):
            session.add(
                DiscoveredColumn(
                    table_id=table.id,
                    name=name,
                    ordinal_position=i,
                    data_type=dtype,
                    is_nullable=(name != "Id"),
                    is_primary_key=(name == "Id"),
                )
            )
        for entry in approvals:
            cls = ClassificationResult(
                job_id=job.id,
                schema_name="dbo",
                table_name="T",
                column_name=entry["column"],
                sensitivity=entry["column"],
                confidence=0.9,
                reasoning="r",
                suggested_strategy=entry.get("strategy", "Hash"),
                attempts=1,
                ok=True,
            )
            session.add(cls)
            session.flush()
            session.add(
                Approval(
                    classification_result_id=cls.id,
                    approved_by_user_id=user.id,
                    decision=entry.get("decision", "approve"),
                    masking_strategy=entry.get("strategy", "Hash"),
                    excluded=entry.get("excluded", False),
                )
            )
        session.commit()
        # Materialize the masking job (salt/token key) so load() works.
        MaskingOrchestrator(session).prepare(job.id)
        return job.id


def hash_pw() -> str:
    from app.security.passwords import hash_password

    return hash_password("u-pw-12345678")


def _specs(env, job_id: str) -> dict:
    with session_scope() as session:
        ctx = MaskingOrchestrator(session).load(job_id)
        return dict(ctx._column_specs)  # type: ignore[attr-defined]


def _plans(env, job_id: str):
    with session_scope() as session:
        agent = SafeUpdateAgent(session, "mssql+pyodbc://placeholder")
        agent._count_rows = MagicMock(return_value=3)  # type: ignore[attr-defined]
        return agent.plan(job_id, target_database="d", batch_size=2)


# ---------------------------------------------------------------------------
# excluded / rejected / unapproved columns never enter the pipeline
# ---------------------------------------------------------------------------


def test_excluded_column_not_in_specs_or_plan(env):
    job_id = _seed(
        env,
        approvals=[
            {"column": "Email", "decision": "approve"},
            {"column": "Phone", "decision": "approve", "excluded": True},
        ],
    )
    specs = _specs(env, job_id)
    assert ("T", "Email") in specs
    assert ("T", "Phone") not in specs

    plans = _plans(env, job_id)
    assert [(p.table, p.column) for p in plans] == [("T", "Email")]


def test_rejected_column_not_in_specs_or_plan(env):
    job_id = _seed(
        env,
        approvals=[
            {"column": "Email", "decision": "approve"},
            {"column": "Phone", "decision": "reject"},
        ],
    )
    specs = _specs(env, job_id)
    assert ("T", "Phone") not in specs

    plans = _plans(env, job_id)
    assert ("T", "Phone") not in {(p.table, p.column) for p in plans}


def test_unapproved_column_not_in_specs_or_plan(env):
    """Notes is classified (if included) but has no approval at all."""
    job_id = _seed(env, approvals=[{"column": "Email", "decision": "approve"}])
    specs = _specs(env, job_id)
    assert set(specs) == {("T", "Email")}

    plans = _plans(env, job_id)
    assert {(p.table, p.column) for p in plans} == {("T", "Email")}


def test_mask_returns_original_for_unapproved_column(env):
    """The orchestrator is defense-in-depth: even if asked to mask an
    unapproved column, the value comes back unchanged."""
    job_id = _seed(env, approvals=[{"column": "Email", "decision": "approve"}])
    with session_scope() as session:
        orch = MaskingOrchestrator(session)
        ctx = orch.load(job_id)
        assert orch.mask(ctx, "T", "Notes", "sensitive note") == "sensitive note"
        assert orch.mask(ctx, "T", "Email", "a@b.com") != "a@b.com"


# ---------------------------------------------------------------------------
# generated SQL is parameterized and identifier-safe
# ---------------------------------------------------------------------------


def test_plan_sql_is_parameterized(env):
    job_id = _seed(env, approvals=[{"column": "Email", "decision": "approve"}])
    plans = _plans(env, job_id)
    assert len(plans) == 1
    sql = plans[0].dry_run_sql
    # The value placeholder is a bound parameter, never an inline literal.
    assert "SET [Email] = ?" in sql
    assert "UPDATE TOP (2) [T]" in sql
    assert "-- DRY RUN" in sql


def test_plan_rejects_unsafe_identifiers(env):
    """A table name that fails the identifier check can never produce SQL."""
    job_id = _seed(env, approvals=[{"column": "Email", "decision": "approve"}])
    with session_scope() as session:
        cls = session.query(ClassificationResult).filter_by(job_id=job_id).one()
        cls.table_name = "T; DROP TABLE Users"
        session.commit()
    with pytest.raises(UpdateError, match="unsafe identifier"):
        _plans(env, job_id)


def test_q_rejects_closing_bracket():
    """``]`` fails the identifier safety check, so the bracket-escaping
    branch is defense-in-depth rather than the primary guard."""
    with pytest.raises(UpdateError, match="unsafe identifier"):
        _q("Weird]Name")


# ---------------------------------------------------------------------------
# post-execution validation rolls back on mismatch
# ---------------------------------------------------------------------------


class _ValidationCursor:
    """Scripted cursor for ``_masked_update_batches``.

    * PK discovery returns ``Id``.
    * The row stream returns ``rows`` (pk, original).
    * UPDATEs are recorded and, when ``effective`` is True, the
      re-read (IN-list) returns the masked value; otherwise the re-read
      returns the ORIGINAL value (simulating a server that did not
      apply the update).
    """

    def __init__(self, rows, *, effective: bool):
        self._rows = rows
        self._effective = effective
        self.updates: list[tuple] = []
        self._last: list[tuple] = []
        self._mode = None

    def execute(self, sql, params=None):
        if "KEY_COLUMN_USAGE" in sql:
            self._mode = "pk"
            self._last = [("Id",)]
        elif "WHERE [Email] IS NOT NULL" in sql:
            self._mode = "stream"
            self._last = list(self._rows)
        elif sql.startswith("UPDATE "):
            self._mode = "update"
            self.updates.append(params)
            self._last = []
        elif " IN (" in sql:
            self._mode = "validate"
            if self._effective:
                # Return the masked values that were written. Updates are
                # stored as (masked_value, pk).
                self._last = [(pk, masked) for (masked, pk) in self.updates]
            else:
                # The update silently did nothing: originals still there.
                self._last = list(self._rows)
        else:  # pragma: no cover - unexpected SQL
            self._mode = "other"
            self._last = []

    def fetchall(self):
        rows, self._last = self._last, []
        return rows

    def fetchmany(self, n):
        rows, self._last = self._last[:n], self._last[n:]
        return rows

    def fetchone(self):
        rows, self._last = self._last, []
        return rows[0] if rows else None

    def close(self):
        pass


def _run_batches(cursor, *, effective: bool, dry_run: bool = False):
    with session_scope() as session:
        job_id = _seed(env=None, approvals=[{"column": "Email", "decision": "approve"}])
        orch = MaskingOrchestrator(session)
        ctx = orch.load(job_id)
        spec = ctx._column_specs[("T", "Email")]  # type: ignore[attr-defined]
        conn = MagicMock()
        # PK discovery calls ``conn.cursor()``; route it to the same
        # scripted cursor so the flow stays deterministic.
        conn.cursor.return_value = cursor
        return _masked_update_batches(
            conn=conn,
            cursor=cursor,
            spec=spec,
            ctx=ctx,
            orch=orch,
            table="T",
            column="Email",
            sql_template="UPDATE [T] SET [Email] = ? WHERE [Email] IS NOT NULL",
            batch_size=500,
            dry_run=dry_run,
        )


def test_validation_passes_when_masked_value_is_stored(env):
    cursor = _ValidationCursor([(1, "alice@example.com")], effective=True)
    rows_updated, batches = _run_batches(cursor, effective=True)
    assert rows_updated == 1
    assert batches == 1
    # Exactly one parameterized UPDATE was issued for the one row.
    assert len(cursor.updates) == 1
    masked_value, pk = cursor.updates[0]
    assert pk == 1
    assert masked_value != "alice@example.com"


def test_validation_failure_raises_update_error(env):
    """If the stored value still equals the original (update had no
    effect), validation must fail so the caller rolls back."""
    cursor = _ValidationCursor([(1, "alice@example.com")], effective=False)
    with pytest.raises(UpdateError, match="validation failed"):
        _run_batches(cursor, effective=False)


def test_validation_accepts_server_type_coercion(env):
    """A stored value that differs from the original but also from the
    exact masked value (e.g. decimal rounding) must NOT fail."""

    class _CoercingCursor(_ValidationCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if self._mode == "validate":
                # Server returned a coerced variant of the masked value.
                self._last = [(pk, f"{masked}-coerced") for (masked, pk) in self.updates]

    cursor = _CoercingCursor([(1, "alice@example.com")], effective=True)
    rows_updated, batches = _run_batches(cursor, effective=True)
    assert rows_updated == 1
    assert batches == 1


def test_dry_run_batches_never_write(env):
    cursor = _ValidationCursor([(1, "alice@example.com")], effective=False)
    rows_updated, batches = _run_batches(cursor, effective=False, dry_run=True)
    assert rows_updated == 1  # counted, not written
    assert batches == 0
    assert cursor.updates == []
