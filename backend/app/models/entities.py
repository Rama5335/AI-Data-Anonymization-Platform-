"""Application metadata entities; these never represent target data rows."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampedModel, utc_now


def new_id() -> str:
    return str(uuid4())


class User(TimestampedModel, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(32), default="user")


class Server(TimestampedModel, Base):
    __tablename__ = "servers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=1433)
    auth_type: Mapped[str] = mapped_column(String(32))


class Connection(TimestampedModel, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("server_id", "database_name", name="uq_connection_database"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id"), index=True)
    database_name: Mapped[str] = mapped_column(String(128))
    encrypted_connection_string: Mapped[str] = mapped_column(Text)


class Job(TimestampedModel, Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), index=True)
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    workflow_state: Mapped[dict] = mapped_column(JSON, default=dict)


class ClassificationResult(TimestampedModel, Base):
    """One persisted classification of one (table, column) by the LLM.

    The (job_id, schema_name, table_name, column_name) tuple is unique
    so re-classification upserts: there is exactly one current row per
    column per job. Failed classifications are stored with ``ok=False``
    and the parser error in ``error`` so the UI and downstream agents
    can show what went wrong instead of silently dropping the column.
    """

    __tablename__ = "classification_results"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "schema_name",
            "table_name",
            "column_name",
            name="uq_classification_result_column",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    schema_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    column_name: Mapped[str] = mapped_column(String(128))
    sensitivity: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column()
    reasoning: Mapped[str] = mapped_column(Text)
    suggested_strategy: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Approval(TimestampedModel, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint(
            "classification_result_id",
            name="uq_approval_classification",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    classification_result_id: Mapped[str] = mapped_column(
        ForeignKey("classification_results.id"), index=True
    )
    approved_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(32))  # approve | reject | override
    masking_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLog(Base):
    """Append-only operational event record; no application update/delete path is provided."""

    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ExecutionHistory(Base):
    __tablename__ = "execution_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class BackupRecord(Base):
    """Persistent metadata for a ``BACKUP DATABASE`` attempt.

    A row is written for every backup attempt, success or failure, so
    the audit trail and the update gate have a durable record.

    ``verified`` is set to ``True`` only after ``RESTORE VERIFYONLY``
    succeeds. The safe-update agent refuses to modify target data
    unless it can find a ``verified`` row for the supplied backup path
    (and, when the row is already bound to a job, for the same job).
    """

    __tablename__ = "backup_records"
    __table_args__ = (UniqueConstraint("backup_path", name="uq_backup_record_path"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    database_name: Mapped[str] = mapped_column(String(128), index=True)
    # Bounded length because ``backup_path`` is part of a UNIQUE constraint
    # (see ``__table_args__``). SQL Server rejects TEXT/VARCHAR(MAX) as an
    # index key column (error 1919), so keep the ORM in step with the
    # migration (VARCHAR(512)).
    backup_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveredTable(Base):
    """A TABLE or VIEW discovered by the schema agent for a given job.

    Read-only metadata. Never used to mutate the source database.
    """

    __tablename__ = "discovered_tables"
    __table_args__ = (
        UniqueConstraint("job_id", "schema_name", "table_name", name="uq_discovered_table"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    schema_name: Mapped[str] = mapped_column(String(128))
    table_name: Mapped[str] = mapped_column(String(128))
    object_type: Mapped[str] = mapped_column(String(32))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiscoveredColumn(Base):
    """A column belonging to a previously discovered table."""

    __tablename__ = "discovered_columns"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_discovered_column"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    table_id: Mapped[str] = mapped_column(ForeignKey("discovered_tables.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    ordinal_position: Mapped[int] = mapped_column(Integer)
    data_type: Mapped[str] = mapped_column(String(64))
    is_nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    column_default: Mapped[str | None] = mapped_column(Text, nullable=True)
    character_maximum_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    numeric_precision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    numeric_scale: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False)
    is_foreign_key: Mapped[bool] = mapped_column(Boolean, default=False)


class DiscoveredForeignKey(Base):
    """An outbound foreign key from a discovered column to a target column."""

    __tablename__ = "discovered_foreign_keys"
    __table_args__ = (UniqueConstraint("column_id", name="uq_discovered_fk_column"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    column_id: Mapped[str] = mapped_column(ForeignKey("discovered_columns.id"), index=True)
    fk_name: Mapped[str] = mapped_column(String(128))
    referenced_schema: Mapped[str] = mapped_column(String(128))
    referenced_table: Mapped[str] = mapped_column(String(128))
    referenced_column: Mapped[str] = mapped_column(String(128))


class DiscoveredIndex(Base):
    """An index on a previously discovered table.

    ``column_names`` is stored as a JSON list of column names in the order
    they appear in the index, so multi-column indexes round-trip cleanly.
    """

    __tablename__ = "discovered_indexes"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_discovered_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    table_id: Mapped[str] = mapped_column(ForeignKey("discovered_tables.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    is_unique: Mapped[bool] = mapped_column(Boolean, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    column_names: Mapped[list] = mapped_column(JSON, default=list)


class DiscoveredConstraint(Base):
    """A table-level constraint (CHECK, UNIQUE) discovered alongside the table.

    Primary keys and foreign keys are stored in their own tables so this
    model only carries the remaining constraint kinds.
    """

    __tablename__ = "discovered_constraints"
    __table_args__ = (
        UniqueConstraint("table_id", "name", "constraint_type", name="uq_discovered_constraint"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    table_id: Mapped[str] = mapped_column(ForeignKey("discovered_tables.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    constraint_type: Mapped[str] = mapped_column(String(32))


class DiscoveredSample(Base):
    """A bounded row sample from a previously discovered table.

    Cached per ``(job_id, table_id)``; re-sampling the same table under
    the same job is a no-op. Each row is stored as a dict of
    ``{column_name: {value, truncated, original_length}}``.
    """

    __tablename__ = "discovered_samples"
    __table_args__ = (UniqueConstraint("job_id", "table_id", name="uq_discovered_sample"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("discovered_tables.id"), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    sampled_columns: Mapped[list] = mapped_column(JSON, default=list)
    max_bytes_per_value: Mapped[int] = mapped_column(Integer)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MaskingJob(Base):
    """Per-job state for the masking engine.

    ``salt_encrypted`` and ``token_key_encrypted`` are bytes encrypted
    with the application-level ``CREDENTIAL_ENCRYPTION_KEY`` so they
    are unreadable at rest without that key. ``wiped_at`` is set when
    the operator calls ``MaskingAgent.wipe()``; downstream phases
    must refuse to run after that point.
    """

    __tablename__ = "masking_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    salt_encrypted: Mapped[bytes] = mapped_column()
    token_key_encrypted: Mapped[bytes] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    wiped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TokenVault(Base):
    """A small ciphertext-style reversibility table.

    For each (job, table, column, original_value) tuple that was
    tokenized, we persist ``(tokenized_value → original_value)`` so
    an operator can run a reverse pass **before** the token key is
    wiped. After the wipe, the original values are unrecoverable.
    """

    __tablename__ = "token_vault"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "table_name",
            "column_name",
            "tokenized_value",
            name="uq_token_vault_entry",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(128))
    column_name: Mapped[str] = mapped_column(String(128))
    tokenized_value: Mapped[str] = mapped_column(String(128))
    original_value: Mapped[str] = mapped_column(Text)
