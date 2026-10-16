"""Shared pytest fixtures for the backend test suite.

Every test that touches the application database or settings should use
``app_env`` so it runs against a throwaway SQLite file with its own
backup directory and fresh (cached) configuration. The fixtures are
additive: existing test modules keep their own local setup helpers.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# Fixed test-only key (valid urlsafe-base64, 32 bytes). Never used in
# production; the real key must come from the environment.
TEST_ENCRYPTION_KEY = "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo="
TEST_JWT_SECRET = "x" * 40


@pytest.fixture
def app_env(monkeypatch, tmp_path) -> SimpleNamespace:
    """Isolated app DB (SQLite), backup dir, JWT + encryption keys.

    Clears the cached settings/engine/factory and creates the schema so
    tests never touch the developer's ``app.db``.
    """
    db = tmp_path / "app.db"
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db}")
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", TEST_ENCRYPTION_KEY)
    monkeypatch.setenv("BACKUP_DIR", str(backup_dir))
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    from app.core import config as cfg
    from app.database import session as sess
    from app.models import Base

    cfg.get_settings.cache_clear()
    sess.get_engine.cache_clear()
    sess.get_session_factory.cache_clear()
    Base.metadata.create_all(sess.get_engine())
    return SimpleNamespace(db=db, backup_dir=backup_dir, tmp_path=tmp_path)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app=_import_app())


def _import_app():
    from app.main import app

    return app


@pytest.fixture
def seed_user(app_env):
    """Create a user row directly and return ``(id, username)``."""

    def _seed(username: str = "alice", role: str = "user", active: bool = True):
        from app.database.session import session_scope
        from app.models.entities import User
        from app.security.passwords import hash_password

        with session_scope() as session:
            user = User(
                username=username,
                password_hash=hash_password(f"{username}-pwd-12345"),
                is_active=active,
                role=role,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user.id, user.username

    return _seed


@pytest.fixture
def make_token(app_env):
    """Mint a JWT for a user id using the environment's JWT secret."""

    def _make(user_id: str, *, secret: str | None = None, expires_in_minutes: int = 10) -> str:
        from app.core.config import get_settings
        from app.security.tokens import create_access_token

        settings = get_settings()
        return create_access_token(
            subject=user_id,
            secret=secret if secret is not None else settings.jwt_secret_key.get_secret_value(),
            expires_in_minutes=expires_in_minutes,
            algorithm=settings.jwt_algorithm,
        )

    return _make


@pytest.fixture
def auth_headers(make_token):
    """``{"Authorization": "Bearer <token>"}`` for a given user id."""

    def _headers(user_id: str, **token_kwargs) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(user_id, **token_kwargs)}"}

    return _headers
