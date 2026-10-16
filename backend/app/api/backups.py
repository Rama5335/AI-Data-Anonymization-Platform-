"""Backup API (Phase 9).

Thin HTTP layer over ``BackupAgent``. The endpoint takes the same
short-lived connection details the discovery endpoints use (no
persisted credentials yet) and runs ``BACKUP DATABASE`` against the
target.

We deliberately do NOT execute backups against a database that is
not in the request body. The user is the gatekeeper.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents.backup import BackupAgent, BackupError
from app.core.config import get_settings
from app.models.entities import User
from app.schemas.discovery import ConnectionDetails
from app.security.deps import require_role
from app.security.redaction import redact_text

router = APIRouter(prefix="/backups", tags=["backups"])


class BackupRequest(BaseModel):
    connection: ConnectionDetails
    database: str | None = Field(
        default=None,
        description="Database to back up. Defaults to ``connection.database``.",
    )
    job_id: str | None = Field(
        default=None,
        description="Optional job ID to associate with this backup in the audit log.",
    )
    keep: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Override the per-database retention count for this call.",
    )


class BackupResultOut(BaseModel):
    database: str
    backup_path: str
    size_bytes: int
    verified: bool
    created_at: str
    kept_after: int


class BackupListOut(BaseModel):
    backup_dir: str
    files: list[str]


def _agent() -> BackupAgent:
    settings = get_settings()
    return BackupAgent(backup_dir=settings.backup_dir, keep=settings.backup_keep)


@router.post("", response_model=BackupResultOut)
def run_backup(req: BackupRequest, user: User = Depends(require_role("admin"))) -> BackupResultOut:
    """Create and verify a backup. **Requires the admin role.**

    The agent persists a ``BackupRecord`` (including failed attempts)
    and writes the ``backup.complete`` / ``backup.failed`` audit rows
    with the caller's identity.
    """
    settings = get_settings()
    agent = BackupAgent(
        backup_dir=settings.backup_dir,
        keep=req.keep if req.keep is not None else settings.backup_keep,
    )
    try:
        result = agent.backup(
            req.connection,
            database=req.database,
            job_id=req.job_id,
            created_by_user_id=user.id,
        )
    except BackupError as exc:
        # ``BackupError`` may embed driver text; redact so a
        # connection string or password can never leave the API.
        raise HTTPException(status_code=503, detail=redact_text(str(exc))) from exc
    return BackupResultOut(
        database=result.database,
        backup_path=result.backup_path,
        size_bytes=result.size_bytes,
        verified=result.verified,
        created_at=result.created_at.isoformat(),
        kept_after=result.kept_after,
    )


@router.get("", response_model=BackupListOut)
def list_backups(
    database: str | None = None, user: User = Depends(require_role("admin"))
) -> BackupListOut:
    agent = _agent()
    files = [str(p) for p in agent.list_backups(database)]
    return BackupListOut(backup_dir=str(agent.backup_dir), files=files)
