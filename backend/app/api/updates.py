"""Safe Update API (Phase 11).

Two endpoints:

* ``POST /updates/plan`` — return the dry-run plan only. The caller
  can show this to the operator and require explicit confirmation.
  Requires authentication (read-only).
* ``POST /updates/apply`` — execute the plan. **Requires the admin
  role**, ``confirm: true``, and a non-empty ``expected_backup_path``
  that resolves to a **verified** ``BackupRecord`` (see
  ``SafeUpdateAgent.apply``).
* ``POST /updates/rollback`` — restore the target database from a
  verified backup. **Requires the admin role** and audits every
  attempt (start, success, failure).

The target MSSQL is reached via the same one-shot connection
pattern as the discovery endpoints (no persisted credentials).
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.agents.audit import AuditAgent
from app.agents.backup import server_backup_path
from app.agents.safe_update import (
    SafeUpdateAgent,
    UpdateError,
    UpdatePlan,
    UpdateResult,
    _is_under_backup_dir,
)
from app.core.config import get_settings
from app.database.session import session_scope
from app.models.entities import User
from app.schemas.discovery import ConnectionDetails
from app.security.deps import get_current_user, require_role
from app.security.redaction import redact_text

router = APIRouter(prefix="/updates", tags=["updates"])


class PlanRequest(BaseModel):
    job_id: str
    connection: ConnectionDetails
    batch_size: int | None = None


class PlanResponse(BaseModel):
    job_id: str
    plans: list[dict[str, Any]]


class ApplyRequest(BaseModel):
    job_id: str
    connection: ConnectionDetails
    expected_backup_path: str
    confirm: bool = False
    batch_size: int | None = None
    dry_run: bool = False


class ApplyResponse(BaseModel):
    job_id: str
    results: list[dict[str, Any]]
    dry_run: bool
    started_at: datetime
    completed_at: datetime


def _odbc_conn_str(details: ConnectionDetails) -> str:
    """Build a plain ODBC connection string for ``pyodbc.connect``.

    ``pyodbc`` cannot parse a SQLAlchemy URL string, so we build a
    DSN-style string exactly like the backup agent does.
    """
    return (
        f"DRIVER={{{details.driver}}};"
        f"SERVER={details.host},{details.port};"
        f"UID={details.username};"
        f"PWD={details.password.get_secret_value()};"
        f"DATABASE={details.database or 'master'};"
        f"Encrypt={'yes' if details.encrypt else 'no'};"
        f"TrustServerCertificate={'yes' if details.trust_server_certificate else 'no'};"
        f"Connection Timeout={details.timeout_seconds};"
    )


@router.post("/plan", response_model=PlanResponse)
def plan_update(req: PlanRequest, user: User = Depends(get_current_user)) -> PlanResponse:
    session = session_scope()
    try:
        target_db = req.connection.database or "master"
        agent = SafeUpdateAgent(session, _odbc_conn_str(req.connection))
        try:
            plans = agent.plan(
                req.job_id,
                target_database=target_db,
                batch_size=req.batch_size or 500,
                user_id=user.id,
            )
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=redact_text(str(exc))) from exc
        return PlanResponse(
            job_id=req.job_id,
            plans=[_plan_to_dict(p) for p in plans],
        )
    finally:
        session.close()


@router.post("/apply", response_model=ApplyResponse)
def apply_update(req: ApplyRequest, user: User = Depends(require_role("admin"))) -> ApplyResponse:
    """Execute the approved, backed-up update. **Requires the admin role.**"""
    session = session_scope()
    try:
        target_db = req.connection.database or "master"
        agent = SafeUpdateAgent(session, _odbc_conn_str(req.connection))
        try:
            results = agent.apply(
                req.job_id,
                target_database=target_db,
                expected_backup_path=req.expected_backup_path,
                confirm=req.confirm,
                batch_size=req.batch_size or 500,
                dry_run=req.dry_run,
                user_id=user.id,
            )
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=redact_text(str(exc))) from exc
        return ApplyResponse(
            job_id=req.job_id,
            results=[_result_to_dict(r) for r in results],
            dry_run=req.dry_run,
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )
    finally:
        session.close()


def _plan_to_dict(p: UpdatePlan) -> dict[str, Any]:
    return {
        "table": p.table,
        "column": p.column,
        "row_count_estimate": p.row_count_estimate,
        "batches": p.batches,
        "dry_run_sql": p.dry_run_sql,
    }


def _result_to_dict(r: UpdateResult) -> dict[str, Any]:
    return {
        "table": r.table,
        "column": r.column,
        "rows_updated": r.rows_updated,
        "batches": r.batches,
        "duration_ms": r.duration_ms,
        "audit_log_id": r.audit_log_id,
    }


# ---------------------------------------------------------------------------
# Rollback (restore from backup)
# ---------------------------------------------------------------------------


class RollbackRequest(BaseModel):
    connection: ConnectionDetails
    backup_path: str
    database: str


class RollbackResponse(BaseModel):
    success: bool
    database: str
    backup_path: str
    message: str


def _audit_rollback(
    *,
    event_type: str,
    outcome: str,
    user_id: str,
    username: str,
    job_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit write for a rollback attempt; never raises."""
    try:
        with session_scope() as audit_session:
            AuditAgent(audit_session).write(
                event_type=event_type,
                outcome=outcome,
                job_id=job_id,
                user_id=user_id,
                details={**details, "initiated_by": username},
            )
    except Exception as exc:  # noqa: BLE001 - must not mask the restore result
        from loguru import logger

        logger.error("Failed to write rollback audit event {}: {}", event_type, exc)


def _verify_restorable_backup(backup_path: str, database: str) -> str | None:
    """Return the verified backup record's job id (or ``None``).

    Raises ``HTTPException(422/409)`` when the path is outside the
    backup directory, the file is missing, or there is no *verified*
    ``BackupRecord`` for the requested database. An unverified or
    unrecorded backup is never restorable through this endpoint.
    """
    from pathlib import Path

    from sqlalchemy import select

    from app.agents.backup import _normalize_backup_path
    from app.models.entities import BackupRecord

    path = _normalize_backup_path(backup_path)
    if not _is_under_backup_dir(path):
        raise HTTPException(
            status_code=422,
            detail="backup_path is not under the configured backup directory.",
        )
    if not Path(path).exists():
        raise HTTPException(
            status_code=409,
            detail="Backup file does not exist or is not visible from the application.",
        )
    with session_scope() as session:
        record = session.execute(
            select(BackupRecord).where(BackupRecord.backup_path == path)
        ).scalar_one_or_none()
    if record is None:
        raise HTTPException(
            status_code=409,
            detail="No backup metadata is recorded for this path; the backup was "
            "not created or verified by this platform.",
        )
    if not record.verified:
        raise HTTPException(
            status_code=409,
            detail="The backup for this path is not verified; restoration is refused.",
        )
    if record.database_name != database:
        raise HTTPException(
            status_code=409,
            detail="The verified backup belongs to a different database.",
        )
    return record.job_id


@router.post("/rollback", response_model=RollbackResponse)
def rollback_from_backup(
    req: RollbackRequest,
    user: User = Depends(require_role("admin")),
) -> RollbackResponse:
    """Restore the target database from a verified backup.

    **Requires the admin role.** The backup must exist on disk and be
    recorded as verified (``RESTORE VERIFYONLY`` succeeded) for the
    requested database; otherwise the request is refused before the
    target is touched. Every attempt — start, success, failure — is
    audited with the caller's identity.

    This is the rollback capability required by plan.md: if an
    anonymized update is incorrect, the operator can restore the
    database to its pre-update state.
    """
    import re
    import time

    import pyodbc

    db = req.database
    if not db:
        raise HTTPException(status_code=422, detail="database is required for rollback.")

    _UNSAFE = re.compile(r"[^\w\s.\-_]")
    if _UNSAFE.search(db):
        raise HTTPException(status_code=422, detail=f"Unsafe database name: {db!r}")

    job_id = _verify_restorable_backup(req.backup_path, db)
    _audit_rollback(
        event_type="rollback.start",
        outcome="started",
        user_id=user.id,
        username=user.username,
        job_id=job_id,
        details={"database": db, "backup_path": req.backup_path},
    )

    # The listing may present the backup path as the backend sees it
    # (host form). Translate to the form the database server executes
    # when BACKUP_DIR names a container-only path.
    server_path = server_backup_path(get_settings().backup_dir, req.backup_path)
    safe_path = server_path.replace("'", "''")
    safe_db = db.replace("'", "''")

    conn_str = (
        f"DRIVER={{{req.connection.driver}}};"
        f"SERVER={req.connection.host},{req.connection.port};"
        f"UID={req.connection.username};"
        f"PWD={req.connection.password.get_secret_value()};"
        f"Encrypt={'yes' if req.connection.encrypt else 'no'};"
        f"TrustServerCertificate={'yes' if req.connection.trust_server_certificate else 'no'};"
        f"Connection Timeout={req.connection.timeout_seconds};"
    )

    try:
        conn = pyodbc.connect(conn_str, autocommit=True, timeout=req.connection.timeout_seconds)
    except pyodbc.Error as exc:
        _audit_rollback(
            event_type="rollback.failed",
            outcome="failure",
            user_id=user.id,
            username=user.username,
            job_id=job_id,
            details={"database": db, "stage": "connect", "error": str(exc)},
        )
        raise HTTPException(
            status_code=503,
            detail=redact_text(f"Cannot connect for rollback: {exc}"),
        ) from exc

    started = time.monotonic()
    try:
        cur = conn.cursor()
        # Single-user mode to allow restore.
        cur.execute(f"ALTER DATABASE [{safe_db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE")
        cur.execute(
            f"RESTORE DATABASE [{safe_db}] FROM DISK = N'{safe_path}' " f"WITH REPLACE, RECOVERY"
        )
        # RESTORE returns result sets that must be drained, otherwise
        # the restore is never finalized on the server and the database
        # is left stuck in the RESTORING state.
        while cur.nextset():
            pass
        cur.execute(f"ALTER DATABASE [{safe_db}] SET MULTI_USER")
        cur.close()
    except pyodbc.Error as exc:
        # Best effort: return the database to multi-user mode so a
        # failed restore does not leave it stuck in SINGLE_USER.
        try:
            cur2 = conn.cursor()
            cur2.execute(f"ALTER DATABASE [{safe_db}] SET MULTI_USER")
            cur2.close()
        except Exception:  # noqa: BLE001 - best effort only
            pass
        _audit_rollback(
            event_type="rollback.failed",
            outcome="failure",
            user_id=user.id,
            username=user.username,
            job_id=job_id,
            details={"database": db, "stage": "restore", "error": str(exc)},
        )
        raise HTTPException(
            status_code=503,
            detail=redact_text(f"RESTORE DATABASE failed: {exc}"),
        ) from exc
    finally:
        conn.close()

    duration_ms = int((time.monotonic() - started) * 1000)
    _audit_rollback(
        event_type="rollback.complete",
        outcome="success",
        user_id=user.id,
        username=user.username,
        job_id=job_id,
        details={"database": db, "backup_path": req.backup_path, "duration_ms": duration_ms},
    )

    return RollbackResponse(
        success=True,
        database=db,
        backup_path=req.backup_path,
        message=f"Database [{db}] restored from {req.backup_path}.",
    )
