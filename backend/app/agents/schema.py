"""Schema analysis agent.

Reads per-table metadata from a target MSSQL database and persists it to
the application database. Caching is implicit: a second call with the
same ``job_id`` is a no-op because the per-table unique constraints on
``(job_id, schema_name, table_name)`` prevent duplicates.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.mssql import DatabaseObject, MssqlDiscoveryClient
from app.database.schema_reader import MssqlSchemaReader
from app.models.entities import (
    DiscoveredColumn,
    DiscoveredConstraint,
    DiscoveredForeignKey,
    DiscoveredIndex,
    DiscoveredTable,
    Job,
)


@dataclass(frozen=True)
class SchemaDiscoveryCounts:
    """Counters returned by a single discovery run, for observability/tests."""

    tables: int = 0
    columns: int = 0
    foreign_keys: int = 0
    indexes: int = 0
    constraints: int = 0


@dataclass(frozen=True)
class SchemaDiscoveryResult:
    """Outcome of a single ``SchemaAgent.discover`` call."""

    counts: SchemaDiscoveryCounts
    cached: bool = False


class SchemaAgent:
    """Drive a read-only schema discovery for a single job."""

    def __init__(
        self,
        session: Session,
        discovery_client: MssqlDiscoveryClient,
        schema_reader: MssqlSchemaReader,
    ) -> None:
        self._session = session
        self._discovery = discovery_client
        self._reader = schema_reader

    def discover(self, job_id: str, database: str) -> SchemaDiscoveryResult:
        """Discover every TABLE and VIEW in ``database`` and persist it.

        Idempotent on ``job_id``: if the same job has already produced
        discovered tables, the call returns their current counts without
        issuing any catalog queries.
        """
        from app.agents.audit import AuditAgent

        if not self._job_exists(job_id):
            raise ValueError(f"Job {job_id!r} does not exist.")

        existing = self._existing_table_count(job_id)
        if existing > 0:
            AuditAgent(self._session).write(
                event_type="schema.discover.cached",
                outcome="ok",
                job_id=job_id,
                details={"tables": existing, "database": database},
            )
            return SchemaDiscoveryResult(counts=self._counts_for(job_id), cached=True)

        AuditAgent(self._session).write(
            event_type="schema.discover.start",
            outcome="started",
            job_id=job_id,
            details={"database": database},
        )

        objects = self._discovery.list_objects("TABLE") + self._discovery.list_objects("VIEW")
        # The discovery client lists objects from the database it was opened
        # against. The ``database`` argument is informational; both list_objects
        # calls already return only objects from the current database.
        _ = database

        counts = SchemaDiscoveryCounts()
        for obj in objects:
            table_counts = self._discover_one(job_id, obj)
            counts = SchemaDiscoveryCounts(
                tables=counts.tables + 1,
                columns=counts.columns + table_counts.columns,
                foreign_keys=counts.foreign_keys + table_counts.foreign_keys,
                indexes=counts.indexes + table_counts.indexes,
                constraints=counts.constraints + table_counts.constraints,
            )

        self._session.commit()
        AuditAgent(self._session).write(
            event_type="schema.discover.complete",
            outcome="ok",
            job_id=job_id,
            details={
                "tables": counts.tables,
                "columns": counts.columns,
                "foreign_keys": counts.foreign_keys,
                "indexes": counts.indexes,
                "constraints": counts.constraints,
            },
        )
        return SchemaDiscoveryResult(counts=counts, cached=False)

    def _discover_one(self, job_id: str, obj: DatabaseObject) -> SchemaDiscoveryCounts:
        table = DiscoveredTable(
            job_id=job_id,
            schema_name=obj.schema_name,
            table_name=obj.name,
            object_type=obj.object_type,
        )
        self._session.add(table)
        self._session.flush()

        columns = self._reader.read_columns(obj.schema_name, obj.name)
        pk_names = set(self._reader.read_primary_key_columns(obj.schema_name, obj.name))
        fk_rows = self._reader.read_foreign_keys(obj.schema_name, obj.name)
        fk_columns = {fk.column_name for fk in fk_rows}
        index_rows = self._reader.read_indexes(obj.schema_name, obj.name)
        constraint_rows = self._reader.read_constraints(obj.schema_name, obj.name)

        column_id_by_name: dict[str, str] = {}
        for col in columns:
            entry = DiscoveredColumn(
                table_id=table.id,
                name=col.name,
                ordinal_position=col.ordinal_position,
                data_type=col.data_type,
                is_nullable=col.is_nullable,
                column_default=col.column_default,
                character_maximum_length=col.character_maximum_length,
                numeric_precision=col.numeric_precision,
                numeric_scale=col.numeric_scale,
                is_primary_key=col.name in pk_names,
                is_foreign_key=col.name in fk_columns,
            )
            self._session.add(entry)
            self._session.flush()
            column_id_by_name[col.name] = entry.id

        for fk in fk_rows:
            column_id = column_id_by_name.get(fk.column_name)
            if column_id is None:
                continue
            self._session.add(
                DiscoveredForeignKey(
                    column_id=column_id,
                    fk_name=fk.fk_name,
                    referenced_schema=fk.referenced_schema,
                    referenced_table=fk.referenced_table,
                    referenced_column=fk.referenced_column,
                )
            )

        for idx in index_rows:
            self._session.add(
                DiscoveredIndex(
                    table_id=table.id,
                    name=idx.name,
                    is_unique=idx.is_unique,
                    is_primary=idx.is_primary,
                    column_names=idx.column_names,
                )
            )

        for c in constraint_rows:
            self._session.add(
                DiscoveredConstraint(
                    table_id=table.id,
                    name=c.name,
                    constraint_type=c.constraint_type,
                )
            )

        return SchemaDiscoveryCounts(
            tables=1,
            columns=len(columns),
            foreign_keys=len(fk_rows),
            indexes=len(index_rows),
            constraints=len(constraint_rows),
        )

    def _job_exists(self, job_id: str) -> bool:
        return (
            self._session.execute(select(Job.id).where(Job.id == job_id)).scalar_one_or_none()
            is not None
        )

    def _existing_table_count(self, job_id: str) -> int:
        return len(
            self._session.execute(
                select(DiscoveredTable.id).where(DiscoveredTable.job_id == job_id)
            ).all()
        )

    def _counts_for(self, job_id: str) -> SchemaDiscoveryCounts:
        """Recompute counts from already-persisted metadata."""
        table_ids = [
            row[0]
            for row in self._session.execute(
                select(DiscoveredTable.id).where(DiscoveredTable.job_id == job_id)
            ).all()
        ]
        if not table_ids:
            return SchemaDiscoveryCounts()

        column_rows = self._session.execute(
            select(DiscoveredColumn.id).where(DiscoveredColumn.table_id.in_(table_ids))
        ).all()
        fk_rows = self._session.execute(
            select(DiscoveredForeignKey.id)
            .join(DiscoveredColumn, DiscoveredForeignKey.column_id == DiscoveredColumn.id)
            .where(DiscoveredColumn.table_id.in_(table_ids))
        ).all()
        index_rows = self._session.execute(
            select(DiscoveredIndex.id).where(DiscoveredIndex.table_id.in_(table_ids))
        ).all()
        constraint_rows = self._session.execute(
            select(DiscoveredConstraint.id).where(DiscoveredConstraint.table_id.in_(table_ids))
        ).all()
        return SchemaDiscoveryCounts(
            tables=len(table_ids),
            columns=len(column_rows),
            foreign_keys=len(fk_rows),
            indexes=len(index_rows),
            constraints=len(constraint_rows),
        )
