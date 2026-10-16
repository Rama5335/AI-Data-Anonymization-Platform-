"""Auth API tests (Item 1)."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.database.session import session_scope
from app.main import app
from app.models import Base
from app.models.entities import User
from app.security.tokens import create_access_token, verify_access_token
from app.security.passwords import hash_password, verify_password


client = TestClient(app)


def _setup(monkeypatch, tmp_path):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())


def _seed(monkeypatch, tmp_path, *, admin=False):
    _setup(monkeypatch, tmp_path)
    with session_scope() as session:
        session.add(
            User(
                username="alice",
                password_hash=hash_password("alice-pwd-1"),
                is_active=True,
                role="admin" if admin else "user",
            )
        )
        session.commit()


def test_hash_password_round_trip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h) is True
    assert verify_password("hunter2!", h) is False


def test_login_success_issues_jwt(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = client.post("/auth/login", json={"username": "alice", "password": "alice-pwd-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "user"
    assert body["expires_in"] > 0
    settings = get_settings()
    subject = verify_access_token(
        body["access_token"],
        settings.jwt_secret_key.get_secret_value(),
        settings.jwt_algorithm,
    )
    assert subject == body["user"]["id"]


def test_login_wrong_password_returns_401(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_user_returns_401(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = client.post("/auth/login", json={"username": "nope", "password": "x"})
    assert r.status_code == 401


def test_users_me_requires_auth(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = client.get("/users/me")
    assert r.status_code == 401


def test_users_me_returns_authenticated_user(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    settings = get_settings()
    with session_scope() as session:
        from sqlalchemy import select

        alice = session.execute(select(User).where(User.username == "alice")).scalar_one()
    token = create_access_token(
        subject=alice.id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["id"] == alice.id


def test_users_me_rejects_expired_token(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    import jwt as _jwt
    from datetime import datetime, timedelta, timezone

    settings = get_settings()
    payload = {
        "sub": "00000000-0000-0000-0000-000000000000",
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    expired = _jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
    r = client.get("/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


def test_create_user_requires_admin(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    r = client.post(
        "/users",
        json={"username": "bob", "password": "bob-pwd-12345", "role": "user"},
    )
    # No admin user exists; should be 401 (no token) or 403.
    assert r.status_code in (401, 403)


def test_create_user_works_as_admin(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, admin=True)
    settings = get_settings()
    with session_scope() as session:
        from sqlalchemy import select

        alice = session.execute(select(User).where(User.username == "alice")).scalar_one()
    token = create_access_token(
        subject=alice.id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    r = client.post(
        "/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "bob", "password": "bob-pwd-12345", "role": "user"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "bob"
    assert body["role"] == "user"


def test_create_user_duplicate_409(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, admin=True)
    settings = get_settings()
    with session_scope() as session:
        from sqlalchemy import select

        alice = session.execute(select(User).where(User.username == "alice")).scalar_one()
    token = create_access_token(
        subject=alice.id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    r = client.post(
        "/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "alice", "password": "another-pwd-12345", "role": "user"},
    )
    assert r.status_code == 409


def test_bootstrap_admin_created_on_first_login(monkeypatch, tmp_path):
    """If BOOTSTRAP_ADMIN_USERNAME and PASSWORD are set, the first login
    that touches the user table creates the admin user."""
    db = tmp_path / "auth_bootstrap.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "CHANGE_ME_ADMIN_PASSWORD")
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())

    # First call to login should create the admin and succeed.
    r = client.post("/auth/login", json={"username": "root", "password": "CHANGE_ME_ADMIN_PASSWORD"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "admin"


def test_bootstrap_admin_password_reconciled_when_user_exists(monkeypatch, tmp_path):
    """A later bootstrap must be able to fix an existing admin's password.

    Regression: previously ``_ensure_bootstrap_admin`` returned early
    whenever the username already existed, so a mistyped first-run
    bootstrap password could never be corrected without deleting the
    account.
    """
    db = tmp_path / "auth_bootstrap_reset.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    # Seed the admin without any bootstrap variables, simulating an
    # earlier run whose password got mangled.
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    from app.core import config as cfg
    from app.database import session as sess

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())
    with session_scope() as session:
        session.add(
            User(
                username="admin",
                password_hash=hash_password("old-mistyped-password"),
                is_active=True,
                role="admin",
            )
        )
        session.commit()

    # The stale password works before reconciliation...
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": "old-mistyped-password"},
    )
    assert r.status_code == 200, r.text

    # ...then the operator sets the bootstrap credentials and retries.
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "correct-horse-battery")
    r = client.post(
        "/auth/login",
        json={"username": "admin", "password": "correct-horse-battery"},
    )
    assert r.status_code == 200, r.text

    # The stale password no longer verifies.
    r_old = client.post(
        "/auth/login",
        json={"username": "admin", "password": "old-mistyped-password"},
    )
    assert r_old.status_code == 401
