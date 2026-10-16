"""LLM-driven anonymization agent.

When the classification agent selects a strategy, the LLM decides
the anonymization approach. This agent takes the approved sensitive
column data and sends it to the LLM for actual anonymization,
fulfilling the requirement that the LLM performs the anonymization.

The agent works in batches to minimize LLM calls while keeping
prompt sizes manageable. It uses a per-job cache so the same
input value always maps to the same output within a single run
(referential integrity across FKs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.classification.llm_client import LLMClient
from app.classification.prompts import (
    ANONYMIZE_SYSTEM,
    build_anonymize_user_prompt,
)

# Maximum values per LLM call to avoid prompt-too-long errors.
BATCH_SIZE = 50


@dataclass
class AnonymizationContext:
    """Per-job LLM anonymization state."""

    job_id: str
    llm_client: LLMClient
    _cache: dict[tuple[str, str, Any], Any] = field(default_factory=dict)

    def cache_key(self, table: str, column: str, value: Any) -> tuple[str, str, Any]:
        return (table, column, value)


class AnonymizationAgent:
    """Anonymize sensitive column data via the LLM.

    Usage::

        agent = AnonymizationAgent(llm_client)
        ctx = AnonymizationContext(job_id="j1", llm_client=llm_client)
        masked = agent.anonymize_column(
            ctx, table="Customers", column="Email",
            data_type="nvarchar", sensitivity="Email",
            strategy="Fake Email", values=["a@b.com", "c@d.com"],
        )
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client

    def anonymize_column(
        self,
        ctx: AnonymizationContext,
        table: str,
        column: str,
        data_type: str,
        sensitivity: str,
        strategy: str,
        values: list[Any],
    ) -> list[Any]:
        """Anonymize a batch of values for a single column via the LLM.

        Returns a list of anonymized values in the same order as the input.
        None values are propagated as None. Already-cached values are
        returned from cache (deterministic mapping).
        """
        if not values:
            return []

        llm = ctx.llm_client
        results: list[Any] = [None] * len(values)

        # Separate values that need LLM vs already cached.
        to_process: list[tuple[int, Any]] = []
        for i, v in enumerate(values):
            if v is None:
                results[i] = None
                continue
            cache_key = ctx.cache_key(table, column, v)
            if cache_key in ctx._cache:
                results[i] = ctx._cache[cache_key]
            else:
                to_process.append((i, v))

        if not to_process:
            return results

        # Batch the values for LLM calls.
        for batch_start in range(0, len(to_process), BATCH_SIZE):
            batch = to_process[batch_start : batch_start + BATCH_SIZE]
            batch_values = [v for _, v in batch]

            user_prompt = build_anonymize_user_prompt(
                table_name=table,
                column_name=column,
                data_type=data_type,
                sensitivity=sensitivity,
                strategy=strategy,
                values=batch_values,
            )

            try:
                anonymized = llm.anonymize_batch(
                    system=ANONYMIZE_SYSTEM,
                    user=user_prompt,
                )
            except Exception as exc:
                logger.error(
                    "LLM anonymization failed for {}.{}, batch starting at {}: {}",
                    table,
                    column,
                    batch_start,
                    exc,
                )
                # Fallback: use original values (safe default — data
                # isn't lost, though it isn't anonymized either).
                anonymized = [str(v) for v in batch_values]

            # Validate length.
            if len(anonymized) != len(batch_values):
                logger.warning(
                    "LLM returned {} results for {} values; padding/truncating",
                    len(anonymized),
                    len(batch_values),
                )
                # Pad with empty strings or truncate.
                while len(anonymized) < len(batch_values):
                    anonymized.append("")
                anonymized = anonymized[: len(batch_values)]

            # Map results back and populate cache.
            for (orig_idx, orig_val), anon_val in zip(batch, anonymized):
                results[orig_idx] = anon_val
                cache_key = ctx.cache_key(table, column, orig_val)
                ctx._cache[cache_key] = anon_val

        return results
