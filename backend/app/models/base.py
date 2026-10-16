"""SQLAlchemy declarative base and shared timestamp fields."""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Produce timezone-aware UTC timestamps for metadata records."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all application metadata models."""


class TimestampedModel:
    """Mixin providing an immutable creation timestamp and mutable update timestamp."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
