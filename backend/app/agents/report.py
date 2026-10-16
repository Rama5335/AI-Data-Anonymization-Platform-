"""Phase 7 — classification report generation.

A human-readable report per job. Aggregates ``ClassificationResult``
rows into per-table, per-column buckets, and surfaces counts
("how many Email / Phone / …" and "how many pending approval"). No
LLM call; this is a deterministic rollup of the persisted metadata.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    ClassificationResult,
    DiscoveredColumn,
    DiscoveredTable,
)


@dataclass(frozen=True)
class ColumnReport:
    schema_name: str
    table_name: str
    column_name: str
    data_type: str
    sensitivity: str
    confidence: float
    suggested_strategy: str
    attempts: int
    ok: bool
    error: str | None
    reasoning: str


@dataclass(frozen=True)
class TableReport:
    schema_name: str
    table_name: str
    columns: list[ColumnReport]

    @property
    def sensitive_columns(self) -> int:
        return sum(
            1 for c in self.columns if c.ok and c.sensitivity not in ("Non Sensitive", "Unknown")
        )


@dataclass(frozen=True)
class JobReport:
    job_id: str
    tables: list[TableReport]

    @property
    def total_columns(self) -> int:
        return sum(len(t.columns) for t in self.tables)

    @property
    def sensitive_columns(self) -> int:
        return sum(t.sensitive_columns for t in self.tables)

    @property
    def failed_columns(self) -> int:
        return sum(1 for t in self.tables for c in t.columns if not c.ok and c.attempts > 0)

    @property
    def pending_columns(self) -> int:
        return sum(1 for t in self.tables for c in self._all_columns(t) if c.attempts == 0)

    def _all_columns(self, table: "TableReport") -> list[ColumnReport]:
        return list(table.columns)

    @property
    def sensitivity_counts(self) -> dict[str, int]:
        out: Counter[str] = Counter()
        for t in self.tables:
            for c in t.columns:
                if c.ok:
                    out[c.sensitivity] += 1
        return dict(out)

    @property
    def strategy_counts(self) -> dict[str, int]:
        out: Counter[str] = Counter()
        for t in self.tables:
            for c in t.columns:
                if c.ok:
                    out[c.suggested_strategy] += 1
        return dict(out)


def build_job_report(session: Session, job_id: str) -> JobReport:
    """Aggregate persisted classifications for a job into a report."""
    tables = list(
        session.execute(
            select(DiscoveredTable)
            .where(DiscoveredTable.job_id == job_id)
            .order_by(DiscoveredTable.schema_name, DiscoveredTable.table_name)
        ).scalars()
    )
    if not tables:
        return JobReport(job_id=job_id, tables=[])

    table_ids = [t.id for t in tables]
    columns = list(
        session.execute(
            select(DiscoveredColumn)
            .where(DiscoveredColumn.table_id.in_(table_ids))
            .order_by(DiscoveredColumn.table_id, DiscoveredColumn.ordinal_position)
        ).scalars()
    )
    classifications = list(
        session.execute(
            select(ClassificationResult).where(ClassificationResult.job_id == job_id)
        ).scalars()
    )
    # The unique constraint on (job, schema, table, column) lets us
    # join on those three; we don't need to track column UUIDs here.
    by_triple: dict[tuple[str, str, str], ClassificationResult] = {
        (r.schema_name, r.table_name, r.column_name): r for r in classifications
    }

    by_table: dict[str, list[ColumnReport]] = {t.id: [] for t in tables}
    for t in tables:
        cols_for_t = [c for c in columns if c.table_id == t.id]
        for col in cols_for_t:
            key = (t.schema_name, t.table_name, col.name)
            r = by_triple.get(key)
            if r is None:
                # Discovered column with no classification yet — surface
                # as Unknown with low confidence so the report is honest.
                by_table[t.id].append(
                    ColumnReport(
                        schema_name=t.schema_name,
                        table_name=t.table_name,
                        column_name=col.name,
                        data_type=col.data_type,
                        sensitivity="Unknown",
                        confidence=0.0,
                        suggested_strategy="Preserve Format",
                        attempts=0,
                        ok=False,
                        error="not classified",
                        reasoning="",
                    )
                )
                continue
            by_table[t.id].append(
                ColumnReport(
                    schema_name=t.schema_name,
                    table_name=t.table_name,
                    column_name=col.name,
                    data_type=col.data_type,
                    sensitivity=r.sensitivity,
                    confidence=r.confidence,
                    suggested_strategy=r.suggested_strategy,
                    attempts=r.attempts,
                    ok=r.ok,
                    error=r.error,
                    reasoning=r.reasoning,
                )
            )

    return JobReport(
        job_id=job_id,
        tables=[
            TableReport(
                schema_name=t.schema_name,
                table_name=t.table_name,
                columns=by_table[t.id],
            )
            for t in tables
        ],
    )


def report_to_dict(report: JobReport) -> dict[str, Any]:
    """Plain-dict view of a JobReport for the JSON API."""
    return {
        "job_id": report.job_id,
        "totals": {
            "tables": len(report.tables),
            "columns": report.total_columns,
            "sensitive_columns": report.sensitive_columns,
            "failed_columns": report.failed_columns,
            "pending_columns": report.pending_columns,
        },
        "sensitivity_counts": report.sensitivity_counts,
        "strategy_counts": report.strategy_counts,
        "tables": [
            {
                "schema_name": t.schema_name,
                "table_name": t.table_name,
                "sensitive_columns": t.sensitive_columns,
                "columns": [
                    {
                        "name": c.column_name,
                        "data_type": c.data_type,
                        "sensitivity": c.sensitivity,
                        "confidence": c.confidence,
                        "suggested_strategy": c.suggested_strategy,
                        "attempts": c.attempts,
                        "ok": c.ok,
                        "error": c.error,
                        "reasoning": c.reasoning,
                    }
                    for c in t.columns
                ],
            }
            for t in report.tables
        ],
    }


def render_markdown(report: JobReport) -> str:
    """Human-readable Markdown report for the Approval Center."""
    lines: list[str] = []
    lines.append(f"# Classification report — job `{report.job_id[:8]}`")
    lines.append("")
    lines.append(
        f"**{report.total_columns}** columns across **{len(report.tables)}** tables — "
        f"**{report.sensitive_columns}** sensitive, "
        f"**{report.failed_columns}** failed."
    )
    lines.append("")
    if report.sensitivity_counts:
        lines.append("## Sensitivity")
        for k, v in sorted(report.sensitivity_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {k}: {v}")
        lines.append("")
    if report.strategy_counts:
        lines.append("## Suggested strategies")
        for k, v in sorted(report.strategy_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {k}: {v}")
        lines.append("")
    for t in report.tables:
        lines.append(f"## `{t.schema_name}.{t.table_name}`")
        if not t.columns:
            lines.append("_no columns_")
            lines.append("")
            continue
        lines.append("| column | data_type | sensitivity | conf | strategy | attempts | ok |")
        lines.append("| --- | --- | --- | ---: | --- | ---: | :---: |")
        for c in t.columns:
            conf = f"{c.confidence:.0%}" if c.ok else "—"
            ok = "✓" if c.ok else f"✗ ({c.error or 'failed'})"
            lines.append(
                f"| {c.column_name} | {c.data_type} | {c.sensitivity} | {conf} "
                f"| {c.suggested_strategy} | {c.attempts} | {ok} |"
            )
        lines.append("")
    return "\n".join(lines)
