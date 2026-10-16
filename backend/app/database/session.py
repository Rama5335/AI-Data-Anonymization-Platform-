"""SQLAlchemy engine and session factory for the application metadata DB.

The engine URL is taken from ``Settings.database_url``. The target MSSQL
that schema/discovery agents inspect is supplied per-request and is never
mixed with the metadata engine.
"""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine bound to the metadata database."""
    settings = get_settings()
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Return a cached ``sessionmaker`` bound to the metadata engine."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def session_scope() -> Session:
    """Return a fresh ``Session``; callers are responsible for closing it."""
    return get_session_factory()()
