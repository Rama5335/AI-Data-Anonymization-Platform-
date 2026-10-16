"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from app.logging import configure_logging


def create_application() -> FastAPI:
    """Create the API scaffold with all routes including workflow + auth."""
    configure_logging()
    application = FastAPI(
        title="AI Data Anonymization Platform",
        version="0.1.0",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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


@app.get("/health", include_in_schema=False)
def health_check() -> dict[str, str]:
    """Report service liveness without exposing configuration or secrets."""
    return {"status": "ok"}
