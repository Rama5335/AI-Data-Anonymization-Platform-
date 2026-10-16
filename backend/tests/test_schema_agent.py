"""Unit tests for the schema agent and the schema API."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.schema import SchemaAgent
from app.core.config import get_settings
from app.database.schema_reader import (
    ColumnInfo,
    ConstraintInfo,
    ForeignKeyInfo,
    IndexInfo,
)
from app.main import app
from app.models import Base
from app.security.tokens import create_access_token

client = TestClient(app)


@dataclass
class _StubObject:
    schema_name: str
    name: str
    object_type: str = "BASE TABLE"


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


def test_schema_discover_creates_persisted_rows(tmp_path, monkeypatch) -> None:
    """A real SQLite engine is wired so the round-trip persists and reads back."""
    _prepare_env(monkeypatch, tmp_path, "schema_agent.db")

    # Bootstrap schema in the SQLite DB.
    from app.models import entities  # noqa: F401 - ensure models are registered

    # Stub the catalog queries.
    fake_discovery = MagicMock()
    fake_discovery.list_objects.side_effect = [
        [_StubObject("dbo", "Customers")],
        [],  # views
    ]

    fake_reader = MagicMock()
    fake_reader.read_columns.return_value = [
        ColumnInfo("Id", 1, "int", False, None, None, 10, 0),
        ColumnInfo("Name", 2, "nvarchar", True, None, 100, None, None),
        ColumnInfo("Email", 3, "nvarchar", True, None, 255, None, None),
    ]
    fake_reader.read_primary_key_columns.return_value = ["Id"]
    fake_reader.read_foreign_keys.return_value = []
    fake_reader.read_indexes.return_value = [
        IndexInfo("PK_Customers", True, True, ["Id"]),
        IndexInfo("IX_Customers_Email", False, False, ["Email"]),
    ]
    fake_reader.read_constraints.return_value = [
        ConstraintInfo("UQ_Customers_Email", "UNIQUE"),
    ]

    # Need a job to exist first.
    from app.database.session import session_scope
    from app.models.entities import Connection, Job, Server, User

    with session_scope() as session:
        user = User(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.flush()
        server = Server(name="s1", host="mssql.local", port=1433, auth_type="sql")
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
        job_id = job.id
        session.commit()

    from app.database.session import session_scope as open_session

    with open_session() as session:
        agent = SchemaAgent(session, fake_discovery, fake_reader)
        result = agent.discover(job_id, "appdb")

    assert result.cached is False
    assert result.counts.tables == 1
    assert result.counts.columns == 3
    assert result.counts.indexes == 2
    assert result.counts.constraints == 1

    # Cached second call.
    fake_discovery.list_objects.reset_mock()
    fake_reader.reset_mock()
    with open_session() as session:
        agent2 = SchemaAgent(session, fake_discovery, fake_reader)
        again = agent2.discover(job_id, "appdb")
    assert again.cached is True
    assert again.counts.tables == 1
    assert again.counts.columns == 3
    fake_discovery.list_objects.assert_not_called()  # no catalog re-read


def test_schema_discover_rejects_missing_job(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "missing_job.db")

    fake_discovery = MagicMock()
    fake_reader = MagicMock()
    from app.database.session import session_scope
    from app.models.entities import Connection, Job, Server, User

    with session_scope() as session:
        user = User(username="u", password_hash="!x", is_active=True)
        session.add(user)
        session.flush()
        server = Server(name="s", host="h", port=1, auth_type="sql")
        session.add(server)
        session.flush()
        connection = Connection(
            server_id=server.id, database_name="d", encrypted_connection_string="!x"
        )
        session.add(connection)
        session.flush()
        job = Job(
            connection_id=connection.id,
            requested_by_user_id=user.id,
            status="r",
            workflow_state={},
        )
        session.add(job)
        session.flush()
        existing_id = job.id
        session.commit()

    with session_scope() as session:
        agent = SchemaAgent(session, fake_discovery, fake_reader)
        import pytest

        with pytest.raises(ValueError):
            agent.discover("00000000-0000-0000-0000-000000000000", "d")
        # real job works
        result = agent.discover(existing_id, "d")
        assert result.cached is False
        assert result.counts.tables == 0


def test_schema_discover_endpoint_returns_counts(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "endpoint.db")

    from app.database.session import session_scope
    from app.models.entities import User as UserModel

    with session_scope() as session:
        user = UserModel(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id

    with (
        patch("app.api.schema.MssqlDiscoveryClient") as Discovery,
        patch("app.api.schema.MssqlSchemaReader") as Reader,
    ):
        fake_discovery = MagicMock()
        fake_discovery.list_objects.side_effect = [
            [_StubObject("dbo", "Orders")],
            [],
        ]
        fake_discovery.dispose = MagicMock()
        Discovery.return_value = fake_discovery

        fake_reader = MagicMock()
        fake_reader.read_columns.return_value = [
            ColumnInfo("Id", 1, "int", False, None, None, 10, 0),
            ColumnInfo("CustomerId", 2, "int", False, None, None, 10, 0),
            ColumnInfo("Total", 3, "decimal", True, None, None, 18, 2),
        ]
        fake_reader.read_primary_key_columns.return_value = ["Id"]
        fake_reader.read_foreign_keys.return_value = [
            ForeignKeyInfo(
                fk_name="FK_Orders_CustomerId",
                column_name="CustomerId",
                referenced_schema="dbo",
                referenced_table="Customers",
                referenced_column="Id",
            )
        ]
        fake_reader.read_indexes.return_value = [IndexInfo("PK_Orders", True, True, ["Id"])]
        fake_reader.read_constraints.return_value = []
        fake_reader.dispose = MagicMock()
        Reader.return_value = fake_reader

        response = client.post(
            "/schema/discover",
            headers=_auth_token(user_id),
            json={"connection": _connection(database="appdb")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["table_count"] == 1
        assert body["column_count"] == 3
        assert body["foreign_key_count"] == 1
        assert body["index_count"] == 1
        assert body["constraint_count"] == 0
        assert body["cached"] is False
        job_id = body["job_id"]

    response = client.get(f"/schema/job/{job_id}", headers=_auth_token(user_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == job_id
    assert len(body["tables"]) == 1
    table = body["tables"][0]
    assert table["schema_name"] == "dbo"
    assert table["table_name"] == "Orders"
    assert {c["name"] for c in table["columns"]} == {"Id", "CustomerId", "Total"}
    assert any(c["is_primary_key"] for c in table["columns"])
    assert any(c["is_foreign_key"] for c in table["columns"])
    assert len(table["foreign_keys"]) == 1
    assert table["foreign_keys"][0]["column_name"] == "CustomerId"
    assert table["indexes"][0]["name"] == "PK_Orders"


def test_schema_discover_rejects_sql_auth_without_password(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "no_password.db")
    from app.database.session import session_scope
    from app.models.entities import User as UserModel

    with session_scope() as session:
        user = UserModel(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id

    response = client.post(
        "/schema/discover",
        headers=_auth_token(user_id),
        json={"connection": _connection(password=None)},
    )
    assert response.status_code == 422


def test_schema_get_unknown_job_returns_404(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "unknown_job.db")
    from app.database.session import session_scope
    from app.models.entities import User as UserModel

    with session_scope() as session:
        user = UserModel(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id

    response = client.get(
        "/schema/job/00000000-0000-0000-0000-000000000000", headers=_auth_token(user_id)
    )
    assert response.status_code == 404
