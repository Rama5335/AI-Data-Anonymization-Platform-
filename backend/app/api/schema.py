"""Schema analysis API.

Read-only. Persists discovered metadata to the application database so
downstream agents (sampling, classification, masking) can iterate over
it without re-querying the source database on every step.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schema import SchemaAgent
from app.database.mssql import MssqlDiscoveryClient
from app.database.schema_reader import MssqlSchemaReader
from app.database.session import session_scope
from app.database.url import build_mssql_url
from app.models.entities import (
    Connection,
    DiscoveredColumn,
    DiscoveredConstraint,
    DiscoveredForeignKey,
    DiscoveredIndex,
    DiscoveredTable,
    Job,
    User,
)
from app.schemas.schema import (
    DiscoveredColumnOut,
    DiscoveredConstraintOut,
    DiscoveredForeignKeyOut,
    DiscoveredIndexOut,
    DiscoveredTableOut,
    JobSchemaResponse,
    SchemaDiscoveryRequest,
    SchemaDiscoveryResponse,
)
from app.security.deps import get_current_user
from app.security.redaction import redact_text

router = APIRouter(prefix="/schema", tags=["schema"])


_SYSTEM_USER_USERNAME = "system"


def _get_session() -> Session:
    """Open a metadata-DB session. Closed by the caller (via ``finally``)."""
    return session_scope()


def _ensure_user(session: Session, requested_by_user_id: str | None) -> str:
    if requested_by_user_id:
        user = session.get(User, requested_by_user_id)
        if user is None:
            raise HTTPException(
                status_code=404,
                detail=f"User {requested_by_user_id!r} not found.",
            )
        return user.id
    user = session.execute(
        select(User).where(User.username == _SYSTEM_USER_USERNAME)
    ).scalar_one_or_none()
    if user is not None:
        return user.id
    user = User(
        username=_SYSTEM_USER_USERNAME,
        password_hash="!disabled",
        is_active=False,
    )
    session.add(user)
    session.flush()
    return user.id


def _resolve_attribution(requested_by_user_id: str | None, actor: User) -> str:
    """Return the user id a discovered job should be attributed to.

    Defaults to the authenticated caller. A body-supplied
    ``requested_by_user_id`` is honored only when it matches the
    caller (no-op) or when the caller is an admin; anyone else
    attempting to attribute work to another user is rejected with 403
    so job attribution cannot be spoofed.
    """
    if requested_by_user_id is None or requested_by_user_id == actor.id:
        return actor.id
    if actor.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins may attribute a discovery job to another user.",
        )
    return requested_by_user_id


@router.post("/discover", response_model=SchemaDiscoveryResponse)
def discover_schema(
    request: SchemaDiscoveryRequest, user: User = Depends(get_current_user)
) -> SchemaDiscoveryResponse:
    """Run schema analysis for the target database and persist the result."""
    try:
        target_url = build_mssql_url(request.connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = _get_session()
    try:
        user_id = _ensure_user(session, _resolve_attribution(request.requested_by_user_id, user))

        # Ensure a Server + Connection pair exists for this target. We persist
        # only public metadata (host, port, database, auth type); credentials
        # are not stored at this stage because the request only carries
        # short-lived credentials.
        from app.models.entities import Server

        server = session.execute(
            select(Server).where(Server.name == "ephemeral-discovery")
        ).scalar_one_or_none()
        if server is None:
            server = Server(
                name="ephemeral-discovery",
                host=request.connection.host,
                port=request.connection.port,
                auth_type=request.connection.auth_type,
            )
            session.add(server)
            session.flush()

        connection = session.execute(
            select(Connection).where(
                Connection.server_id == server.id,
                Connection.database_name == (request.connection.database or "master"),
            )
        ).scalar_one_or_none()
        if connection is None:
            connection = Connection(
                server_id=server.id,
                database_name=request.connection.database or "master",
                encrypted_connection_string="!ephemeral",
            )
            session.add(connection)
            session.flush()

        job = Job(
            connection_id=connection.id,
            requested_by_user_id=user_id,
            status="running",
            workflow_state={"phase": "schema_discovery"},
        )
        session.add(job)
        session.flush()

        discovery = MssqlDiscoveryClient(target_url, request.connection.timeout_seconds)
        reader = MssqlSchemaReader(target_url, request.connection.timeout_seconds)
        try:
            agent = SchemaAgent(session, discovery, reader)
            result = agent.discover(job.id, request.connection.database or "master")
        except Exception as exc:  # noqa: BLE001 - surface as 503
            session.rollback()
            raise HTTPException(
                status_code=503,
                # Driver errors can embed connection-string fragments.
                detail=f"Schema discovery failed: {redact_text(str(exc))}",
            ) from exc
        finally:
            discovery.dispose()
            reader.dispose()

        job.status = "completed"
        session.commit()
        counts = result.counts
        return SchemaDiscoveryResponse(
            job_id=job.id,
            table_count=counts.tables,
            column_count=counts.columns,
            foreign_key_count=counts.foreign_keys,
            index_count=counts.indexes,
            constraint_count=counts.constraints,
            cached=result.cached,
        )
    finally:
        session.close()


@router.get("/job/{job_id}", response_model=JobSchemaResponse)
def get_job_schema(job_id: str, user: User = Depends(get_current_user)) -> JobSchemaResponse:
    """Return the persisted schema metadata for a job."""
    session = _get_session()
    try:
        if session.get(Job, job_id) is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

        tables = (
            session.execute(
                select(DiscoveredTable)
                .where(DiscoveredTable.job_id == job_id)
                .order_by(DiscoveredTable.schema_name, DiscoveredTable.table_name)
            )
            .scalars()
            .all()
        )
        if not tables:
            return JobSchemaResponse(job_id=job_id, tables=[])

        table_ids = [t.id for t in tables]
        columns = (
            session.execute(
                select(DiscoveredColumn)
                .where(DiscoveredColumn.table_id.in_(table_ids))
                .order_by(DiscoveredColumn.table_id, DiscoveredColumn.ordinal_position)
            )
            .scalars()
            .all()
        )
        foreign_keys = (
            session.execute(
                select(DiscoveredForeignKey)
                .join(DiscoveredColumn, DiscoveredForeignKey.column_id == DiscoveredColumn.id)
                .where(DiscoveredColumn.table_id.in_(table_ids))
                .order_by(DiscoveredForeignKey.fk_name)
            )
            .scalars()
            .all()
        )
        indexes = (
            session.execute(
                select(DiscoveredIndex)
                .where(DiscoveredIndex.table_id.in_(table_ids))
                .order_by(DiscoveredIndex.name)
            )
            .scalars()
            .all()
        )
        constraints = (
            session.execute(
                select(DiscoveredConstraint)
                .where(DiscoveredConstraint.table_id.in_(table_ids))
                .order_by(DiscoveredConstraint.name)
            )
            .scalars()
            .all()
        )

        cols_by_table: dict[str, list[DiscoveredColumnOut]] = {tid: [] for tid in table_ids}
        col_id_to_table: dict[str, str] = {}
        for c in columns:
            cols_by_table[c.table_id].append(
                DiscoveredColumnOut(
                    id=c.id,
                    name=c.name,
                    ordinal_position=c.ordinal_position,
                    data_type=c.data_type,
                    is_nullable=c.is_nullable,
                    column_default=c.column_default,
                    character_maximum_length=c.character_maximum_length,
                    numeric_precision=c.numeric_precision,
                    numeric_scale=c.numeric_scale,
                    is_primary_key=c.is_primary_key,
                    is_foreign_key=c.is_foreign_key,
                )
            )
            col_id_to_table[c.id] = c.table_id

        fk_by_table: dict[str, list[DiscoveredForeignKeyOut]] = {tid: [] for tid in table_ids}
        for fk in foreign_keys:
            table_id = col_id_to_table.get(fk.column_id)
            if table_id is None:
                continue
            fk_by_table[table_id].append(
                DiscoveredForeignKeyOut(
                    fk_name=fk.fk_name,
                    column_name=_column_name(session, fk.column_id),
                    referenced_schema=fk.referenced_schema,
                    referenced_table=fk.referenced_table,
                    referenced_column=fk.referenced_column,
                )
            )

        idx_by_table: dict[str, list[DiscoveredIndexOut]] = {tid: [] for tid in table_ids}
        for idx in indexes:
            idx_by_table[idx.table_id].append(
                DiscoveredIndexOut(
                    name=idx.name,
                    is_unique=idx.is_unique,
                    is_primary=idx.is_primary,
                    column_names=list(idx.column_names),
                )
            )

        cons_by_table: dict[str, list[DiscoveredConstraintOut]] = {tid: [] for tid in table_ids}
        for c in constraints:
            cons_by_table[c.table_id].append(
                DiscoveredConstraintOut(name=c.name, constraint_type=c.constraint_type)
            )

        out_tables = [
            DiscoveredTableOut(
                id=t.id,
                schema_name=t.schema_name,
                table_name=t.table_name,
                object_type=t.object_type,
                discovered_at=t.discovered_at,
                columns=cols_by_table[t.id],
                foreign_keys=fk_by_table[t.id],
                indexes=idx_by_table[t.id],
                constraints=cons_by_table[t.id],
            )
            for t in tables
        ]
        return JobSchemaResponse(job_id=job_id, tables=out_tables)
    finally:
        session.close()


def _column_name(session: Session, column_id: str) -> str:
    """Resolve a discovered column's id back to its name (for FK output)."""
    col = session.get(DiscoveredColumn, column_id)
    return col.name if col is not None else ""
