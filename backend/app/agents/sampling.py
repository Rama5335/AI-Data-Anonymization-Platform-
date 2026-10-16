"""Data sampling agent.

Pulls N rows from each previously discovered table, applying the
per-value byte cap policy. Results are cached per ``(job_id, table_id)``
so re-running on the same job is a no-op.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.sampling import (
    DEFAULT_MAX_BYTES_PER_VALUE,
    DEFAULT_ROW_COUNT,
    ColumnSpec,
    MssqlSampler,
)
from app.models.entities import (
    DiscoveredColumn,
    DiscoveredSample,
    DiscoveredTable,
    Job,
)


@dataclass(frozen=True)
class SamplingResult:
    """Outcome of a single ``SamplingAgent.sample_table`` call."""

    rows: list[dict[str, Any]]
    row_count: int
    sampled_columns: list[str]
    max_bytes_per_value: int
    cached: bool


class SamplingAgent:
    """Drive a bounded row sample for a single previously discovered table."""

    def __init__(self, session: Session, sampler: MssqlSampler) -> None:
        self._session = session
        self._sampler = sampler

    def sample_table(
        self,
        job_id: str,
        table_id: str,
        row_count: int = DEFAULT_ROW_COUNT,
        max_bytes_per_value: int = DEFAULT_MAX_BYTES_PER_VALUE,
        excluded_column_names: list[str] | None = None,
        include_views: bool = False,
    ) -> SamplingResult:
        """Fetch up to ``row_count`` rows from the table and persist them.

        Cached per ``(job_id, table_id)``: a second call returns the
        already-persisted sample without re-querying the target.
        """
        if row_count < 1:
            raise ValueError("row_count must be >= 1")
        if max_bytes_per_value < 1:
            raise ValueError("max_bytes_per_value must be >= 1")

        if not self._job_exists(job_id):
            raise ValueError(f"Job {job_id!r} does not exist.")
        table = self._session.get(DiscoveredTable, table_id)
        if table is None or table.job_id != job_id:
            raise ValueError(f"DiscoveredTable {table_id!r} not found in job {job_id!r}.")
        if table.object_type.upper() == "VIEW" and not include_views:
            raise ValueError(
                "Sampling from views is disabled by default. "
                "Set include_views=True on the request to opt in."
            )

        existing = self._existing_sample(job_id, table_id)
        if existing is not None:
            # The cache is per (job_id, table_id); re-issue the same
            # underlying sample. Apply the caller's exclusion filter on
            # top so different exclusion sets do not require a re-sample.
            excluded = set(excluded_column_names or [])
            sampled_columns = [c for c in existing.sampled_columns if c not in excluded]
            rows = [{k: v for k, v in row.items() if k not in excluded} for row in existing.rows]
            return SamplingResult(
                rows=rows,
                row_count=len(rows),
                sampled_columns=sampled_columns,
                max_bytes_per_value=existing.max_bytes_per_value,
                cached=True,
            )

        excluded = set(excluded_column_names or [])
        columns = self._discovered_columns(table_id)
        spec_list = [
            ColumnSpec(
                name=c.name,
                data_type=c.data_type,
                character_maximum_length=c.character_maximum_length,
            )
            for c in columns
            if c.name not in excluded
        ]
        if not spec_list:
            raise ValueError("After exclusions, no columns remain to sample.")

        samples = self._sampler.fetch_samples(
            table.schema_name,
            table.table_name,
            spec_list,
            row_count=row_count,
            max_bytes_per_value=max_bytes_per_value,
        )

        entry = DiscoveredSample(
            job_id=job_id,
            table_id=table_id,
            row_count=len(samples),
            sampled_columns=[c.name for c in spec_list],
            max_bytes_per_value=max_bytes_per_value,
            rows=samples,
        )
        self._session.add(entry)
        self._session.commit()
        return SamplingResult(
            rows=samples,
            row_count=len(samples),
            sampled_columns=[c.name for c in spec_list],
            max_bytes_per_value=max_bytes_per_value,
            cached=False,
        )

    def _job_exists(self, job_id: str) -> bool:
        return (
            self._session.execute(select(Job.id).where(Job.id == job_id)).scalar_one_or_none()
            is not None
        )

    def _existing_sample(self, job_id: str, table_id: str) -> DiscoveredSample | None:
        return self._session.execute(
            select(DiscoveredSample).where(
                DiscoveredSample.job_id == job_id,
                DiscoveredSample.table_id == table_id,
            )
        ).scalar_one_or_none()

    def _discovered_columns(self, table_id: str) -> list[DiscoveredColumn]:
        return list(
            self._session.execute(
                select(DiscoveredColumn)
                .where(DiscoveredColumn.table_id == table_id)
                .order_by(DiscoveredColumn.ordinal_position)
            ).scalars()
        )
