"""Audit API (Phase 12).

Reading audit records is a privileged operation: **both endpoints
require the admin role** so classification metadata, backup paths and
operational details are not exposed to ordinary users. The ``limit``
parameter is bounded to keep reads predictable. The API exposes read
access only — there is no endpoint to modify or delete audit rows.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.agents.audit import AuditAgent
from app.database.session import session_scope
from app.models.entities import User
from app.security.deps import require_role

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditRowOut(BaseModel):
    id: str
    occurred_at: datetime
    user_id: Optional[str]
    job_id: Optional[str]
    event_type: str
    outcome: str
    details: dict


class AuditListOut(BaseModel):
    items: list[AuditRowOut]


def _row_to_out(row) -> AuditRowOut:
    return AuditRowOut(
        id=row.id,
        occurred_at=row.occurred_at,
        user_id=row.user_id,
        job_id=row.job_id,
        event_type=row.event_type,
        outcome=row.outcome,
        details=row.details or {},
    )


@router.get("", response_model=AuditListOut)
def list_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(require_role("admin")),
) -> AuditListOut:
    session = session_scope()
    try:
        rows = AuditAgent(session).all(limit=limit)
        return AuditListOut(items=[_row_to_out(r) for r in rows])
    finally:
        session.close()


@router.get("/jobs/{job_id}", response_model=AuditListOut)
def list_audit_for_job(
    job_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(require_role("admin")),
) -> AuditListOut:
    session = session_scope()
    try:
        rows = AuditAgent(session).for_job(job_id, limit=limit)
        return AuditListOut(items=[_row_to_out(r) for r in rows])
    finally:
        session.close()
