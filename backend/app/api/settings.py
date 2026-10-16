"""Settings API — read-only view of backend configuration."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import get_settings
from app.models.entities import User
from app.security.deps import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    llm_provider: str
    ollama_model: str
    ollama_base_url: str
    openai_model: str
    openai_base_url: str
    backup_dir: str
    backup_keep: int
    safe_update_batch_size: int
    credential_encryption_key_set: bool


@router.get("", response_model=SettingsOut)
def get_settings_view(
    user: User = Depends(get_current_user),
) -> SettingsOut:
    s = get_settings()
    return SettingsOut(
        llm_provider=s.llm_provider,
        ollama_model=s.ollama_model,
        ollama_base_url=s.ollama_base_url,
        openai_model=s.openai_model,
        openai_base_url=s.openai_base_url,
        backup_dir=s.backup_dir,
        backup_keep=s.backup_keep,
        safe_update_batch_size=s.safe_update_batch_size,
        credential_encryption_key_set=bool(s.credential_encryption_key),
    )
