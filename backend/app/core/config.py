"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration loaded only from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AI Data Anonymization Platform"
    app_env: str = Field(default="development", pattern="^(development|test|production)$")
    log_level: str = "INFO"
    database_url: SecretStr
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = Field(default=30, ge=1, le=1_440)
    credential_encryption_key: SecretStr
    llm_provider: str = Field(
        default="ollama",
        pattern="^(ollama|openai|mock)$",
        description=(
            "Which LLM client to use. ``ollama`` hits a local Ollama daemon; "
            "``openai`` calls the OpenAI Chat Completions API (or any "
            "OpenAI-compatible endpoint); ``mock`` returns deterministic "
            "fixture responses (CI / development without a model)."
        ),
    )
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    openai_api_key: SecretStr = Field(default="")
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    backup_dir: str = "./backups"
    backup_keep: int = Field(default=5, ge=1, le=1000)
    workflow_approval_timeout_hours: int = Field(default=24 * 7, ge=1, le=24 * 30)
    safe_update_batch_size: int = Field(default=500, ge=1, le=100_000)


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated settings object."""
    return Settings()
