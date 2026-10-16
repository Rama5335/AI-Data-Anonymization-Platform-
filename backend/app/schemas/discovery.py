"""Schemas for read-only discovery endpoints and server metadata."""

from pydantic import BaseModel, Field, SecretStr


class ServerRegistration(BaseModel):
    """Metadata-only server registration (no credentials)."""

    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    auth_type: str = Field(pattern="^(sql|windows)$")


class ConnectionDetails(BaseModel):
    """Full, short-lived connection details for read-only discovery.

    The password is wrapped in ``SecretStr`` so accidental ``repr()``/logs do
    not expose it. The values are used only to construct a one-shot
    ``MssqlDiscoveryClient``; nothing is persisted.
    """

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    auth_type: str = Field(pattern="^(sql|windows)$")
    username: str | None = None
    password: SecretStr | None = None
    database: str | None = None
    driver: str = Field(default="ODBC Driver 18 for SQL Server", min_length=1)
    encrypt: bool = True
    trust_server_certificate: bool = False
    timeout_seconds: int = Field(default=15, ge=1, le=120)


class ConnectionTestResult(BaseModel):
    """Outcome of a single read-only connection probe."""

    success: bool


class DatabaseListResponse(BaseModel):
    """Online, non-snapshot user databases discovered on the target server."""

    databases: list[str]


class SchemaListResponse(BaseModel):
    """User-defined schemas visible in the target database."""

    schemas: list[str]


class ObjectInfo(BaseModel):
    """A single TABLE or VIEW discovered via INFORMATION_SCHEMA."""

    schema_name: str
    name: str
    object_type: str


class ObjectListResponse(BaseModel):
    """Discoverable tables or views in the target database."""

    objects: list[ObjectInfo]
