"""Schemas for the data sampling API."""

from pydantic import BaseModel, Field

from app.database.sampling import DEFAULT_MAX_BYTES_PER_VALUE, DEFAULT_ROW_COUNT
from app.schemas.discovery import ConnectionDetails


class SampleTableRequest(BaseModel):
    """Trigger a bounded row sample for a single discovered table.

    The body carries short-lived connection details because the platform
    does not yet store credentials at rest. Once the credential vault
    lands, this will be replaced with a job-bound lookup.
    """

    connection: ConnectionDetails = Field(
        description="Short-lived credentials for the target MSSQL server.",
    )
    row_count: int = Field(
        default=DEFAULT_ROW_COUNT,
        ge=1,
        le=1000,
        description="Maximum number of rows to return.",
    )
    max_bytes_per_value: int = Field(
        default=DEFAULT_MAX_BYTES_PER_VALUE,
        ge=1,
        le=65536,
        description=(
            "Per-value byte cap. Values longer than this are truncated at the "
            "SQL layer and flagged with ``truncated: true`` in the response."
        ),
    )
    excluded_column_names: list[str] = Field(
        default_factory=list,
        description="Column names to omit from the sample (case-sensitive).",
    )
    include_views: bool = Field(
        default=False,
        description=(
            "Allow sampling from VIEW objects. Off by default because views "
            "can be unbounded; set true to opt in for a specific call."
        ),
    )


class SampleCellOut(BaseModel):
    """A single cell with truncation metadata."""

    value: str | int | float | bool | None
    truncated: bool
    original_length: int | None


class SampleResultResponse(BaseModel):
    """Outcome of a single sampling call."""

    job_id: str
    table_id: str
    schema_name: str
    table_name: str
    row_count: int
    sampled_columns: list[str]
    max_bytes_per_value: int
    rows: list[dict[str, SampleCellOut]]
    cached: bool = Field(
        description=(
            "True when the call returned the already-persisted sample "
            "without re-querying the source database."
        )
    )
