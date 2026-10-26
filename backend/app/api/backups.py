"""Backup API (Phase 9).

Thin HTTP layer over ``BackupAgent``. The endpoint takes the same
short-lived connection details the discovery endpoints use (no
persisted credentials yet) and runs ``BACKUP DATABASE`` against the
target.

We deliberately do NOT execute backups against a database that is
not in the request body. The user is the gatekeeper.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.backup import BackupAgent, BackupError, _normalize_backup_path
from app.core.config import get_settings
from app.database.session import session_scope
from app.models.entities import BackupRecord, User
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


class BackupRecordOut(BaseModel):
    backup_path: str
    database: str
    size_bytes: int
    verified: bool
    created_at: datetime
    job_id: str | None = None


class BackupListOut(BaseModel):
    backup_dir: str
    files: list[str]
    records: list[BackupRecordOut]


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
    """List backups with their recorded size/verification metadata.

    The ``files`` field is kept for backwards compatibility. ``records``
    additionally exposes the durable ``BackupRecord`` metadata so the UI
    can show real sizes and verification state instead of guessing from
    the filename. Files present on disk without a metadata row are
    included as unverified.
    """
    agent = _agent()
    paths = agent.list_backups(database)
    files = [str(p) for p in paths]

    with session_scope() as session:
        stmt = select(BackupRecord).order_by(BackupRecord.created_at.desc())
        if database is not None:
            stmt = stmt.where(BackupRecord.database_name == database)
        rows = list(session.execute(stmt).scalars())

    records: list[BackupRecordOut] = []
    seen: set[str] = set()
    for row in rows:
        key = _normalize_backup_path(row.backup_path)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            BackupRecordOut(
                backup_path=row.backup_path,
                database=row.database_name,
                size_bytes=row.size_bytes,
                verified=row.verified,
                created_at=row.created_at,
                job_id=row.job_id,
            )
        )

    # Include on-disk files that predate metadata recording (or were
    # copied into the backup directory manually). They can never
    # authorize an update until a verified record exists.
    for path in paths:
        key = _normalize_backup_path(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = path.stat()
            size_bytes = stat.st_size
            created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        except OSError:
            size_bytes = 0
            created_at = datetime.now(timezone.utc)
        records.append(
            BackupRecordOut(
                backup_path=str(path),
                database=database or "",
                size_bytes=size_bytes,
                verified=False,
                created_at=created_at,
                job_id=None,
            )
        )

    records.sort(key=lambda r: r.created_at, reverse=True)
    return BackupListOut(
        backup_dir=str(agent.backup_dir),
        files=files,
        records=records,
    )
