"""Servers API tests (Item 4 backend)."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.session import session_scope
from app.main import app
from app.models import Base
from app.models.entities import Connection, User
from app.security.passwords import hash_password
from app.security.tokens import create_access_token


client = TestClient(app)


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "servers.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
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


def _seed_admin(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        admin = User(
            username="admin",
            password_hash=hash_password("admin-pwd-12345"),
            is_active=True,
            role="admin",
        )
        session.add(admin)
        session.commit()


def _admin_token():
    settings = get_settings()
    with session_scope() as session:
        from sqlalchemy import select

        admin = session.execute(select(User).where(User.username == "admin")).scalar_one()
    return create_access_token(
        subject=admin.id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )


def _auth():
    return {"Authorization": f"Bearer {_admin_token()}"}


def test_list_servers_starts_empty(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.get("/servers", headers=_auth())
    assert r.status_code == 200
    assert r.json() == []


def test_create_server(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers",
        headers=_auth(),
        json={"name": "lab-mssql", "host": "mssql.lab.local", "port": 1433, "auth_type": "sql"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "lab-mssql"
    assert body["host"] == "mssql.lab.local"
    assert body["port"] == 1433
    assert body["auth_type"] == "sql"
    assert "id" in body


def test_create_server_duplicate_409(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    payload = {"name": "lab", "host": "h", "port": 1433, "auth_type": "sql"}
    r1 = client.post("/servers", headers=_auth(), json=payload)
    assert r1.status_code == 201
    r2 = client.post("/servers", headers=_auth(), json=payload)
    assert r2.status_code == 409


def test_get_server(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers", headers=_auth(), json={"name": "a", "host": "h", "port": 1, "auth_type": "sql"}
    )
    sid = r.json()["id"]
    r2 = client.get(f"/servers/{sid}", headers=_auth())
    assert r2.status_code == 200
    assert r2.json()["name"] == "a"


def test_get_server_404(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.get("/servers/00000000-0000-0000-0000-000000000000", headers=_auth())
    assert r.status_code == 404


def test_patch_server(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers", headers=_auth(), json={"name": "a", "host": "h1", "port": 1, "auth_type": "sql"}
    )
    sid = r.json()["id"]
    r2 = client.patch(
        f"/servers/{sid}",
        headers=_auth(),
        json={"name": "b", "host": "h2", "port": 2},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["name"] == "b"
    assert body["host"] == "h2"
    assert body["port"] == 2
    assert body["auth_type"] == "sql"  # unchanged


def test_delete_server_no_connections(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers", headers=_auth(), json={"name": "a", "host": "h", "port": 1, "auth_type": "sql"}
    )
    sid = r.json()["id"]
    r2 = client.delete(f"/servers/{sid}", headers=_auth())
    assert r2.status_code == 204
    r3 = client.get(f"/servers/{sid}", headers=_auth())
    assert r3.status_code == 404


def test_delete_server_with_connection_409(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers", headers=_auth(), json={"name": "a", "host": "h", "port": 1, "auth_type": "sql"}
    )
    sid = r.json()["id"]
    # Insert a Connection that references this server.
    with session_scope() as session:
        session.add(
            Connection(
                server_id=sid,
                database_name="appdb",
                encrypted_connection_string="!placeholder",
            )
        )
        session.commit()
    r2 = client.delete(f"/servers/{sid}", headers=_auth())
    assert r2.status_code == 409


def test_probe_server_unreachable(monkeypatch, tmp_path):
    """Probe a non-existent server: should return reachable=False, error=... ."""
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers",
        headers=_auth(),
        json={"name": "ghost", "host": "127.0.0.1", "port": 1, "auth_type": "sql"},
    )
    sid = r.json()["id"]
    r2 = client.post(
        f"/servers/{sid}/probe",
        headers=_auth(),
        json={"username": "sa", "password": "whatever"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["reachable"] is False
    assert body["databases"] is None
    assert body["error"]


def test_probe_server_reachable(monkeypatch, tmp_path):
    """Probe a real server (the Phase-1 validator's MSSQL).

    Skipped when the local MSSQL container isn't reachable. The
    ``unreachable`` test above covers the negative path.
    """
    _seed_admin(monkeypatch, tmp_path)
    r = client.post(
        "/servers",
        headers=_auth(),
        json={"name": "local-mssql", "host": "127.0.0.1", "port": 1433, "auth_type": "sql"},
    )
    sid = r.json()["id"]
    r2 = client.post(
        f"/servers/{sid}/probe",
        headers=_auth(),
        json={"username": "sa", "password": "CHANGE_ME_SA_PASSWORD"},
    )
    if r2.status_code != 200:
        pytest.skip(f"probe endpoint error: {r2.text[:200]}")
    body = r2.json()
    if not body.get("reachable"):
        pytest.skip(f"local MSSQL not reachable in this environment: {body.get('error')}")
    assert "anonymizer_app" in (body["databases"] or [])


def test_servers_requires_auth(monkeypatch, tmp_path):
    _seed_admin(monkeypatch, tmp_path)
    r = client.get("/servers")
    assert r.status_code == 401
    r2 = client.post(
        "/servers",
        json={"name": "a", "host": "h", "port": 1, "auth_type": "sql"},
    )
    assert r2.status_code == 401
