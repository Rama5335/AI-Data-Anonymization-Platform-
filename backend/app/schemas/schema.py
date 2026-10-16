"""Schemas for the schema analysis API."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.discovery import ConnectionDetails


class SchemaDiscoveryRequest(BaseModel):
    """Trigger a schema discovery for a target MSSQL database.

    The target database is whatever ``ConnectionDetails.database`` points to;
    if it's empty, discovery is run against the server's default database.
    """

    connection: ConnectionDetails
    requested_by_user_id: str | None = Field(
        default=None,
        description=(
            "Optional requester attribution. The server only honors this when "
            "it matches the authenticated caller, or when the caller is an "
            "admin (attribution on behalf of another user). Any other mismatch "
            "is rejected with 403; if omitted, the job is attributed to the "
            "authenticated caller."
        ),
    )


class SchemaDiscoveryResponse(BaseModel):
    """Outcome of a single schema discovery run."""

    job_id: str
    table_count: int
    column_count: int
    foreign_key_count: int
    index_count: int
    constraint_count: int
    cached: bool = Field(
        description=(
            "True when the run returned the already-persisted metadata "
            "without re-querying the source database."
        )
    )


class DiscoveredColumnOut(BaseModel):
    """One persisted column in a discovered table."""

    id: str
    name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    column_default: str | None
    character_maximum_length: int | None
    numeric_precision: int | None
    numeric_scale: int | None
    is_primary_key: bool
    is_foreign_key: bool


class DiscoveredForeignKeyOut(BaseModel):
    """One persisted foreign key relationship."""

    fk_name: str
    column_name: str
    referenced_schema: str
    referenced_table: str
    referenced_column: str


class DiscoveredIndexOut(BaseModel):
    """One persisted index on a discovered table."""

    name: str
    is_unique: bool
    is_primary: bool
    column_names: list[str]


class DiscoveredConstraintOut(BaseModel):
    """One persisted table-level CHECK/UNIQUE constraint."""

    name: str
    constraint_type: str


class DiscoveredTableOut(BaseModel):
    """One discovered table with all of its persisted metadata."""

    id: str
    schema_name: str
    table_name: str
    object_type: str
    discovered_at: datetime
    columns: list[DiscoveredColumnOut]
    foreign_keys: list[DiscoveredForeignKeyOut]
    indexes: list[DiscoveredIndexOut]
    constraints: list[DiscoveredConstraintOut]


class JobSchemaResponse(BaseModel):
    """Full persisted metadata for a single job."""

    job_id: str
    tables: list[DiscoveredTableOut]
