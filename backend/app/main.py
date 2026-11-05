"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.api.approvals import router as approvals_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.backups import router as backups_router
from app.api.classification import router as classification_router
from app.api.discovery import router as discovery_router
from app.api.masking import router as masking_router
from app.api.sampling import router as sampling_router
from app.api.schema import router as schema_router
from app.api.servers import router as servers_router
from app.api.settings import router as settings_router
from app.api.updates import router as updates_router
from app.api.workflow import router as workflow_router
from app.core.config import get_settings
from app.logging import configure_logging


def health_check() -> dict[str, str]:
    """Liveness probe: the process is up and serving HTTP.

    Does not touch the database or any external service, so an
    orchestrator can distinguish "process alive" from "dependencies
    ready" (see ``/health/ready``).
    """
    return {"status": "ok"}


def readiness_check() -> JSONResponse:
    """Readiness probe: the metadata database answers a trivial query.

    Returns HTTP 503 when the database is unreachable so a container
    orchestrator or load balancer does not send traffic to an instance
    that cannot authenticate users or read jobs. The error detail is
    intentionally generic; connection strings and driver text are
    never returned.
    """
    from sqlalchemy import text

    from app.database.session import session_scope

    try:
        session = session_scope()
        try:
            session.execute(text("SELECT 1"))
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - readiness must not leak driver text
        logger.error("Readiness check failed: {}", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unreachable"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "database": "ok"},
    )


def create_application() -> FastAPI:
    """Create the API scaffold with all routes including workflow + auth."""
    configure_logging()
    settings = get_settings()
    application = FastAPI(
        title="AI Data Anonymization Platform",
        version="0.1.0",
    )
    # Origins are environment-driven so a production deployment is not
    # limited to the Vite development ports. See CORS_ORIGINS in
    # .env.example / docs/RUNBOOK.md.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_api_route("/health", health_check, methods=["GET"], include_in_schema=False)
    application.add_api_route(
        "/health/ready", readiness_check, methods=["GET"], include_in_schema=False
    )
    application.include_router(auth_router)
    application.include_router(discovery_router)
    application.include_router(schema_router)
    application.include_router(sampling_router)
    application.include_router(classification_router)
    application.include_router(approvals_router)
    application.include_router(backups_router)
    application.include_router(masking_router)
    application.include_router(updates_router)
    application.include_router(audit_router)
    application.include_router(workflow_router)
    application.include_router(servers_router)
    application.include_router(settings_router)
    return application


app = create_application()
