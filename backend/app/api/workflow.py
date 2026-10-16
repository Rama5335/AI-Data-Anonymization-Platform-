"""Workflow API (Phase 13)."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.workflow import (
    Phase,
    WorkflowError,
    WorkflowOrchestrator,
    allowed_transitions,
)
from app.database.session import session_scope
from app.models.entities import User
from app.security.deps import get_current_user

router = APIRouter(prefix="/workflow", tags=["workflow"])


class WorkflowStateOut(BaseModel):
    job_id: str
    phase: str
    updated_at: datetime
    started_at: datetime
    is_stale: bool
    notes: Optional[str] = None


class TransitionRequest(BaseModel):
    to_phase: str
    note: Optional[str] = None


class TransitionResponse(BaseModel):
    ok: bool
    state: WorkflowStateOut


@router.get("/jobs/{job_id}", response_model=WorkflowStateOut)
def get_state(job_id: str, user: User = Depends(get_current_user)) -> WorkflowStateOut:
    session = session_scope()
    try:
        st = WorkflowOrchestrator(session).current(job_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        session.close()
    return _to_out(st)


@router.post("/jobs/{job_id}/transition", response_model=TransitionResponse)
def transition(
    job_id: str, req: TransitionRequest, user: User = Depends(get_current_user)
) -> TransitionResponse:
    try:
        to = Phase(req.to_phase)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown phase: {req.to_phase!r}. Allowed: {[p.value for p in Phase]}",
        ) from exc
    session = session_scope()
    try:
        orch = WorkflowOrchestrator(session)
        try:
            st = orch.transition(job_id, to, note=req.note)
        except WorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        session.close()
    return TransitionResponse(ok=True, state=_to_out(st))


@router.get("/phases")
def get_phases(user: User = Depends(get_current_user)) -> dict[str, list[str]]:
    """Public view of the state machine."""
    return {p.value: sorted(q.value for q in qs) for p, qs in allowed_transitions().items()}


def _to_out(st) -> WorkflowStateOut:
    return WorkflowStateOut(
        job_id=st.job_id,
        phase=st.phase.value,
        updated_at=st.updated_at,
        started_at=st.started_at,
        is_stale=st.is_stale,
        notes=st.notes,
    )
