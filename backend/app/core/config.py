"""Environment-backed application settings."""

import ipaddress
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Docker/Compose hostnames that resolve to the local machine or a
# sibling container. Everything else must be a loopback/private address
# or a single-label Docker service name.
_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }
)


def _is_local_hostname(hostname: str) -> bool:
    """Return ``True`` when *hostname* can only refer to a local service.

    Accepted forms:

    * ``localhost`` and the Docker desktop aliases
      (``host.docker.internal`` / ``gateway.docker.internal``)
    * loopback, link-local, or private IPv4/IPv6 literals
    * single-label Docker/Compose service names such as ``ollama``

    Public DNS names and public IP addresses are rejected so the
    platform cannot accidentally send sensitive schema/classification
    prompts to a cloud inference endpoint.
    """
    host = hostname.strip().strip("[]").lower().rstrip(".")
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal: accept only single-label (Docker service)
        # names. A dotted name is a DNS name and may be remote.
        return "." not in host
    return ip.is_loopback or ip.is_private or ip.is_link_local


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
    # Comma-separated list of browser origins allowed to call the API.
    # Example: "https://anonymizer.example.com,http://localhost:5173"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5174,http://localhost:5174"
    llm_provider: str = Field(
        default="ollama",
        pattern="^(ollama|mock)$",
        description=(
            "Which LLM client to use. ``ollama`` hits a local Ollama daemon; "
            "``mock`` returns deterministic fixture responses (CI / development "
            "without a model). Cloud providers are intentionally unsupported."
        ),
    )
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    backup_dir: str = "./backups"
    backup_keep: int = Field(default=5, ge=1, le=1000)
    workflow_approval_timeout_hours: int = Field(default=24 * 7, ge=1, le=24 * 30)
    safe_update_batch_size: int = Field(default=500, ge=1, le=100_000)

    @field_validator("ollama_base_url")
    @classmethod
    def _validate_ollama_base_url(cls, value: str) -> str:
        """Reject non-local inference endpoints (local-AI-only guarantee)."""
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        if not parsed.hostname:
            raise ValueError("OLLAMA_BASE_URL must include a hostname")
        if parsed.username or parsed.password:
            raise ValueError("OLLAMA_BASE_URL must not embed credentials")
        if not _is_local_hostname(parsed.hostname):
            raise ValueError(
                "OLLAMA_BASE_URL must point to a local/private endpoint "
                f"(got {parsed.hostname!r}). Cloud AI providers are not supported."
            )
        return raw.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse ``CORS_ORIGINS`` into a clean list of origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated settings object."""
    return Settings()
