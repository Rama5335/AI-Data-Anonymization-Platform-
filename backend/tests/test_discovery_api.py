"""Unit tests for the read-only discovery HTTP endpoints."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.session import session_scope
from app.main import app
from app.models import Base
from app.models.entities import User
from app.security.tokens import create_access_token


client = TestClient(app)


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "discovery_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes-xx")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed_user(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        user = User(username="tester", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id
    return user_id


def _auth_token(user_id):
    settings = get_settings()
    token = create_access_token(
        subject=user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _details(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "host": "mssql.local",
        "port": 1433,
        "auth_type": "sql",
        "username": "sa",
        "password": "P@ssw0rd!",
        "database": "appdb",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": True,
        "timeout_seconds": 5,
    }
    payload.update(overrides)
    return payload


def test_test_connection_reports_success(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient()
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post(
            "/discovery/test-connection", headers=_auth_token(user_id), json=_details()
        )
    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake_client.disposed is True


def test_test_connection_translates_driver_failure_to_503(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient(raise_on_test=RuntimeError("boom"))
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post(
            "/discovery/test-connection", headers=_auth_token(user_id), json=_details()
        )
    assert response.status_code == 503
    assert "boom" in response.json()["detail"]
    assert fake_client.disposed is True


def test_list_databases_returns_payload(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient(databases=["appdb", "reporting"])
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post(
            "/discovery/databases", headers=_auth_token(user_id), json=_details()
        )
    assert response.status_code == 200
    assert response.json() == {"databases": ["appdb", "reporting"]}


def test_list_schemas_returns_payload(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient(schemas=["dbo", "sales"])
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post("/discovery/schemas", headers=_auth_token(user_id), json=_details())
    assert response.status_code == 200
    assert response.json() == {"schemas": ["dbo", "sales"]}


def test_list_objects_rejects_disallowed_type(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    response = client.post(
        "/discovery/objects?object_type=PROCEDURE", headers=_auth_token(user_id), json=_details()
    )
    assert response.status_code == 422


def test_list_objects_returns_table_payload(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient(
        objects=[
            _FakeObject(schema_name="dbo", name="Customers", object_type="BASE TABLE"),
            _FakeObject(schema_name="sales", name="Orders", object_type="BASE TABLE"),
        ]
    )
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post(
            "/discovery/objects?object_type=TABLE", headers=_auth_token(user_id), json=_details()
        )
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "objects": [
            {"schema_name": "dbo", "name": "Customers", "object_type": "BASE TABLE"},
            {"schema_name": "sales", "name": "Orders", "object_type": "BASE TABLE"},
        ]
    }


def test_list_objects_translates_client_validation_error(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    fake_client = _FakeClient(raise_on_objects=ValueError("Object type must be TABLE or VIEW."))
    with patch("app.api.discovery.MssqlDiscoveryClient", return_value=fake_client):
        response = client.post(
            "/discovery/objects?object_type=TABLE", headers=_auth_token(user_id), json=_details()
        )
    assert response.status_code == 400


def test_sql_auth_without_password_is_rejected(monkeypatch, tmp_path) -> None:
    user_id = _seed_user(monkeypatch, tmp_path)
    payload = _details()
    payload["password"] = None
    response = client.post("/discovery/test-connection", headers=_auth_token(user_id), json=payload)
    assert response.status_code == 422


class _FakeObject:
    def __init__(self, schema_name: str, name: str, object_type: str) -> None:
        self.schema_name = schema_name
        self.name = name
        self.object_type = object_type


class _FakeClient:
    def __init__(
        self,
        *,
        databases: list[str] | None = None,
        schemas: list[str] | None = None,
        objects: list[_FakeObject] | None = None,
        raise_on_test: Exception | None = None,
        raise_on_objects: Exception | None = None,
    ) -> None:
        self._databases = databases or []
        self._schemas = schemas or []
        self._objects = objects or []
        self._raise_on_test = raise_on_test
        self._raise_on_objects = raise_on_objects
        self.disposed = False

    def test_connection(self) -> None:
        if self._raise_on_test is not None:
            raise self._raise_on_test

    def list_databases(self) -> list[str]:
        return self._databases

    def list_schemas(self) -> list[str]:
        return self._schemas

    def list_objects(self, object_type: str) -> list[_FakeObject]:
        if self._raise_on_objects is not None:
            raise self._raise_on_objects
        return self._objects

    def dispose(self) -> None:
        self.disposed = True
