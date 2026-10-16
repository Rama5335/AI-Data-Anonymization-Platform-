"""Data sampling API.

The sampling agent reads from the target MSSQL using the same one-shot
URL pattern as the discovery endpoints. Results are cached per
``(job_id, table_id)`` and returned both for fresh samples and cached
re-reads.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.agents.sampling import SamplingAgent
from app.database.sampling import MssqlSampler
from app.database.session import session_scope
from app.database.url import build_mssql_url
from app.models.entities import DiscoveredTable, User
from app.security.redaction import redact_text
from app.schemas.sampling import SampleResultResponse, SampleTableRequest
from app.security.deps import get_current_user

router = APIRouter(prefix="/sampling", tags=["sampling"])


@router.post("/tables/{table_id}", response_model=SampleResultResponse)
def sample_table(
    table_id: str, request: SampleTableRequest, user: User = Depends(get_current_user)
) -> SampleResultResponse:
    """Fetch up to ``row_count`` rows from the discovered table and cache them."""
    try:
        target_url = build_mssql_url(request.connection)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = session_scope()
    try:
        table = session.get(DiscoveredTable, table_id)
        if table is None:
            raise HTTPException(status_code=404, detail=f"DiscoveredTable {table_id!r} not found.")

        sampler = MssqlSampler(target_url, request.connection.timeout_seconds)
        try:
            agent = SamplingAgent(session, sampler)
            result = agent.sample_table(
                job_id=table.job_id,
                table_id=table.id,
                row_count=request.row_count,
                max_bytes_per_value=request.max_bytes_per_value,
                excluded_column_names=request.excluded_column_names or None,
                include_views=request.include_views,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface any driver failure as 503
            # Driver errors can embed connection-string fragments.
            raise HTTPException(
                status_code=503, detail=f"Sampling failed: {redact_text(str(exc))}"
            ) from exc
        finally:
            sampler.dispose()

        return SampleResultResponse(
            job_id=table.job_id,
            table_id=table.id,
            schema_name=table.schema_name,
            table_name=table.table_name,
            row_count=result.row_count,
            sampled_columns=result.sampled_columns,
            max_bytes_per_value=result.max_bytes_per_value,
            rows=result.rows,
            cached=result.cached,
        )
    finally:
        session.close()
