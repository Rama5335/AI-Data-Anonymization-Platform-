"""Masking API (Phase 10).

Trivial pass-through to ``MaskingOrchestrator``. The orchestrator
itself is what Phase 11 (Safe Update) will call when issuing
``UPDATE`` statements; the standalone ``POST /masking/prepare`` and
``POST /masking/mask`` exist so the UI and the validator can do a
dry-run, and so a custom integration can re-use the masker without
running an update.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.masking_orchestrator import (
    MaskingOrchestrator,
    MaskingOrchestratorError,
)
from app.database.session import session_scope
from app.models.entities import User
from app.security.deps import get_current_user

router = APIRouter(prefix="/masking", tags=["masking"])


class PrepareRequest(BaseModel):
    job_id: str


class MaskRequest(BaseModel):
    job_id: str
    table: str
    column: str
    value: Any


class MaskResponse(BaseModel):
    job_id: str
    table: str
    column: str
    input: Any
    output: Any
    changed: bool


@router.post("/prepare")
def prepare_masking(req: PrepareRequest, user: User = Depends(get_current_user)) -> dict[str, str]:
    session = session_scope()
    try:
        orch = MaskingOrchestrator(session)
        try:
            orch.prepare(req.job_id)
        except MaskingOrchestratorError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": req.job_id, "status": "ready"}
    finally:
        session.close()


@router.post("/mask", response_model=MaskResponse)
def mask_value_endpoint(req: MaskRequest, user: User = Depends(get_current_user)) -> MaskResponse:
    session = session_scope()
    try:
        orch = MaskingOrchestrator(session)
        try:
            ctx = orch.load(req.job_id)
        except MaskingOrchestratorError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        output = orch.mask(ctx, req.table, req.column, req.value)
        return MaskResponse(
            job_id=req.job_id,
            table=req.table,
            column=req.column,
            input=req.value,
            output=output,
            changed=output != req.value,
        )
    finally:
        session.close()
