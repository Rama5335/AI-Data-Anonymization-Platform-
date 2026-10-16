"""Classification agent.

Drives the LLM-based classification of discovered columns. For each
column:

1. Fetch the column metadata (name, data type, nullable, table).
2. If a ``DiscoveredSample`` exists for the table, pull the per-cell
   values for that column. (The sample is already capped at 512 bytes
   per cell in Phase 5, so the LLM never sees a 5 MB LOB.)
3. Build the user prompt, call the LLM via
   :func:`app.classification.parser.classify_column`, and validate
   the response (with retries on malformed JSON).
4. Upsert the result in ``classification_results`` (unique by
   ``(job_id, schema_name, table_name, column_name)``). Failures are
   persisted with ``ok=False`` and the error text, so the UI can
   surface them instead of silently dropping the column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.classification.llm_client import LLMClient
from app.classification.parser import (
    ClassificationFailure,
    ClassificationSuccess,
    classify_column as parse_classify_column,
)
from app.models.entities import (
    ClassificationResult,
    DiscoveredColumn,
    DiscoveredSample,
    DiscoveredTable,
    Job,
)


@dataclass(frozen=True)
class ClassificationOutcome:
    """Result of classifying a single column."""

    column_id: str
    schema_name: str
    table_name: str
    column_name: str
    sensitivity: str
    confidence: float
    reasoning: str
    suggested_strategy: str
    attempts: int
    ok: bool
    error: str | None
    classification_id: str


class ClassificationAgent:
    """Drive classification of one or more discovered columns via the LLM."""

    def __init__(self, session: Session, llm_client: LLMClient) -> None:
        self._session = session
        self._llm = llm_client

    def classify_column(
        self,
        job_id: str,
        column_id: str,
        max_retries: int = 3,
    ) -> ClassificationOutcome:
        """Classify a single discovered column by id."""
        if not self._job_exists(job_id):
            raise ValueError(f"Job {job_id!r} does not exist.")
        column = self._session.get(DiscoveredColumn, column_id)
        if column is None:
            raise ValueError(f"DiscoveredColumn {column_id!r} not found.")
        table = self._session.get(DiscoveredTable, column.table_id)
        if table is None or table.job_id != job_id:
            raise ValueError(f"DiscoveredColumn {column_id!r} is not part of job {job_id!r}.")

        sample_values = self._sample_values_for_column(table.id, column.name)
        result = parse_classify_column(
            self._llm,
            schema_name=table.schema_name,
            table_name=table.table_name,
            column_name=column.name,
            data_type=column.data_type,
            is_nullable=column.is_nullable,
            sample_values=sample_values,
            max_retries=max_retries,
        )
        outcome = self._upsert_result(job_id, table, column, result)
        from app.agents.audit import AuditAgent

        AuditAgent(self._session).write(
            event_type="classification.column.complete",
            outcome="ok" if outcome.ok else "failure",
            job_id=job_id,
            details={
                "table": table.table_name,
                "column": column.name,
                "sensitivity": outcome.sensitivity,
                "strategy": outcome.suggested_strategy,
                "attempts": outcome.attempts,
                "confidence": outcome.confidence,
                "error": outcome.error,
            },
        )
        return outcome

    def classify_table(
        self,
        job_id: str,
        table_id: str,
        max_retries: int = 3,
    ) -> list[ClassificationOutcome]:
        """Classify every column of a discovered table."""
        if not self._job_exists(job_id):
            raise ValueError(f"Job {job_id!r} does not exist.")
        table = self._session.get(DiscoveredTable, table_id)
        if table is None or table.job_id != job_id:
            raise ValueError(f"DiscoveredTable {table_id!r} not found in job {job_id!r}.")
        columns = list(
            self._session.execute(
                select(DiscoveredColumn)
                .where(DiscoveredColumn.table_id == table_id)
                .order_by(DiscoveredColumn.ordinal_position)
            ).scalars()
        )
        outcomes: list[ClassificationOutcome] = []
        for col in columns:
            outcomes.append(self.classify_column(job_id, col.id, max_retries))
        return outcomes

    def classify_job(
        self,
        job_id: str,
        max_retries: int = 3,
        table_id: str | None = None,
        include_non_sampleable: bool = False,
    ) -> list[ClassificationOutcome]:
        """Classify every column of every table in a job (or one table)."""
        if not self._job_exists(job_id):
            raise ValueError(f"Job {job_id!r} does not exist.")
        query = select(DiscoveredTable).where(DiscoveredTable.job_id == job_id)
        if table_id is not None:
            query = query.where(DiscoveredTable.id == table_id)
        tables = list(self._session.execute(query.order_by(DiscoveredTable.table_name)).scalars())
        outcomes: list[ClassificationOutcome] = []
        for table in tables:
            if not include_non_sampleable and not self._has_sample(table.id):
                logger.info(
                    "Skipping table {}.{} (no sample available; pass "
                    "include_non_sampleable=True to override).",
                    table.schema_name,
                    table.table_name,
                )
                continue
            outcomes.extend(self.classify_table(job_id, table.id, max_retries=max_retries))
        return outcomes

    def _upsert_result(
        self,
        job_id: str,
        table: DiscoveredTable,
        column: DiscoveredColumn,
        result: ClassificationSuccess | ClassificationFailure,
    ) -> ClassificationOutcome:
        existing = self._session.execute(
            select(ClassificationResult).where(
                ClassificationResult.job_id == job_id,
                ClassificationResult.schema_name == table.schema_name,
                ClassificationResult.table_name == table.table_name,
                ClassificationResult.column_name == column.name,
            )
        ).scalar_one_or_none()

        if existing is not None:
            row = existing
        else:
            row = ClassificationResult(
                job_id=job_id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                column_name=column.name,
                sensitivity="Unknown",
                confidence=0.0,
                reasoning="",
                suggested_strategy="Preserve Format",
            )
            self._session.add(row)

        if isinstance(result, ClassificationSuccess):
            row.sensitivity = result.classification.sensitivity
            row.confidence = result.classification.confidence
            row.reasoning = result.classification.reasoning
            row.suggested_strategy = result.classification.suggested_strategy
            row.attempts = result.attempts
            row.ok = True
            row.error = None
        else:
            # Keep the previous sensitivity/confidence if a previous run
            # had succeeded; only update error metadata.
            row.attempts = result.attempts
            row.ok = False
            row.error = result.error

        self._session.commit()

        return ClassificationOutcome(
            column_id=column.id,
            schema_name=table.schema_name,
            table_name=table.table_name,
            column_name=column.name,
            sensitivity=row.sensitivity,
            confidence=row.confidence,
            reasoning=row.reasoning,
            suggested_strategy=row.suggested_strategy,
            attempts=row.attempts,
            ok=row.ok,
            error=row.error,
            classification_id=row.id,
        )

    def _sample_values_for_column(self, table_id: str, column_name: str) -> list[dict[str, Any]]:
        sample = self._session.execute(
            select(DiscoveredSample).where(DiscoveredSample.table_id == table_id)
        ).scalar_one_or_none()
        if sample is None:
            return []
        out: list[dict[str, Any]] = []
        for row in sample.rows:
            cell = row.get(column_name)
            if cell is not None:
                out.append(cell)
        return out

    def _has_sample(self, table_id: str) -> bool:
        return (
            self._session.execute(
                select(DiscoveredSample.id).where(DiscoveredSample.table_id == table_id)
            ).scalar_one_or_none()
            is not None
        )

    def _job_exists(self, job_id: str) -> bool:
        return (
            self._session.execute(select(Job.id).where(Job.id == job_id)).scalar_one_or_none()
            is not None
        )
