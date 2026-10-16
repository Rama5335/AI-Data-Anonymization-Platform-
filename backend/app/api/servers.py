"""Servers API (Item 4).

CRUD on the ``Server`` table plus a "list databases" helper that
probes a server using short-lived credentials (the same pattern the
ephemeral-discovery /schema/discover endpoint uses).
"""

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.session import session_scope
from app.models.entities import Connection, Server, User
from app.security.deps import get_current_user, require_role
from app.security.redaction import redact_text

router = APIRouter(prefix="/servers", tags=["servers"])


class ServerOut(BaseModel):
    id: str
    name: str
    host: str
    port: int
    auth_type: str
    created_at: datetime
    updated_at: datetime


class CreateServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    auth_type: str = Field(default="sql", pattern="^(sql|windows)$")


class UpdateServerRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    auth_type: Optional[str] = Field(default=None, pattern="^(sql|windows)$")


class ProbeRequest(BaseModel):
    username: str
    password: str
    database: str | None = None


class ProbeResult(BaseModel):
    server_id: str
    host: str
    port: int
    reachable: bool
    databases: list[str] | None = None
    error: str | None = None


def _to_out(s: Server) -> ServerOut:
    return ServerOut(
        id=s.id,
        name=s.name,
        host=s.host,
        port=s.port,
        auth_type=s.auth_type,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("", response_model=list[ServerOut])
def list_servers(
    user: Annotated[User, Depends(get_current_user)],
) -> list[ServerOut]:
    session = session_scope()
    try:
        rows = list(session.execute(select(Server).order_by(Server.name)).scalars())
        return [_to_out(s) for s in rows]
    finally:
        session.close()


@router.post("", response_model=ServerOut, status_code=201)
def create_server(
    req: CreateServerRequest,
    user: Annotated[User, Depends(require_role("admin"))],
) -> ServerOut:
    session = session_scope()
    try:
        server = Server(
            name=req.name,
            host=req.host,
            port=req.port,
            auth_type=req.auth_type,
        )
        session.add(server)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Server name {req.name!r} already exists.",
            ) from exc
        session.refresh(server)
        return _to_out(server)
    finally:
        session.close()


@router.get("/{server_id}", response_model=ServerOut)
def get_server(
    server_id: str,
    user: Annotated[User, Depends(get_current_user)],
) -> ServerOut:
    session = session_scope()
    try:
        s = session.get(Server, server_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found.")
        return _to_out(s)
    finally:
        session.close()


@router.patch("/{server_id}", response_model=ServerOut)
def update_server(
    server_id: str,
    req: UpdateServerRequest,
    user: Annotated[User, Depends(require_role("admin"))],
) -> ServerOut:
    session = session_scope()
    try:
        s = session.get(Server, server_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found.")
        if req.name is not None:
            s.name = req.name
        if req.host is not None:
            s.host = req.host
        if req.port is not None:
            s.port = req.port
        if req.auth_type is not None:
            s.auth_type = req.auth_type
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail=f"Server name {req.name!r} already exists."
            ) from exc
        session.refresh(s)
        return _to_out(s)
    finally:
        session.close()


@router.delete("/{server_id}", status_code=204)
def delete_server(
    server_id: str,
    user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    session = session_scope()
    try:
        s = session.get(Server, server_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found.")
        # Refuse to delete if there are Connections referencing it.
        conns = list(
            session.execute(select(Connection).where(Connection.server_id == server_id)).scalars()
        )
        if conns:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot delete: {len(conns)} Connection(s) reference this server. "
                    "Delete them first."
                ),
            )
        session.delete(s)
        session.commit()
    finally:
        session.close()


@router.post("/{server_id}/probe", response_model=ProbeResult)
def probe_server(
    server_id: str,
    req: ProbeRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ProbeResult:
    """Open a short-lived connection to the server and list databases.

    The username/password are *not* persisted — they're used only for
    this probe. Use the ephemeral-discovery /schema/discover endpoint
    for the same behaviour through the schema API.
    """
    from app.database.url import build_mssql_url
    from app.schemas.discovery import ConnectionDetails
    from app.database.mssql import MssqlDiscoveryClient

    session = session_scope()
    try:
        s = session.get(Server, server_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found.")
    finally:
        session.close()

    details = ConnectionDetails(
        host=s.host,
        port=s.port,
        auth_type=s.auth_type,
        username=req.username,
        password=req.password,
        database=req.database,
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=10,
    )
    try:
        url = build_mssql_url(details)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    client = MssqlDiscoveryClient(url, details.timeout_seconds)
    try:
        try:
            dbs = client.list_databases()
        except Exception as exc:  # noqa: BLE001
            return ProbeResult(
                server_id=server_id,
                host=s.host,
                port=s.port,
                reachable=False,
                databases=None,
                # Driver errors can embed connection-string fragments
                # (including passwords); never echo them verbatim.
                error=redact_text(str(exc)),
            )
        return ProbeResult(
            server_id=server_id,
            host=s.host,
            port=s.port,
            reachable=True,
            databases=dbs,
            error=None,
        )
    finally:
        client.dispose()
