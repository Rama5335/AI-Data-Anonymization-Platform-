"""Approval API (Phase 8) — now JWT-protected (Item 1).

Thin HTTP layer over the ApprovalAgent. Endpoints:

* ``POST /approvals`` — submit a decision. **Requires JWT.**
  The ``approver_user_id`` is derived from the token (the body field
  is ignored).
* ``GET  /approvals/jobs/{job_id}/pending`` — list classifications
  awaiting human review. **Requires JWT.**
* ``GET  /approvals/jobs/{job_id}`` — list submitted approvals.
  **Requires JWT.**
"""

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.approval import ApprovalAgent
from app.database.session import session_scope
from app.models.entities import Approval, DiscoveredColumn, User
from app.security.deps import get_current_user

router = APIRouter(prefix="/approvals", tags=["approvals"])


class SubmitApprovalRequest(BaseModel):
    classification_id: str
    decision: str = Field(pattern="^(approve|reject|override)$")
    masking_strategy: Optional[str] = None
    comment: Optional[str] = None
    excluded: bool = False


class ApprovalOut(BaseModel):
    id: str
    classification_id: str
    approved_by_user_id: str
    decision: str
    masking_strategy: Optional[str]
    comment: Optional[str]
    excluded: bool
    created_at: datetime
    updated_at: datetime


class PendingItem(BaseModel):
    classification_id: str
    schema_name: str
    table_name: str
    column_name: str
    data_type: str
    sensitivity: str
    confidence: float
    suggested_strategy: str
    attempts: int
    reasoning: str


class PendingList(BaseModel):
    job_id: str
    items: list[PendingItem]


def _row_to_out(row: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=row.id,
        classification_id=row.classification_result_id,
        approved_by_user_id=row.approved_by_user_id,
        decision=row.decision,
        masking_strategy=row.masking_strategy,
        comment=row.comment,
        excluded=row.excluded,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("", response_model=ApprovalOut)
def submit_approval(
    req: SubmitApprovalRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ApprovalOut:
    session = session_scope()
    try:
        agent = ApprovalAgent(session)
        try:
            decision = agent.submit(
                classification_id=req.classification_id,
                approver_user_id=user.id,
                decision=req.decision,
                masking_strategy=req.masking_strategy,
                comment=req.comment,
                excluded=req.excluded,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        from app.models.entities import Approval as _A

        row = session.execute(select(_A).where(_A.id == decision.id)).scalar_one()
        return _row_to_out(row)
    finally:
        session.close()


@router.get("/jobs/{job_id}/pending", response_model=PendingList)
def list_pending(job_id: str, user: Annotated[User, Depends(get_current_user)]) -> PendingList:
    session = session_scope()
    try:
        agent = ApprovalAgent(session)
        rows = agent.pending_for_job(job_id)
        # Resolve data_type from DiscoveredColumn for the UI.
        col_types: dict[tuple[str, str, str], str] = {}
        for r in rows:
            key = (r.schema_name, r.table_name, r.column_name)
            if key in col_types:
                continue
            from app.models.entities import DiscoveredTable

            tbl = session.execute(
                select(DiscoveredTable).where(
                    DiscoveredTable.job_id == job_id,
                    DiscoveredTable.schema_name == r.schema_name,
                    DiscoveredTable.table_name == r.table_name,
                )
            ).scalar_one_or_none()
            if tbl is None:
                continue
            col = session.execute(
                select(DiscoveredColumn).where(
                    DiscoveredColumn.table_id == tbl.id,
                    DiscoveredColumn.name == r.column_name,
                )
            ).scalar_one_or_none()
            if col is not None:
                col_types[key] = col.data_type
        items = [
            PendingItem(
                classification_id=r.id,
                schema_name=r.schema_name,
                table_name=r.table_name,
                column_name=r.column_name,
                data_type=col_types.get((r.schema_name, r.table_name, r.column_name), "?"),
                sensitivity=r.sensitivity,
                confidence=r.confidence,
                suggested_strategy=r.suggested_strategy,
                attempts=r.attempts,
                reasoning=r.reasoning,
            )
            for r in rows
        ]
        return PendingList(job_id=job_id, items=items)
    finally:
        session.close()


@router.get("/jobs/{job_id}", response_model=list[ApprovalOut])
def list_approvals(
    job_id: str, user: Annotated[User, Depends(get_current_user)]
) -> list[ApprovalOut]:
    session = session_scope()
    try:
        agent = ApprovalAgent(session)
        rows = agent.approvals_for_job(job_id)
        return [_row_to_out(r) for r in rows]
    finally:
        session.close()
