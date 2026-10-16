"""Alembic environment scaffold; metadata models begin in Phase 2."""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.models.base import Base
import app.models.entities  # noqa: F401

config = context.config
target_metadata = Base.metadata

# Allow DATABASE_URL in the environment to override alembic.ini so the same
# config can drive both production migrations and disposable test targets
# (e.g. a Docker MSSQL container) without committing secrets to source.
# When the variable is not set, fall back to the application Settings which
# are loaded from backend/.env, so `alembic upgrade head` works out of the
# box using the exact same URL the API uses.
_db_url = os.getenv("DATABASE_URL")
if not _db_url:
    from app.core.config import get_settings

    _db_url = get_settings().database_url.get_secret_value()
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)


def run_migrations_offline() -> None:
    context.configure(
        url=context.config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
