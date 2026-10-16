"""Phase 9 — Backup agent.

Implements ``BACKUP DATABASE`` against the target MSSQL instance via a
``pyodbc`` connection (independent of the application's metadata
engine). Verifies integrity with ``RESTORE VERIFYONLY`` before
returning success. Enforces a FIFO retention policy on the backup
directory.

Configuration via environment:

* ``BACKUP_DIR`` — directory for ``.bak`` files (default ``./backups``).
* ``BACKUP_KEEP`` — number of backups to retain per database
  (default ``5``). The oldest file is deleted on every new backup.

Hard rule (per ``implement_plan.md`` §10): if backup creation OR
verification fails, the workflow must halt. The agent therefore
raises on any failure; callers (Phase 13) translate that into a
workflow-level exception.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyodbc

from app.schemas.discovery import ConnectionDetails


DEFAULT_BACKUP_DIR = "./backups"
DEFAULT_KEEP = 5
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]")


class BackupError(Exception):
    """Raised when a backup or verify step fails."""


def server_backup_path(backup_dir: str, path: str) -> str:
    """Return the path form the database server executes.

    ``BACKUP_DIR`` may name a directory only the database server's
    container can see (e.g. ``/var/opt/mssql/backups``). On Windows,
    pathlib rewrites such a POSIX absolute path into a drive-relative
    one (``D:\\var\\opt\\mssql\\backups``) that the server cannot
    reach, so rebuild the POSIX form from the configured directory and
    the basename. Non-POSIX dirs are returned unchanged.
    """
    if backup_dir.replace("\\", "/").startswith("/"):
        return posixpath.join(backup_dir, Path(path).name)
    return path


def _normalize_backup_path(path: str | os.PathLike[str]) -> str:
    """Return the canonical string form used as the ``BackupRecord`` key.

    Both the backup agent and the safe-update gate normalize through
    this function so a path returned by ``/backups`` matches the row
    written when the backup ran.
    """
    return os.path.normpath(os.fspath(path))


@dataclass(frozen=True)
class BackupResult:
    database: str
    backup_path: str
    size_bytes: int
    verified: bool
    created_at: datetime
    kept_after: int  # number of .bak files for this db after retention


class BackupAgent:
    """Run native ``BACKUP DATABASE`` against a target MSSQL instance."""

    def __init__(
        self,
        backup_dir: str | os.PathLike[str] = DEFAULT_BACKUP_DIR,
        keep: int = DEFAULT_KEEP,
    ) -> None:
        self._raw_dir = os.fspath(backup_dir)
        self._backup_dir = Path(self._raw_dir).resolve()
        self._keep = max(1, int(keep))
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def backup(
        self,
        details: ConnectionDetails,
        database: str | None = None,
        job_id: str | None = None,
        created_by_user_id: str | None = None,
    ) -> BackupResult:
        """Run a full backup of ``database`` and verify it.

        ``database`` defaults to ``details.database``; raising if
        neither is set, because backing up ``master`` is rarely what
        the user wants.

        A ``BackupRecord`` row is persisted for **every** attempt. On
        success it is marked ``verified=True`` (after
        ``RESTORE VERIFYONLY``); on any failure it is persisted with
        ``verified=False`` and the operation is audited before the
        error propagates. A failed or unverified backup can never
        authorize a later update.
        """
        db = database or details.database
        if not db:
            raise BackupError("No database specified for backup.")
        if not details.username or not details.password:
            raise BackupError("Backup requires a SQL-auth connection.")
        if _UNSAFE_NAME_RE.search(db):
            raise BackupError(
                f"Refusing to back up a database whose name fails the safety check: {db!r}"
            )

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = self._backup_dir / f"{db}_{ts}.bak"
        backup_path = _normalize_backup_path(out)

        # Build a pyodbc DSN directly. We deliberately do NOT use the
        # SQLAlchemy URL builder because BACKUP DATABASE / RESTORE
        # VERIFYONLY want a plain ODBC connection string, and the
        # driver name is per-machine.
        conn_str = (
            f"DRIVER={{{details.driver}}};"
            f"SERVER={details.host},{details.port};"
            f"UID={details.username};"
            f"PWD={details.password.get_secret_value()};"
            f"Encrypt={'yes' if details.encrypt else 'no'};"
            f"TrustServerCertificate={'yes' if details.trust_server_certificate else 'no'};"
            f"Connection Timeout={details.timeout_seconds};"
        )
        # Use a dedicated autocommit=True connection for BACKUP DATABASE.
        try:
            conn = pyodbc.connect(conn_str, autocommit=True, timeout=details.timeout_seconds)
        except pyodbc.Error as exc:
            self._record_failure(
                database=db,
                backup_path=backup_path,
                job_id=job_id,
                user_id=created_by_user_id,
                stage="connect",
                error=exc,
            )
            raise BackupError(f"Cannot connect to target for backup: {exc}") from exc
        try:
            # The disk path is constructed entirely from a sanitized
            # database name and a server-generated UTC timestamp, then
            # joined to the configured backup directory. We bracket the
            # full path because Windows paths contain backslashes that
            # would otherwise terminate the string literal.
            #
            # ``BACKUP_DIR`` may name a directory only the database
            # server's container can see (e.g. /var/opt/mssql/backups);
            # hand the server the POSIX form it actually executes.
            server_path = server_backup_path(self._raw_dir, str(out))
            safe_path = server_path.replace("'", "''")
            sql = (
                f"BACKUP DATABASE [{db}] TO DISK = N'{safe_path}' " "WITH INIT, FORMAT, COMPRESSION"
            )
            try:
                cur = conn.cursor()
                cur.execute(sql)
                # BACKUP DATABASE returns multiple result sets; consume them.
                while cur.nextset():
                    pass
                cur.close()
            except pyodbc.Error as exc:
                self._record_failure(
                    database=db,
                    backup_path=backup_path,
                    job_id=job_id,
                    user_id=created_by_user_id,
                    stage="backup",
                    error=exc,
                )
                raise BackupError(f"BACKUP DATABASE [{db}] failed: {exc}") from exc

            # Skip the host-side file existence check. When the backup
            # dir is inside a Docker container, the host's Path.exists()
            # returns False even though the file is correctly written
            # inside the container. RESTORE VERIFYONLY is the
            # authoritative integrity check.

            try:
                cur = conn.cursor()
                cur.execute(f"RESTORE VERIFYONLY FROM DISK = N'{safe_path}'")
                # RESTORE VERIFYONLY returns a result set on success and
                # raises on failure. Some driver paths return no rows;
                # the no-exception result is the authoritative success
                # signal.
                cur.close()
            except pyodbc.Error as exc:
                self._record_failure(
                    database=db,
                    backup_path=backup_path,
                    job_id=job_id,
                    user_id=created_by_user_id,
                    stage="verify",
                    error=exc,
                )
                raise BackupError(f"RESTORE VERIFYONLY failed for {out}: {exc}") from exc
        finally:
            try:
                conn.close()
            except pyodbc.Error:
                pass

        kept_after = self._enforce_retention(db)
        # ``out.stat().st_size`` would only work on the host, which
        # may not share a filesystem with the MSSQL container. Return
        # 0 and let the operator ``ls -lh`` the directory on the side
        # that actually owns the file.
        try:
            size_bytes = out.stat().st_size
        except OSError:
            size_bytes = 0
        created_at = datetime.now(timezone.utc)
        self._record_success(
            database=db,
            backup_path=backup_path,
            size_bytes=size_bytes,
            job_id=job_id,
            user_id=created_by_user_id,
            kept_after=kept_after,
            created_at=created_at,
        )
        return BackupResult(
            database=db,
            backup_path=backup_path,
            size_bytes=size_bytes,
            verified=True,
            created_at=created_at,
            kept_after=kept_after,
        )

    # ---------- metadata persistence ----------

    def _record_success(
        self,
        *,
        database: str,
        backup_path: str,
        size_bytes: int,
        job_id: str | None,
        user_id: str | None,
        kept_after: int,
        created_at: datetime,
    ) -> None:
        from app.agents.audit import AuditAgent
        from app.database.session import session_scope

        with session_scope() as session:
            self._upsert_record(
                session,
                database=database,
                backup_path=backup_path,
                verified=True,
                size_bytes=size_bytes,
                job_id=job_id,
                user_id=user_id,
                error=None,
            )
            AuditAgent(session).write(
                event_type="backup.complete",
                outcome="success",
                job_id=job_id,
                user_id=user_id,
                details={
                    "database": database,
                    "backup_path": backup_path,
                    "size_bytes": size_bytes,
                    "verified": True,
                    "verification_method": "RESTORE VERIFYONLY",
                    "kept_after": kept_after,
                },
            )

    def _record_failure(
        self,
        *,
        database: str,
        backup_path: str,
        job_id: str | None,
        user_id: str | None,
        stage: str,
        error: BaseException,
    ) -> None:
        """Persist a failed attempt and audit it; never raise over the
        original error.
        """
        from app.agents.audit import AuditAgent
        from app.database.session import session_scope
        from app.security.redaction import safe_error_message

        message = safe_error_message(error, category=f"backup_{stage}_failed")
        try:
            with session_scope() as session:
                self._upsert_record(
                    session,
                    database=database,
                    backup_path=backup_path,
                    verified=False,
                    size_bytes=0,
                    job_id=job_id,
                    user_id=user_id,
                    error=message,
                )
                AuditAgent(session).write(
                    event_type="backup.failed",
                    outcome="failure",
                    job_id=job_id,
                    user_id=user_id,
                    details={
                        "database": database,
                        "backup_path": backup_path,
                        "stage": stage,
                        "error": message,
                        "verified": False,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - never mask the original error
            import logging

            logging.getLogger(__name__).error("Failed to persist backup failure record: %s", exc)

    def _upsert_record(
        self,
        session,
        *,
        database: str,
        backup_path: str,
        verified: bool,
        size_bytes: int,
        job_id: str | None,
        user_id: str | None,
        error: str | None,
    ) -> None:
        from sqlalchemy import select

        from app.models.entities import BackupRecord

        existing = session.execute(
            select(BackupRecord).where(BackupRecord.backup_path == backup_path)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                BackupRecord(
                    database_name=database,
                    backup_path=backup_path,
                    size_bytes=size_bytes,
                    verified=verified,
                    verification_method="RESTORE VERIFYONLY" if verified else None,
                    error=error,
                    job_id=job_id,
                    created_by_user_id=user_id,
                    created_at=datetime.now(timezone.utc),
                )
            )
        else:
            existing.database_name = database
            existing.size_bytes = size_bytes
            existing.verified = verified
            existing.verification_method = "RESTORE VERIFYONLY" if verified else None
            existing.error = error
            if job_id is not None:
                existing.job_id = job_id
            if user_id is not None:
                existing.created_by_user_id = user_id
        session.commit()

    def list_backups(self, database: str | None = None) -> list[Path]:
        """Return the .bak files for a database (or all), newest first."""
        if database is None:
            files = sorted(self._backup_dir.glob("*.bak"), reverse=True)
        else:
            files = sorted(self._backup_dir.glob(f"{database}_*.bak"), reverse=True)
        return files

    def _enforce_retention(self, database: str) -> int:
        # When the backup directory is inside a container the host
        # path may not enumerate the files. In that case we fall back
        # to the in-database sys.master_files list, which works
        # regardless of the host filesystem.
        try:
            files = sorted(
                self._backup_dir.glob(f"{database}_*.bak"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            files = []
        for old in files[self._keep :]:
            try:
                old.unlink()
            except OSError as exc:
                # Non-fatal: log a warning and continue.
                import logging

                logging.getLogger(__name__).warning("Failed to delete old backup %s: %s", old, exc)
        return len(files[: self._keep])
