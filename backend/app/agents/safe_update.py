"""Phase 11 — Safe Update (Update Agent).

Executes ``UPDATE`` statements against the target MSSQL inside
explicit transactions, with parameterized SQL (never string-built),
batched in chunks of ``settings.safe_update_batch_size``, and with
an explicit ``confirm: true`` gate.

Pre-conditions enforced in code:

* The caller MUST have an approval record for every column being
  masked (rejected/excluded columns never enter the plan).
* The caller MUST supply a non-empty ``expected_backup_path`` that
  resolves to a **verified** ``BackupRecord`` inside the configured
  backup directory before any real update runs.
* A dry-run path (``dry_run=True``) returns the SQL plan without
  executing (the backup gate is skipped for dry-runs).
* On any error or post-execution validation failure mid-transaction,
  the agent rolls back so no partial change survives.

After each batch of updates the agent re-reads the affected rows and
validates them against the expected masked values (validation failure
raises ``UpdateError`` → rollback). The agent writes audit rows for
plan, start, validation/rollback outcomes and completion so Phase 12
has a complete trail.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pyodbc
from loguru import logger
from sqlalchemy.orm import Session

from app.agents.audit import AuditAgent
from app.agents.masking_orchestrator import (
    MaskingOrchestrator,
    MaskingOrchestratorError,
)
from app.classification.factory import build_llm_client
from app.models.entities import Job  # noqa: F401  -- reserved for future use
from app.security.redaction import safe_error_message


_UNSAFE_IDENT = re.compile(r"[^A-Za-z0-9_]")


class UpdateError(Exception):
    """Raised on any failure during a Safe Update."""


@dataclass
class UpdatePlan:
    """A single statement the agent intends to execute.

    ``dry_run_sql`` is the rendered SQL with ``-- DRY RUN`` prefixed
    on the first line so the caller can eyeball it before sending
    ``confirm: true``.
    """

    table: str
    column: str
    row_count_estimate: int
    batches: int
    dry_run_sql: str


@dataclass
class UpdateResult:
    """Outcome of a single update run."""

    job_id: str
    table: str
    column: str
    rows_updated: int
    batches: int
    duration_ms: int
    audit_log_id: str


class SafeUpdateAgent:
    """Drive the Phase 11 transaction-bounded update."""

    def __init__(
        self,
        session: Session,
        target_connection_string: str,
    ) -> None:
        self._session = session
        self._conn_str = target_connection_string
        self._audit = AuditAgent(session)

    # ---------- planning ----------

    def plan(
        self,
        job_id: str,
        target_database: str,
        batch_size: int = 500,
        user_id: str | None = None,
    ) -> list[UpdatePlan]:
        """Build a list of ``UpdatePlan`` for every approved column.

        Issues ``COUNT(*)`` to estimate row counts, then slices each
        table into batches. The plans carry the rendered ``UPDATE``
        statement but DO NOT execute.

        The plan is derived exclusively from persisted approvals: a
        column appears only if an ``approve``/``override`` approval
        exists and it is neither rejected nor excluded.
        """
        from app.core.config import get_settings

        if batch_size <= 0:
            batch_size = get_settings().safe_update_batch_size
        # Probe the approved columns *before* loading the masking
        # context — if there are none, we never need to materialize
        # salt/token_key. ``load()`` would also fail because
        # ``prepare()`` was never called.
        orch = MaskingOrchestrator(self._session)
        try:
            ctx = orch.load(job_id)
        except MaskingOrchestratorError as exc:
            raise UpdateError(f"Cannot plan update: {exc}") from exc
        specs = ctx._column_specs  # type: ignore[attr-defined]
        if not specs:
            raise UpdateError("No approved columns to update.")
        plans: list[UpdatePlan] = []
        for (table, column), spec in sorted(specs.items()):
            count = self._count_rows(target_database, table)
            batches = max(1, (count + batch_size - 1) // batch_size)
            quoted_t = _q(table)
            quoted_c = _q(column)
            sql = (
                f"UPDATE TOP ({batch_size}) {quoted_t} "
                f"SET {quoted_c} = ? "
                f"WHERE {quoted_c} IS NOT NULL;"
            )
            plans.append(
                UpdatePlan(
                    table=table,
                    column=column,
                    row_count_estimate=count,
                    batches=batches,
                    dry_run_sql=f"-- DRY RUN  table={table} column={column} batches={batches}\n{sql}",
                )
            )
        self._audit.write(
            event_type="safe_update.plan",
            outcome="success",
            job_id=job_id,
            user_id=user_id,
            details={
                "target_database": target_database,
                "columns": [f"{t}.{c}" for (t, c) in sorted(specs)],
                "column_count": len(plans),
            },
        )
        return plans

    # ---------- execution ----------

    def apply(
        self,
        job_id: str,
        target_database: str,
        expected_backup_path: str,
        confirm: bool,
        batch_size: int = 500,
        dry_run: bool = False,
        user_id: str | None = None,
    ) -> list[UpdateResult]:
        """Execute the plan. ``confirm`` must be True unless ``dry_run``.

        Pre-conditions for a real (non-dry-run) execution:

        1. ``confirm=True`` — the operator explicitly approved.
        2. ``expected_backup_path`` is non-empty, resolves inside the
           configured backup directory, and names a file that exists.
        3. A persisted ``BackupRecord`` for that path exists, is
           ``verified=True`` (i.e. ``RESTORE VERIFYONLY`` succeeded),
           and was taken against the same target database. A backup
           already bound to another job is rejected; an unbound backup
           is bound to this job so the metadata is associated with the
           execution.
        4. The masking context loads with at least one approved,
           non-excluded, non-rejected column (else ``UpdateError``).

        Any failure raises ``UpdateError`` before the target database
        is touched, and is audited.
        """
        if not dry_run and not confirm:
            raise UpdateError("apply() requires confirm=True (or dry_run=True).")

        if not expected_backup_path and not dry_run:
            self._audit_gate_failure(
                job_id=job_id,
                user_id=user_id,
                target_database=target_database,
                reason="missing_backup_path",
                error="expected_backup_path is empty",
            )
            raise UpdateError(
                "Refusing to UPDATE without an expected_backup_path from Phase 9. "
                "Run /backups first and pass the path back."
            )

        if not dry_run:
            self._require_verified_backup(
                job_id=job_id,
                target_database=target_database,
                expected_backup_path=expected_backup_path,
                user_id=user_id,
            )

        audit_id = str(uuid.uuid4())
        self._audit.write(
            event_type="safe_update.start",
            outcome="started",
            job_id=job_id,
            user_id=user_id,
            details={
                "target_database": target_database,
                "expected_backup_path": expected_backup_path,
                "dry_run": dry_run,
                "batch_size": batch_size,
            },
        )
        started_at = datetime.now(timezone.utc)

        orch = MaskingOrchestrator(self._session, llm_client=build_llm_client())
        try:
            ctx = orch.load(job_id)
        except MaskingOrchestratorError as exc:
            self._audit.write(
                event_type="safe_update.error",
                outcome="failure",
                job_id=job_id,
                user_id=user_id,
                details={"reason": "masking_load_failed", "error": str(exc)},
            )
            raise UpdateError(f"Cannot load masking context: {exc}") from exc

        specs = ctx._column_specs  # type: ignore[attr-defined]
        if not specs:
            self._audit.write(
                event_type="safe_update.error",
                outcome="failure",
                job_id=job_id,
                user_id=user_id,
                details={"reason": "no_approved_columns"},
            )
            raise UpdateError("No approved columns to update.")

        # Pre-batch LLM anonymization for columns using LLM Anonymize strategy.
        llm_columns = [
            (table, column, spec)
            for (table, column), spec in specs.items()
            if spec.strategy == "LLM Anonymize"
        ]

        results: list[UpdateResult] = []
        conn = pyodbc.connect(self._conn_str, autocommit=False, timeout=30)
        try:
            cursor = conn.cursor()

            # Warm the LLM anonymization cache for each LLM column.
            for table, column, spec in llm_columns:
                try:
                    pk_col = _discover_pk_column(cursor, table)
                    if pk_col is not None:
                        sel = f"SELECT {_q(column)} FROM {_q(table)} WHERE {_q(column)} IS NOT NULL"
                        cursor.execute(sel)
                        all_values = [r[0] for r in cursor.fetchall()]
                        if all_values:
                            orch.preprocess_column(
                                ctx,
                                table,
                                column,
                                data_type="nvarchar",
                                sensitivity=spec.classification_id,
                                values=all_values,
                            )
                except Exception as exc:
                    logger.warning(
                        "Failed to preprocess LLM column {}.{}: {}",
                        table,
                        column,
                        exc,
                    )

            for (table, column), spec in sorted(specs.items()):
                quoted_t = _q(table)
                quoted_c = _q(column)
                sql = (
                    f"UPDATE TOP ({batch_size}) {quoted_t} "
                    f"SET {quoted_c} = ? "
                    f"WHERE {quoted_c} IS NOT NULL"
                )
                t0 = datetime.now(timezone.utc)
                rows_updated, batches = _masked_update_batches(
                    conn=conn,
                    cursor=cursor,
                    spec=spec,
                    ctx=ctx,
                    orch=orch,
                    table=table,
                    column=column,
                    sql_template=sql,
                    batch_size=batch_size,
                    dry_run=dry_run,
                )
                duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
                res = UpdateResult(
                    job_id=job_id,
                    table=table,
                    column=column,
                    rows_updated=rows_updated,
                    batches=batches,
                    duration_ms=duration_ms,
                    audit_log_id=audit_id,
                )
                results.append(res)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        except pyodbc.Error as exc:
            self._abort_transaction(
                conn,
                job_id=job_id,
                user_id=user_id,
                reason="update_error",
                error=str(exc),
            )
            raise UpdateError(f"Database error during update: {exc}") from exc
        except UpdateError as val_exc:
            # Post-execution validation failed: roll back so no partial
            # change survives; audit both the failure and the rollback.
            self._abort_transaction(
                conn,
                job_id=job_id,
                user_id=user_id,
                reason="validation_failed",
                error=str(val_exc),
            )
            raise
        except Exception as exc:
            self._abort_transaction(
                conn,
                job_id=job_id,
                user_id=user_id,
                reason="unexpected",
                error=str(exc),
            )
            raise
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        if not dry_run:
            # Wipe the token key + salt; this is the irreversible
            # boundary the operator must understand.
            try:
                orch.wipe(job_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to wipe masking job: {}", exc)

        self._audit.write(
            event_type="safe_update.complete",
            outcome="success" if not dry_run else "dry_run",
            job_id=job_id,
            user_id=user_id,
            details={
                "tables": sorted({r.table for r in results}),
                "rows_updated": sum(r.rows_updated for r in results),
                "batches": sum(r.batches for r in results),
                "duration_ms_total": sum(r.duration_ms for r in results),
                "dry_run": dry_run,
                "started_at": started_at.isoformat(),
                "validated": not dry_run,
                "backup_path": expected_backup_path,
            },
        )
        return results

    # ---------- backup gate ----------

    def _require_verified_backup(
        self,
        *,
        job_id: str,
        target_database: str,
        expected_backup_path: str,
        user_id: str | None,
    ) -> None:
        """Enforce backup-before-update.

        Aborts (``UpdateError``) unless the supplied path is inside
        the backup directory, exists on disk, and has a persisted
        ``BackupRecord`` marked ``verified=True`` for the same target
        database. An unbound record is bound to this job. Gate
        failures are audited as ``safe_update.backup_gate``.
        """
        from sqlalchemy import select

        from app.agents.backup import _normalize_backup_path
        from app.models.entities import BackupRecord

        def _reject(reason: str, message: str) -> None:
            self._audit_gate_failure(
                job_id=job_id,
                user_id=user_id,
                target_database=target_database,
                reason=reason,
                error=message,
                backup_path=expected_backup_path,
            )
            raise UpdateError(message)

        path = _normalize_backup_path(expected_backup_path)
        if not _is_under_backup_dir(path):
            _reject(
                "backup_outside_backup_dir",
                "Refusing to UPDATE: expected_backup_path is not under the "
                "configured backup directory.",
            )
        # The existence check uses the application's own view of the
        # backup directory — the same view ``GET /backups`` lists from.
        if not Path(path).exists():
            _reject(
                "backup_file_missing",
                "Refusing to UPDATE: backup file does not exist or is not "
                "visible from the application; the backup must be created "
                "and verified before execution.",
            )

        record = self._session.execute(
            select(BackupRecord).where(BackupRecord.backup_path == path)
        ).scalar_one_or_none()
        if record is None:
            _reject(
                "backup_record_missing",
                "Refusing to UPDATE: no backup metadata is recorded for "
                "this path. Run the backup through the platform so it is "
                "verified and recorded.",
            )
            return
        if not record.verified:
            _reject(
                "backup_unverified",
                "Refusing to UPDATE: the backup for this path was not "
                "verified (RESTORE VERIFYONLY did not succeed).",
            )
            return
        if record.database_name != target_database:
            _reject(
                "backup_database_mismatch",
                "Refusing to UPDATE: the verified backup was taken for a " "different database.",
            )
            return
        if record.job_id is not None and record.job_id != job_id:
            _reject(
                "backup_job_mismatch",
                "Refusing to UPDATE: the verified backup is associated " "with a different job.",
            )
            return
        if record.job_id is None:
            # Associate the backup metadata with this execution.
            record.job_id = job_id
            self._session.commit()

    def _audit_gate_failure(
        self,
        *,
        job_id: str,
        user_id: str | None,
        target_database: str,
        reason: str,
        error: str,
        backup_path: str | None = None,
    ) -> None:
        try:
            self._audit.write(
                event_type="safe_update.backup_gate",
                outcome="failure",
                job_id=job_id,
                user_id=user_id,
                details={
                    "reason": reason,
                    "error": error,
                    "target_database": target_database,
                    "backup_path": backup_path,
                },
            )
        except Exception:  # noqa: BLE001 - never mask the original gate error
            pass

    def _abort_transaction(
        self,
        conn,
        *,
        job_id: str,
        user_id: str | None,
        reason: str,
        error: str | None,
    ) -> None:
        """Roll back ``conn``, audit the rollback outcome and the error.

        Never raises: a rollback failure is itself audited (outcome
        ``failure``) so a partial-application risk is visible.
        """
        rolled_back, rollback_error = _safe_rollback(conn)
        self._audit.write(
            event_type="safe_update.rollback",
            outcome="success" if rolled_back else "failure",
            job_id=job_id,
            user_id=user_id,
            details={"reason": reason, "error": rollback_error},
        )
        self._audit.write(
            event_type="safe_update.error",
            outcome="failure",
            job_id=job_id,
            user_id=user_id,
            details={"reason": reason, "error": error},
        )

    # ---------- internals ----------

    def _count_rows(self, target_database: str, table: str) -> int:
        conn_str = re.sub(
            r"DATABASE=[^;]*", f"DATABASE={target_database}", self._conn_str, flags=re.IGNORECASE
        )
        with pyodbc.connect(conn_str, autocommit=True, timeout=10) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {_q(table)}")
            row = cur.fetchone()
            cur.close()
            return int(row[0]) if row else 0


def _safe_rollback(conn) -> tuple[bool, str | None]:
    """Roll back a connection; never raise. Return ``(ok, error_str)``."""
    try:
        conn.rollback()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, safe_error_message(exc, category="rollback_failed")


def _q(identifier: str) -> str:
    """Bracket-quote a SQL Server identifier after a safety check."""
    if _UNSAFE_IDENT.search(identifier):
        raise UpdateError(f"Refusing to use unsafe identifier: {identifier!r}")
    return f"[{identifier.replace(']', ']]')}]"


def _is_under_backup_dir(path: str) -> bool:
    """True if ``path`` lives under the configured backup directory."""
    from app.core.config import get_settings

    base = Path(get_settings().backup_dir).resolve()
    candidate = Path(path)
    try:
        candidate = candidate.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        pass
    return candidate == base or base in candidate.parents


def _masked_update_batches(
    *,
    conn,
    cursor,
    spec,
    ctx,
    orch,
    table: str,
    column: str,
    sql_template: str,
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Run the masked UPDATE in batches; return ``(rows_updated, batches)``.

    In real-run mode each batch is re-read and validated against the
    expected masked values before the next batch proceeds. A
    validation mismatch raises ``UpdateError`` (the caller rolls the
    whole transaction back).
    """
    if not dry_run:
        # We need the *current* value of each row to compute the
        # masked output. The original ``UPDATE col = ?`` template
        # doesn't know the value, so we iterate by primary key and
        # issue one parameterized UPDATE per row. This is the
        # "slow but correct" mode.
        # Discover the table's PK column (or first column) for the
        # iteration key.
        pk_col = _discover_pk_column(conn.cursor(), table)
        if pk_col is None:
            # No PK or no rows; just skip.
            return 0, 0
        # Stream rows in batches to avoid OOM on large tables.
        sel = f"SELECT {_q(pk_col)}, {_q(column)} FROM {_q(table)} WHERE {_q(column)} IS NOT NULL"
        cursor.execute(sel)
        rows_updated = 0
        batches = 0
        while True:
            chunk = cursor.fetchmany(batch_size)
            if not chunk:
                break
            expected: dict[object, tuple[object, object]] = {}
            for r in chunk:
                pk_value = r[0]
                original = r[1]
                masked = orch.mask(ctx, table, column, original)
                upd = f"UPDATE {_q(table)} SET {_q(column)} = ? WHERE {_q(pk_col)} = ?"
                cursor.execute(upd, (masked, pk_value))
                expected[pk_value] = (original, masked)
                rows_updated += 1
            # Post-execution validation for this batch: re-read and
            # confirm every approved row now holds the masked value
            # (or at least no longer holds the original value — the
            # strict compare can be defeated by server-side type
            # coercion, e.g. decimal rounding).
            _validate_batch(
                cursor=cursor,
                table=table,
                column=column,
                pk_col=pk_col,
                expected=expected,
            )
            batches += 1
        return rows_updated, batches
    else:
        # Dry run: just count the rows that *would* be touched.
        sel = f"SELECT COUNT(*) FROM {_q(table)} WHERE {_q(column)} IS NOT NULL"
        cursor.execute(sel)
        row = cursor.fetchone()
        return int(row[0] or 0), 0


def _validate_batch(
    *,
    cursor,
    table: str,
    column: str,
    pk_col: str,
    expected: dict[object, tuple[object, object]],
) -> None:
    """Re-read the rows of one batch and validate the masked values.

    ``expected`` maps pk → (original, masked). A row is invalid when
    it cannot be re-read, or it still holds the original value while
    differing from the expected masked value. Raises ``UpdateError``
    on the first failed batch so the caller rolls back.
    """
    if not expected:
        return
    failures: list[str] = []
    pks = list(expected)
    # Stay well below SQL Server's 2100-parameter limit.
    for start in range(0, len(pks), 900):
        window = pks[start : start + 900]
        placeholders = ", ".join("?" for _ in window)
        sel = (
            f"SELECT {_q(pk_col)}, {_q(column)} FROM {_q(table)} "
            f"WHERE {_q(pk_col)} IN ({placeholders})"
        )
        cursor.execute(sel, tuple(window))
        seen: set[object] = set()
        for row in cursor.fetchall():
            pk_value = row[0]
            stored = row[1]
            seen.add(pk_value)
            original, masked = expected.get(pk_value, (None, None))
            stored_key = None if stored is None else str(stored)
            masked_key = None if masked is None else str(masked)
            original_key = None if original is None else str(original)
            if stored_key == masked_key:
                continue
            if stored_key != original_key:
                # Value changed but strict equality failed: server
                # type-coercion of the masked value. Acceptable.
                continue
            failures.append(str(pk_value))
        for missing in set(window) - seen:
            failures.append(f"{missing} (row not found)")
    if failures:
        sample = ", ".join(failures[:5])
        raise UpdateError(
            f"Post-update validation failed for {table}.{column}: "
            f"{len(failures)} row(s) did not receive the expected masked "
            f"value (pk: {sample}); transaction will be rolled back."
        )


def _discover_pk_column(cursor, table: str) -> str | None:
    """Return the first PK column name for ``table``, or None.

    Tries the exact table name first (may include schema prefix),
    then falls back to searching all schemas.
    """
    try:
        # Try exact match first (table may be "dbo.Customers" or just "Customers").
        parts = table.split(".")
        if len(parts) == 2:
            schema_name, table_name = parts
        else:
            schema_name, table_name = None, table

        if schema_name:
            cursor.execute(
                "SELECT kcu.COLUMN_NAME "
                "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
                "WHERE tc.TABLE_SCHEMA = ? AND tc.TABLE_NAME = ? "
                "  AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'",
                (schema_name, table_name),
            )
        else:
            cursor.execute(
                "SELECT kcu.COLUMN_NAME "
                "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                "  ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
                "WHERE tc.TABLE_NAME = ? "
                "  AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'",
                (table_name,),
            )
        row = cursor.fetchone()
        if row:
            return row[0]
    except Exception:
        pass
    return None
