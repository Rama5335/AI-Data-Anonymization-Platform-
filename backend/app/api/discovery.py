"""Read-only MSSQL discovery endpoints.

Every route in this module is bounded by the same invariants:

* Only ``MssqlDiscoveryClient`` methods are exposed, and that client only
  issues fixed catalog queries.
* Connection details are validated by Pydantic and never logged.
* The client is disposed per-request so credentials live only for the
  lifetime of the HTTP call.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database.mssql import MssqlDiscoveryClient
from app.models.entities import User
from app.security.deps import get_current_user
from app.security.redaction import redact_text
from app.database.url import build_mssql_url
from app.schemas.discovery import (
    ConnectionDetails,
    ConnectionTestResult,
    DatabaseListResponse,
    ObjectListResponse,
    SchemaListResponse,
)

router = APIRouter(prefix="/discovery", tags=["discovery"])

_ALLOWED_OBJECT_TYPES = frozenset({"TABLE", "VIEW"})


def _open_client(details: ConnectionDetails) -> MssqlDiscoveryClient:
    try:
        url = build_mssql_url(details)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MssqlDiscoveryClient(url, details.timeout_seconds)


@router.post("/test-connection", response_model=ConnectionTestResult)
def test_connection(
    details: ConnectionDetails, user: User = Depends(get_current_user)
) -> ConnectionTestResult:
    """Open and immediately release a connection; never touch target state."""
    client = _open_client(details)
    try:
        client.test_connection()
    except Exception as exc:  # noqa: BLE001 - surface any driver failure as 503
        raise HTTPException(
            status_code=503,
            # Driver errors can embed connection-string fragments (including
            # passwords); only a redacted message leaves the process.
            detail=f"Connection failed: {redact_text(str(exc))}",
        ) from exc
    finally:
        client.dispose()
    return ConnectionTestResult(success=True)


@router.post("/databases", response_model=DatabaseListResponse)
def list_databases(
    details: ConnectionDetails, user: User = Depends(get_current_user)
) -> DatabaseListResponse:
    """Return online, non-snapshot user databases on the target server."""
    client = _open_client(details)
    try:
        return DatabaseListResponse(databases=client.list_databases())
    finally:
        client.dispose()


@router.post("/schemas", response_model=SchemaListResponse)
def list_schemas(
    details: ConnectionDetails, user: User = Depends(get_current_user)
) -> SchemaListResponse:
    """Return user-defined schemas in the target database."""
    client = _open_client(details)
    try:
        return SchemaListResponse(schemas=client.list_schemas())
    finally:
        client.dispose()


@router.post("/objects", response_model=ObjectListResponse)
def list_objects(
    details: ConnectionDetails,
    object_type: str = Query(..., pattern="^(TABLE|VIEW)$"),
    user: User = Depends(get_current_user),
) -> ObjectListResponse:
    """List tables or views via INFORMATION_SCHEMA.TABLES only."""
    if object_type not in _ALLOWED_OBJECT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="object_type must be one of: TABLE, VIEW.",
        )
    client = _open_client(details)
    try:
        objects = client.list_objects(object_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        client.dispose()
    return ObjectListResponse(
        objects=[
            {
                "schema_name": obj.schema_name,
                "name": obj.name,
                "object_type": obj.object_type,
            }
            for obj in objects
        ]
    )
