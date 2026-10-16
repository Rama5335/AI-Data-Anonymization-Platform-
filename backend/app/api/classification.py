"""Classification API.

The classification agent is invoked from a thin HTTP layer. The
caller passes only a ``column_id``/``table_id``/``job_id``; the agent
fetches sample values from the persisted ``DiscoveredSample`` and
calls the LLM. Failures are surfaced as ``ok=False`` rows so the UI
can render them.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.classification import ClassificationAgent, ClassificationOutcome
from app.agents.report import build_job_report, render_markdown, report_to_dict
from app.classification.factory import build_llm_client
from app.database.session import session_scope
from app.models.entities import (
    ClassificationResult,
    DiscoveredColumn,
    DiscoveredTable,
    Job,
    User,
)
from app.schemas.classification import (
    ClassifyColumnRequest,
    ClassifyJobRequest,
    ClassificationResultOut,
    ClassificationRunResponse,
    JobClassificationResponse,
)
from app.security.deps import get_current_user
from app.security.redaction import redact_text

router = APIRouter(prefix="/classification", tags=["classification"])


def _row_to_out(row: ClassificationResult, job_id: str) -> ClassificationResultOut:
    return ClassificationResultOut(
        id=row.id,
        job_id=job_id,
        schema_name=row.schema_name,
        table_name=row.table_name,
        column_name=row.column_name,
        sensitivity=row.sensitivity,
        confidence=row.confidence,
        reasoning=row.reasoning,
        suggested_strategy=row.suggested_strategy,
        attempts=row.attempts,
        ok=row.ok,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _read_rows(
    session: Session, outcomes: list[ClassificationOutcome]
) -> list[ClassificationResult]:
    rows: list[ClassificationResult] = []
    for outcome in outcomes:
        row = session.execute(
            select(ClassificationResult).where(ClassificationResult.id == outcome.classification_id)
        ).scalar_one_or_none()
        if row is not None:
            rows.append(row)
    return rows


def _job_id_from_outcome(session: Session, outcomes: list[ClassificationOutcome]) -> str:
    if not outcomes:
        return ""
    col = session.get(DiscoveredColumn, outcomes[0].column_id)
    if col is None:
        return ""
    table = session.get(DiscoveredTable, col.table_id)
    return table.job_id if table is not None else ""


@router.post("/columns/{column_id}", response_model=ClassificationRunResponse)
def classify_column(
    column_id: str, request: ClassifyColumnRequest, user: User = Depends(get_current_user)
) -> ClassificationRunResponse:
    """Classify a single discovered column by id."""
    client = build_llm_client()
    session = session_scope()
    try:
        agent = ClassificationAgent(session, client)
        try:
            outcomes = [
                agent.classify_column(
                    _job_id_for_column(session, column_id), column_id, request.max_retries
                )
            ]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail=f"Classification failed: {redact_text(str(exc))}"
            ) from exc
        job_id = _job_id_from_outcome(session, outcomes)
        rows = _read_rows(session, outcomes)
    finally:
        session.close()

    results = [_row_to_out(r, job_id) for r in rows]
    return ClassificationRunResponse(
        job_id=job_id,
        results=results,
        classified_count=sum(1 for r in results if r.ok),
        failed_count=sum(1 for r in results if not r.ok),
    )


@router.post("/tables/{table_id}", response_model=ClassificationRunResponse)
def classify_table(
    table_id: str, request: ClassifyJobRequest, user: User = Depends(get_current_user)
) -> ClassificationRunResponse:
    """Classify every column of a discovered table."""
    client = build_llm_client()
    session = session_scope()
    try:
        agent = ClassificationAgent(session, client)
        try:
            outcomes = agent.classify_table(
                _job_id_for_table(session, table_id), table_id, request.max_retries
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail=f"Classification failed: {redact_text(str(exc))}"
            ) from exc
        job_id = _job_id_from_outcome(session, outcomes)
        rows = _read_rows(session, outcomes)
    finally:
        session.close()

    results = [_row_to_out(r, job_id) for r in rows]
    return ClassificationRunResponse(
        job_id=job_id,
        results=results,
        classified_count=sum(1 for r in results if r.ok),
        failed_count=sum(1 for r in results if not r.ok),
    )


@router.post("/jobs/{job_id}", response_model=ClassificationRunResponse)
def classify_job(
    job_id: str, request: ClassifyJobRequest, user: User = Depends(get_current_user)
) -> ClassificationRunResponse:
    """Classify every column of every table in a job (or one table)."""
    client = build_llm_client()
    session = session_scope()
    try:
        agent = ClassificationAgent(session, client)
        try:
            outcomes = agent.classify_job(
                job_id,
                max_retries=request.max_retries,
                table_id=request.table_id,
                include_non_sampleable=request.include_non_sampleable,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail=f"Classification failed: {redact_text(str(exc))}"
            ) from exc
        rows = _read_rows(session, outcomes)
    finally:
        session.close()

    results = [_row_to_out(r, job_id) for r in rows]
    return ClassificationRunResponse(
        job_id=job_id,
        results=results,
        classified_count=sum(1 for r in results if r.ok),
        failed_count=sum(1 for r in results if not r.ok),
    )


@router.get("/jobs/{job_id}", response_model=JobClassificationResponse)
def get_job_classifications(
    job_id: str, user: User = Depends(get_current_user)
) -> JobClassificationResponse:
    """Return the persisted classifications for a job.

    Always returns ``status: "completed"`` — the classification API
    is synchronous. The field exists so a future async backend can
    signal in-flight status without changing the response shape.
    """
    session = session_scope()
    try:
        rows = list(
            session.execute(
                select(ClassificationResult)
                .where(ClassificationResult.job_id == job_id)
                .order_by(
                    ClassificationResult.schema_name,
                    ClassificationResult.table_name,
                    ClassificationResult.column_name,
                )
            ).scalars()
        )
        # Count discovered columns that are not yet classified.
        from app.models.entities import DiscoveredColumn, DiscoveredTable
        from sqlalchemy import func

        total_cols = session.execute(
            select(func.count(DiscoveredColumn.id))
            .join(DiscoveredTable, DiscoveredColumn.table_id == DiscoveredTable.id)
            .where(DiscoveredTable.job_id == job_id)
        ).scalar_one()
        pending = max(0, (total_cols or 0) - len(rows))
        return JobClassificationResponse(
            job_id=job_id,
            results=[_row_to_out(r, job_id) for r in rows],
            status="completed",
            pending_columns=pending,
        )
    finally:
        session.close()


@router.get("/jobs/{job_id}/report")
def get_job_report(
    job_id: str, format: str = "json", user: User = Depends(get_current_user)
) -> dict[str, Any] | dict[str, str]:
    """Return the per-job classification report.

    ``format=json`` returns the structured rollup (default).
    ``format=md`` returns a Markdown rendering for the human reviewer.
    """
    if format not in ("json", "md"):
        raise HTTPException(status_code=400, detail="format must be 'json' or 'md'")
    session = session_scope()
    try:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
        report = build_job_report(session, job_id)
    finally:
        session.close()
    if format == "md":
        return {"job_id": job_id, "markdown": render_markdown(report)}
    return report_to_dict(report)


def _job_id_for_column(session: Session, column_id: str) -> str:
    col = session.get(DiscoveredColumn, column_id)
    if col is None:
        raise ValueError(f"DiscoveredColumn {column_id!r} not found.")
    table = session.get(DiscoveredTable, col.table_id)
    if table is None:
        raise ValueError(f"DiscoveredTable for column {column_id!r} not found.")
    return table.job_id


def _job_id_for_table(session: Session, table_id: str) -> str:
    table = session.get(DiscoveredTable, table_id)
    if table is None:
        raise ValueError(f"DiscoveredTable {table_id!r} not found.")
    return table.job_id
