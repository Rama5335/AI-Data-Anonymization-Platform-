"""Masking orchestrator (Phase 10).

Builds a per-job ``MaskingContext`` from approved decisions, then
offers a single ``mask(value, table, column)`` method that:
  * looks up the strategy for that column (or the FK-propagated one),
  * calls the strategy from ``app.agents.masking``,
  * records Tokenization reversibility into ``token_vault``,
  * caches the mapping so the same input maps to the same output
    across the PK and every FK pointing to it (referential integrity).

Wipe semantics:
  * ``wipe()`` zeroes the in-memory token key + salt and records
    ``wiped_at`` on the ``masking_jobs`` row. Subsequent calls raise.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.masking import (
    MaskingContext,
    SUPPORTED_STRATEGIES,
    generate_salt,
    generate_token_key,
    mask_value,
)
from app.classification.llm_client import LLMClient
from app.core.config import get_settings
from app.models.entities import (
    Approval,
    ClassificationResult,
    DiscoveredColumn,
    DiscoveredForeignKey,
    DiscoveredTable,
    MaskingJob,
    TokenVault,
)


class MaskingOrchestratorError(Exception):
    pass


@dataclass
class ColumnSpec:
    """The resolved masking spec for a single (table, column) pair."""

    table: str
    column: str
    strategy: str
    classification_id: str


class MaskingOrchestrator:
    """Build and use a per-job ``MaskingContext``."""

    _lock = threading.Lock()

    def __init__(self, session: Session, llm_client: LLMClient | None = None) -> None:
        self._session = session
        self._fernet = _build_fernet()
        self._llm_client = llm_client
        self._anon_agent: AnonymizationAgent | None = None
        if llm_client is not None:
            from app.agents.anonymization import AnonymizationAgent

            self._anon_agent = AnonymizationAgent(llm_client)

    # ---------- public API ----------

    def prepare(self, job_id: str) -> MaskingContext:
        """Materialize the job's salt + token key, persist them, return the context."""
        existing = self._existing_for_job(job_id)
        if existing is not None:
            # Idempotent: a re-prepare returns the same context.
            return self.load(job_id)
        salt = generate_salt()
        token_key = generate_token_key()
        row = MaskingJob(
            job_id=job_id,
            salt_encrypted=self._fernet.encrypt(salt),
            token_key_encrypted=self._fernet.encrypt(token_key),
        )
        self._session.add(row)
        self._session.commit()
        return self.load(job_id)

    def load(self, job_id: str) -> MaskingContext:
        row = self._existing_for_job(job_id)
        if row is None:
            raise MaskingOrchestratorError(
                f"No masking job for job_id {job_id!r}; call prepare() first."
            )
        if row.wiped_at is not None:
            raise MaskingOrchestratorError(
                f"Masking job {job_id!r} was wiped at {row.wiped_at.isoformat()}; "
                "create a new job to mask again."
            )
        try:
            salt = self._fernet.decrypt(row.salt_encrypted)
            token_key = self._fernet.decrypt(row.token_key_encrypted)
        except InvalidToken as exc:
            raise MaskingOrchestratorError(
                "Cannot decrypt masking job secrets; check CREDENTIAL_ENCRYPTION_KEY."
            ) from exc
        # Build the column → strategy map from approvals + classification
        # results, then propagate PK strategy to all referencing FKs.
        spec_by_table_col: dict[tuple[str, str], ColumnSpec] = self._build_column_specs(job_id)
        propagated = self._propagate_pk_to_fks(spec_by_table_col, job_id)
        ctx = MaskingContext(
            job_id=job_id,
            salt=salt,
            token_key=token_key,
        )
        # Stash the propagated specs on the context so mask() can look up
        # the right strategy in O(1).
        ctx._column_specs = dict(propagated)  # type: ignore[attr-defined]
        return ctx

    def mask(
        self,
        ctx: MaskingContext,
        table: str,
        column: str,
        value: Any,
    ) -> Any:
        spec = ctx._column_specs.get((table, column))  # type: ignore[attr-defined]
        if spec is None:
            # Not approved for masking: return value unchanged.
            return value
        if spec.strategy == "LLM Anonymize":
            # Check if the LLM agent has a cached result for this value.
            anon_ctx = getattr(ctx, "_anonymization_ctx", None)
            if anon_ctx is not None:
                cache_key = anon_ctx.cache_key(table, column, value)
                cached = anon_ctx._cache.get(cache_key)
                if cached is not None:
                    return cached
            # Fallback: use the LLM agent for this single value.
            if self._anon_agent is not None and value is not None:
                anon_ctx_local = getattr(ctx, "_anonymization_ctx", None)
                if anon_ctx_local is None:
                    from app.agents.anonymization import AnonymizationContext

                    anon_ctx_local = AnonymizationContext(
                        job_id=ctx.job_id, llm_client=self._llm_client
                    )
                results = self._anon_agent.anonymize_column(
                    anon_ctx_local,
                    table=table,
                    column=column,
                    data_type="nvarchar",
                    sensitivity=spec.classification_id,
                    strategy="LLM Anonymize",
                    values=[value],
                )
                return results[0] if results else value
            # No LLM client available: fall back to Preserve Format.
            return mask_value(value, "Preserve Format", column, ctx)
        masked = mask_value(value, spec.strategy, column, ctx)
        if spec.strategy == "Tokenization" and value is not None:
            self._record_token_entry(ctx.job_id, table, column, str(value), str(masked))
        return masked

    def preprocess_column(
        self,
        ctx: MaskingContext,
        table: str,
        column: str,
        data_type: str,
        sensitivity: str,
        values: list[Any],
    ) -> None:
        """Pre-batch LLM anonymization for a full column.

        Call this before the row-by-row loop in safe_update to warm
        the anonymization cache. For non-LLM strategies, this is a no-op.
        """
        spec = ctx._column_specs.get((table, column))  # type: ignore[attr-defined]
        if spec is None or spec.strategy != "LLM Anonymize":
            return
        if self._anon_agent is None:
            logger.warning(
                "LLM Anonymize strategy for {}.{}, but no LLM client configured; "
                "falling back to Preserve Format.",
                table,
                column,
            )
            return
        from app.agents.anonymization import AnonymizationContext

        anon_ctx = AnonymizationContext(job_id=ctx.job_id, llm_client=self._llm_client)
        # Copy any existing cache entries from a prior run.
        existing = getattr(ctx, "_anonymization_ctx", None)
        if existing is not None:
            anon_ctx._cache = dict(existing._cache)
        self._anon_agent.anonymize_column(
            anon_ctx,
            table=table,
            column=column,
            data_type=data_type,
            sensitivity=sensitivity,
            strategy="LLM Anonymize",
            values=values,
        )
        # Stash the anon context on the masking context for mask() to use.
        ctx._anonymization_ctx = anon_ctx  # type: ignore[attr-defined]

    def wipe(self, job_id: str) -> None:
        row = self._existing_for_job(job_id)
        if row is None:
            return
        from datetime import datetime, timezone

        row.wiped_at = datetime.now(timezone.utc)
        # Best-effort re-encrypt the column to garbage. This makes the
        # bytes at rest undecryptable even with the current key.
        row.salt_encrypted = b"\x00" * len(row.salt_encrypted)
        row.token_key_encrypted = b"\x00" * len(row.token_key_encrypted)
        self._session.commit()

    def _existing_for_job(self, job_id: str) -> MaskingJob | None:
        return self._session.execute(
            select(MaskingJob).where(MaskingJob.job_id == job_id)
        ).scalar_one_or_none()

    # ---------- internals ----------

    def _build_column_specs(self, job_id: str) -> dict[tuple[str, str], ColumnSpec]:
        approvals = list(
            self._session.execute(
                select(Approval)
                .join(
                    ClassificationResult,
                    Approval.classification_result_id == ClassificationResult.id,
                )
                .where(ClassificationResult.job_id == job_id)
            ).scalars()
        )
        out: dict[tuple[str, str], ColumnSpec] = {}
        for a in approvals:
            r = self._session.get(ClassificationResult, a.classification_result_id)
            if r is None or a.excluded:
                continue
            if a.decision == "reject":
                continue
            strategy = a.masking_strategy or r.suggested_strategy
            if strategy not in SUPPORTED_STRATEGIES:
                logger.warning(
                    "Approval %s has unsupported strategy %r; falling back to Preserve Format",
                    a.id,
                    strategy,
                )
                strategy = "Preserve Format"
            out[(r.table_name, r.column_name)] = ColumnSpec(
                table=r.table_name,
                column=r.column_name,
                strategy=strategy,
                classification_id=r.id,
            )
        return out

    def _propagate_pk_to_fks(
        self,
        specs: dict[tuple[str, str], ColumnSpec],
        job_id: str,
    ) -> dict[tuple[str, str], ColumnSpec]:
        """For every approved PK, copy its spec to every column that
        references it via a discovered FK. The result keeps the
        original PK spec; FKs are added only when they don't already
        have a more specific spec.
        """
        # Load all FKs across this job's tables.
        fks = list(
            self._session.execute(
                select(DiscoveredForeignKey)
                .join(DiscoveredColumn, DiscoveredForeignKey.column_id == DiscoveredColumn.id)
                .join(
                    DiscoveredTable,
                    DiscoveredColumn.table_id == DiscoveredTable.id,
                )
                .where(DiscoveredTable.job_id == job_id)
            ).scalars()
        )
        # Build a lookup: (table, column) → spec.
        out = dict(specs)
        for fk in fks:
            col = self._session.get(DiscoveredColumn, fk.column_id)
            if col is None:
                continue
            pk_key = (fk.referenced_table, fk.referenced_column)
            if pk_key in specs and (col.table_id, col.name) not in out:
                pk_spec = specs[pk_key]
                # Resolve the FK's table name.
                tbl = self._session.get(DiscoveredTable, col.table_id)
                if tbl is None:
                    continue
                out[(tbl.table_name, col.name)] = ColumnSpec(
                    table=tbl.table_name,
                    column=col.name,
                    strategy=pk_spec.strategy,
                    classification_id=pk_spec.classification_id,
                )
        return out

    def _record_token_entry(
        self,
        job_id: str,
        table: str,
        column: str,
        original: str,
        tokenized: str,
    ) -> None:
        existing = self._session.execute(
            select(TokenVault).where(
                TokenVault.job_id == job_id,
                TokenVault.table_name == table,
                TokenVault.column_name == column,
                TokenVault.tokenized_value == tokenized,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        self._session.add(
            TokenVault(
                job_id=job_id,
                table_name=table,
                column_name=column,
                tokenized_value=tokenized,
                original_value=original,
            )
        )
        self._session.commit()


def _build_fernet() -> Fernet:
    """Derive a Fernet key from ``CREDENTIAL_ENCRYPTION_KEY``.

    The settings value is a base64-encoded 32-byte secret. Fernet
    wants a url-safe base64-encoded 32-byte key. We accept either
    form (raw 32 bytes or a Fernet-shaped key) and normalize.
    """
    raw = get_settings().credential_encryption_key.get_secret_value()
    try:
        # If it's already a Fernet key (44 chars, urlsafe b64), use it.
        if len(raw) == 44 and raw.endswith("="):
            return Fernet(raw.encode("ascii"))
    except Exception:
        pass
    # Treat the raw value as a 32-byte key: pad or truncate.
    key_bytes = raw.encode("utf-8")[:32].ljust(32, b"0")
    import base64

    return Fernet(base64.urlsafe_b64encode(key_bytes))
