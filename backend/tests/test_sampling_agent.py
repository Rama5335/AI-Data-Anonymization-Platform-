"""Unit tests for the sampling agent, the sampling API, and the sampler."""

from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.sampling import (
    DEFAULT_MAX_BYTES_PER_VALUE,
    MssqlSampler,
    _coerce_cell,
)
from app.main import app
from app.models import Base
from app.security.tokens import create_access_token

client = TestClient(app)


def _prepare_env(monkeypatch, tmp_path, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes-xx")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    from app.core import config as config_module
    from app.database import session as session_module

    config_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
    from app.database.session import get_engine

    Base.metadata.create_all(get_engine())


def _auth_token(user_id: str) -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        subject=user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _connection(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "host": "mssql.local",
        "port": 1433,
        "auth_type": "sql",
        "username": "sa",
        "password": "CHANGE_ME_SA_PASSWORD",
        "database": "appdb",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": True,
        "timeout_seconds": 5,
    }
    payload.update(overrides)
    return payload


def _bootstrap_job_with_table(monkeypatch, tmp_path) -> tuple[str, str, str, str]:
    """Create a user, server, connection, job, and table; return (job_id, table_id, server_id, user_id)."""
    from app.database.session import session_scope
    from app.models.entities import (
        Connection,
        DiscoveredColumn,
        DiscoveredTable,
        Job,
        Server,
        User,
    )

    _prepare_env(monkeypatch, tmp_path, "sampling_agent.db")
    with session_scope() as session:
        user = User(username="sampler", password_hash="!stub", is_active=True)
        session.add(user)
        session.flush()
        server = Server(name="ephemeral-discovery", host="mssql.local", port=1433, auth_type="sql")
        session.add(server)
        session.flush()
        connection = Connection(
            server_id=server.id,
            database_name="appdb",
            encrypted_connection_string="!stub",
        )
        session.add(connection)
        session.flush()
        job = Job(
            connection_id=connection.id,
            requested_by_user_id=user.id,
            status="running",
            workflow_state={},
        )
        session.add(job)
        session.flush()
        table = DiscoveredTable(
            job_id=job.id,
            schema_name="dbo",
            table_name="Customers",
            object_type="BASE TABLE",
        )
        session.add(table)
        session.flush()
        for ord_, (name, dt, length) in enumerate(
            [
                ("Id", "int", None),
                ("FullName", "nvarchar", 200),
                ("Email", "nvarchar", 255),
                ("Bio", "nvarchar", -1),  # MAX
            ],
            start=1,
        ):
            session.add(
                DiscoveredColumn(
                    table_id=table.id,
                    name=name,
                    ordinal_position=ord_,
                    data_type=dt,
                    is_nullable=True,
                    character_maximum_length=length,
                )
            )
        job_id, table_id = job.id, table.id
        session.commit()
    return job_id, table_id, server.id, user.id


def test_sampling_agent_fetches_and_caches_rows(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    from app.agents.sampling import SamplingAgent
    from app.database.session import session_scope

    job_id, table_id, _server_id, _user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)

    fake_sampler = MagicMock()
    fake_sampler.fetch_samples.return_value = [
        {
            "Id": {"value": 1, "truncated": False, "original_length": None},
            "FullName": {"value": "Ada Lovelace", "truncated": False, "original_length": 12},
            "Email": {"value": "ada@example.com", "truncated": False, "original_length": 15},
            "Bio": {
                "value": "Mathematician and writer.",
                "truncated": True,
                "original_length": 4096,
            },
        }
    ]
    fake_sampler.dispose = MagicMock()

    with session_scope() as session:
        agent = SamplingAgent(session, fake_sampler)
        result = agent.sample_table(
            job_id=job_id,
            table_id=table_id,
            row_count=5,
            max_bytes_per_value=DEFAULT_MAX_BYTES_PER_VALUE,
        )

    assert result.cached is False
    assert result.row_count == 1
    assert result.sampled_columns == ["Id", "FullName", "Email", "Bio"]
    assert result.max_bytes_per_value == DEFAULT_MAX_BYTES_PER_VALUE
    assert result.rows[0]["Bio"]["truncated"] is True
    assert result.rows[0]["Bio"]["original_length"] == 4096
    fake_sampler.fetch_samples.assert_called_once()

    # Second call is a no-op: sampler is not invoked.
    fake_sampler.reset_mock()
    with session_scope() as session:
        again = SamplingAgent(session, fake_sampler).sample_table(job_id=job_id, table_id=table_id)
    assert again.cached is True
    assert again.row_count == 1
    fake_sampler.fetch_samples.assert_not_called()


def test_sampling_agent_filters_exclusions_on_cached_call(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    from app.agents.sampling import SamplingAgent
    from app.database.session import session_scope

    job_id, table_id, _server_id, _user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)

    fake_sampler = MagicMock()
    fake_sampler.fetch_samples.return_value = [
        {
            "Id": {"value": 1, "truncated": False, "original_length": None},
            "FullName": {"value": "Ada", "truncated": False, "original_length": 3},
            "Email": {"value": "ada@example.com", "truncated": False, "original_length": 15},
        }
    ]
    fake_sampler.dispose = MagicMock()

    with session_scope() as session:
        agent = SamplingAgent(session, fake_sampler)
        first = agent.sample_table(job_id=job_id, table_id=table_id)
    assert first.cached is False
    assert set(first.sampled_columns) == {"Id", "FullName", "Email", "Bio"}

    # Second call: cache hit, but the caller excludes Email. The agent
    # filters the cached rows; the sampler is not invoked again.
    fake_sampler.reset_mock()
    with session_scope() as session:
        second = SamplingAgent(session, fake_sampler).sample_table(
            job_id=job_id, table_id=table_id, excluded_column_names=["Email"]
        )
    assert second.cached is True
    assert "Email" not in second.sampled_columns
    assert "FullName" in second.sampled_columns
    assert all("Email" not in row for row in second.rows)
    fake_sampler.fetch_samples.assert_not_called()


def test_sampling_agent_rejects_view_by_default(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    import pytest

    from app.agents.sampling import SamplingAgent
    from app.database.session import session_scope
    from app.models.entities import DiscoveredColumn, DiscoveredTable

    job_id, _table_id, _server_id, _user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)

    with session_scope() as session:
        view = DiscoveredTable(
            job_id=job_id, schema_name="dbo", table_name="v_Customers", object_type="VIEW"
        )
        session.add(view)
        session.flush()
        session.add(
            DiscoveredColumn(
                table_id=view.id,
                name="FullName",
                ordinal_position=1,
                data_type="nvarchar",
                is_nullable=True,
                character_maximum_length=200,
            )
        )
        view_id = view.id
        session.commit()

    fake_sampler = MagicMock()
    fake_sampler.fetch_samples.return_value = [
        {
            "FullName": {
                "value": "Ada Lovelace",
                "truncated": False,
                "original_length": 12,
            }
        }
    ]
    fake_sampler.dispose = MagicMock()
    with session_scope() as session:
        agent = SamplingAgent(session, fake_sampler)
        with pytest.raises(ValueError, match="views is disabled"):
            agent.sample_table(job_id=job_id, table_id=view_id)
        # Opt-in works: view check passes, sampler is invoked.
        result = agent.sample_table(job_id=job_id, table_id=view_id, include_views=True)
        assert result.cached is False
    fake_sampler.fetch_samples.assert_called_once()


def test_sampling_agent_rejects_after_excluding_all_columns(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    import pytest

    from app.agents.sampling import SamplingAgent
    from app.database.session import session_scope

    job_id, table_id, _server_id, _user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)

    fake_sampler = MagicMock()
    fake_sampler.dispose = MagicMock()
    with session_scope() as session:
        agent = SamplingAgent(session, fake_sampler)
        with pytest.raises(ValueError, match="no columns remain"):
            agent.sample_table(
                job_id=job_id,
                table_id=table_id,
                excluded_column_names=["Id", "FullName", "Email", "Bio"],
            )
    fake_sampler.fetch_samples.assert_not_called()


def test_sampling_agent_rejects_unknown_job(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock

    import pytest

    from app.agents.sampling import SamplingAgent
    from app.database.session import session_scope

    _bootstrap_job_with_table(monkeypatch, tmp_path)
    fake_sampler = MagicMock()
    with session_scope() as session:
        agent = SamplingAgent(session, fake_sampler)
        with pytest.raises(ValueError, match="does not exist"):
            agent.sample_table(
                job_id="00000000-0000-0000-0000-000000000000",
                table_id="00000000-0000-0000-0000-000000000000",
            )


def test_sampling_endpoint_returns_rows(monkeypatch, tmp_path) -> None:
    from unittest.mock import MagicMock, patch

    job_id, table_id, _server_id, user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)
    with patch("app.api.sampling.MssqlSampler") as Sampler:
        fake_sampler = MagicMock()
        fake_sampler.fetch_samples.return_value = [
            {
                "Id": {"value": 1, "truncated": False, "original_length": None},
                "FullName": {"value": "Ada Lovelace", "truncated": False, "original_length": 12},
            }
        ]
        fake_sampler.dispose = MagicMock()
        Sampler.return_value = fake_sampler

        response = client.post(
            f"/sampling/tables/{table_id}",
            headers=_auth_token(user_id),
            json={"connection": _connection(), "row_count": 5, "max_bytes_per_value": 512},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job_id"] == job_id
        assert body["table_id"] == table_id
        assert body["schema_name"] == "dbo"
        assert body["table_name"] == "Customers"
        assert body["row_count"] == 1
        assert body["cached"] is False
        assert body["sampled_columns"] == ["Id", "FullName", "Email", "Bio"]
        assert body["rows"][0]["FullName"]["value"] == "Ada Lovelace"

    # Cached re-read.
    response = client.post(
        f"/sampling/tables/{table_id}",
        headers=_auth_token(user_id),
        json={"connection": _connection(), "row_count": 5, "max_bytes_per_value": 512},
    )
    assert response.status_code == 200
    assert response.json()["cached"] is True


def test_sampling_endpoint_rejects_unknown_table(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "unknown_table.db")
    from app.database.session import session_scope
    from app.models.entities import User

    with session_scope() as session:
        user = User(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id
    response = client.post(
        "/sampling/tables/00000000-0000-0000-0000-000000000000",
        headers=_auth_token(user_id),
        json={"connection": _connection(), "row_count": 5, "max_bytes_per_value": 512},
    )
    assert response.status_code == 404


def test_sampling_endpoint_rejects_sql_auth_without_password(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "no_password_sampling.db")
    from app.database.session import session_scope
    from app.models.entities import User

    with session_scope() as session:
        user = User(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id
    response = client.post(
        "/sampling/tables/00000000-0000-0000-0000-000000000000",
        headers=_auth_token(user_id),
        json={"connection": _connection(password=None), "row_count": 5},
    )
    assert response.status_code == 422


def test_sampling_endpoint_rejects_zero_row_count(monkeypatch, tmp_path) -> None:
    _, table_id, _server_id, user_id = _bootstrap_job_with_table(monkeypatch, tmp_path)
    response = client.post(
        f"/sampling/tables/{table_id}",
        headers=_auth_token(user_id),
        json={"connection": _connection(), "row_count": 0},
    )
    assert response.status_code == 422


def test_coerce_cell_handles_string_under_cap() -> None:
    cell = _coerce_cell("hello", "nvarchar", 16, original_length=5)
    assert cell == {"value": "hello", "truncated": False, "original_length": 5}


def test_coerce_cell_truncates_string_over_cap() -> None:
    """The sampler relies on SQL CAST for truncation; _coerce_cell just flags it.

    The value passed in is what the (truncated) driver returned, so the
    cell's ``value`` matches that — but the ``truncated`` flag and
    ``original_length`` correctly indicate the cell came from a larger
    source.
    """
    cell = _coerce_cell("x" * 1024, "nvarchar", 10, original_length=1024)
    assert cell["truncated"] is True
    assert cell["original_length"] == 1024
    assert cell["value"] == "x" * 1024  # SQL already trimmed; we echo it as-is.


def test_coerce_cell_flags_truncation_from_orig_when_value_is_trimmed() -> None:
    """Even if the value passed in is short, original_length drives the flag."""
    cell = _coerce_cell("x" * 5, "nvarchar", 10, original_length=2048)
    assert cell["truncated"] is True
    assert cell["original_length"] == 2048


def test_coerce_cell_handles_binary() -> None:
    cell = _coerce_cell(b"\x00\x01\x02", "varbinary", 16, original_length=3)
    assert cell == {
        "value": "000102",
        "truncated": False,
        "original_length": 3,
    }
    over = _coerce_cell(bytes(range(8)), "varbinary", 8, original_length=64)
    assert over["truncated"] is True
    assert over["original_length"] == 64
    assert len(bytes.fromhex(over["value"])) == 8


def test_coerce_cell_handles_none_and_int_and_datetime() -> None:
    assert _coerce_cell(None, "nvarchar", 16, original_length=None) == {
        "value": None,
        "truncated": False,
        "original_length": None,
    }
    assert _coerce_cell(42, "int", 16, original_length=None) == {
        "value": 42,
        "truncated": False,
        "original_length": None,
    }
    from datetime import datetime, timezone

    dt = datetime(2026, 7, 23, 10, 30, 0, tzinfo=timezone.utc)
    assert _coerce_cell(dt, "datetime2", 16, original_length=None) == {
        "value": dt.isoformat(),
        "truncated": False,
        "original_length": None,
    }


def test_sampler_builds_nolock_and_top_query(monkeypatch, tmp_path) -> None:
    """Black-box: confirm SELECT TOP @n ... WITH (NOLOCK) is the issued query."""
    captured: dict[str, Any] = {}

    class _FakeResult:
        def keys(self) -> list[str]:
            return ["Id", "Email"]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [(1, "ada@example.com")]

    class _FakeConn:
        def __init__(self) -> None:
            pass

        def execute(self, query, params):
            captured["query"] = str(query)
            captured["params"] = dict(params)
            return _FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = _FakeConn()
    monkeypatch.setattr("app.database.sampling.create_engine", lambda *a, **kw: fake_engine)

    from app.database.sampling import ColumnSpec, MssqlSampler

    sampler = MssqlSampler("mssql+pyodbc://placeholder", timeout_seconds=5)
    rows = sampler.fetch_samples(
        schema_name="dbo",
        table_name="Customers",
        columns=[
            ColumnSpec("Id", "int", None),
            ColumnSpec("Email", "nvarchar", 255),
        ],
        row_count=7,
        max_bytes_per_value=512,
    )

    assert captured["params"] == {"n": 7}
    assert "TOP (:n)" in captured["query"]
    assert "WITH (NOLOCK)" in captured["query"]
    assert "[dbo].[Customers]" in captured["query"]
    assert rows[0]["Email"]["value"] == "ada@example.com"


def test_sampler_truncates_max_columns_in_sql(monkeypatch, tmp_path) -> None:
    """MAX columns are wrapped in a CAST(CAST AS NVARCHAR(N)) at the SQL layer."""
    captured: dict[str, Any] = {}

    class _FakeResult:
        def keys(self) -> list[str]:
            return ["Id", "Bio", "Bio__orig"]

        def fetchall(self) -> list[tuple[Any, ...]]:
            return [(1, "short bio", 9)]

    class _FakeConn:
        def execute(self, query, params):
            captured["query"] = str(query)
            return _FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = _FakeConn()
    monkeypatch.setattr("app.database.sampling.create_engine", lambda *a, **kw: fake_engine)

    from app.database.sampling import ColumnSpec, MssqlSampler

    sampler = MssqlSampler("mssql+pyodbc://placeholder", timeout_seconds=5)
    rows = sampler.fetch_samples(
        schema_name="dbo",
        table_name="Customers",
        columns=[
            ColumnSpec("Id", "int", None),
            ColumnSpec("Bio", "nvarchar", -1),  # MAX
        ],
        row_count=1,
        max_bytes_per_value=128,
    )
    assert "CAST(CAST([Bio] AS NVARCHAR(MAX)) AS NVARCHAR(64))" in captured["query"]
    assert "LEN([Bio]) AS [Bio__orig]" in captured["query"]
    assert "[Id]" in captured["query"]
    # Bio__orig is folded into the cell as original_length and hidden from
    # the caller; Bio is exposed with the right shape.
    assert "Bio" in rows[0]
    assert "Bio__orig" not in rows[0]
    assert rows[0]["Bio"]["original_length"] == 9
    assert rows[0]["Bio"]["truncated"] is False  # 9 < 128


def test_sampler_rejects_invalid_arguments() -> None:
    sampler = MssqlSampler("mssql+pyodbc://placeholder", timeout_seconds=5)
    import pytest

    with pytest.raises(ValueError):
        sampler.fetch_samples("dbo", "T", columns=[], row_count=5)
    with pytest.raises(ValueError):
        sampler.fetch_samples(
            "dbo",
            "T",
            columns=[
                __import__("app.database.sampling", fromlist=["ColumnSpec"]).ColumnSpec(
                    "C", "int", None
                )
            ],
            row_count=0,
        )
    with pytest.raises(ValueError):
        sampler.fetch_samples(
            "dbo",
            "T",
            columns=[
                __import__("app.database.sampling", fromlist=["ColumnSpec"]).ColumnSpec(
                    "C", "int", None
                )
            ],
            row_count=10_000,
        )
