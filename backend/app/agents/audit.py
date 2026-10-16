"""Phase 12 — Audit Agent.

The audit log is append-only by contract. The application never
``UPDATE``s or ``DELETE``s an ``AuditLog`` row; that constraint is
documented in the model and tested by not exposing any update/delete
code path here. Operators who need to redact an entry must insert a
**new** row with a redaction event.

Every other agent in the system writes through ``AuditAgent.write``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog
from app.security.redaction import sanitize_details


class AuditAgent:
    """Append-only audit logger.

    There is intentionally no ``update`` or ``delete`` method: ordinary
    users cannot mutate audit history through the application.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def write(
        self,
        *,
        event_type: str,
        outcome: str,
        job_id: str | None = None,
        user_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Append one audit row and return its id.

        ``details`` is sanitized (secret-shaped keys/values redacted,
        long strings truncated) before it is persisted so raw
        credentials, tokens, connection strings and PII samples never
        reach the audit trail.
        """
        row = AuditLog(
            id=str(uuid.uuid4()),
            occurred_at=datetime.now(timezone.utc),
            user_id=user_id,
            job_id=job_id,
            event_type=event_type,
            outcome=outcome,
            details=sanitize_details(details),
        )
        self._session.add(row)
        self._session.commit()
        return row.id

    def for_job(self, job_id: str, limit: int = 200) -> list[AuditLog]:
        """Return the most recent audit rows for a job, newest first."""
        rows = list(
            self._session.execute(
                select(AuditLog)
                .where(AuditLog.job_id == job_id)
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit)
            ).scalars()
        )
        return rows

    def all(self, limit: int = 200) -> list[AuditLog]:
        rows = list(
            self._session.execute(
                select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)
            ).scalars()
        )
        return rows
