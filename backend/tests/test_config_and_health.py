"""Configuration guards, CORS wiring, and health/readiness probes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


# ---------------------------------------------------------------------------
# Local-AI-only configuration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://192.168.1.10:11434",
        "http://10.0.0.5:11434",
        "http://ollama:11434",
        "http://host.docker.internal:11434",
    ],
)
def test_ollama_base_url_accepts_local_endpoints(app_env, url):
    settings = _settings(ollama_base_url=url)
    assert settings.ollama_base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://example.com:11434",
        "http://8.8.8.8:11434",
        "http://user:pass@localhost:11434",
        "ftp://localhost:11434",
        "localhost:11434",
    ],
)
def test_ollama_base_url_rejects_remote_or_malformed_endpoints(app_env, url):
    with pytest.raises(ValidationError):
        _settings(ollama_base_url=url)


def test_cloud_llm_provider_is_not_supported(app_env):
    with pytest.raises(ValidationError):
        _settings(llm_provider="openai")


def test_mock_and_ollama_providers_are_supported(app_env):
    assert _settings(llm_provider="ollama").llm_provider == "ollama"
    assert _settings(llm_provider="mock").llm_provider == "mock"


def test_cors_origins_are_parsed_from_comma_separated_env(app_env):
    settings = _settings(
        cors_origins="https://anonymizer.example.com, http://localhost:5173 ,",
    )
    assert settings.cors_origin_list == [
        "https://anonymizer.example.com",
        "http://localhost:5173",
    ]


# ---------------------------------------------------------------------------
# Health and readiness
# ---------------------------------------------------------------------------


def test_liveness_probe(app_env, client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readiness_probe_ok_when_database_answers(app_env, client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_readiness_probe_returns_503_without_leaking_error(app_env, client, monkeypatch):
    def _boom():
        raise RuntimeError("connection string with password=SECRET")

    monkeypatch.setattr("app.database.session.session_scope", _boom)
    r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "database": "unreachable"}
    assert "SECRET" not in r.text


# ---------------------------------------------------------------------------
# CORS middleware honours CORS_ORIGINS
# ---------------------------------------------------------------------------


def test_cors_middleware_allows_configured_origin(app_env, monkeypatch):
    from app.core import config as cfg
    from app.main import create_application

    monkeypatch.setenv("CORS_ORIGINS", "https://anonymizer.example.com")
    cfg.get_settings.cache_clear()
    app = create_application()
    r = TestClient(app).options(
        "/health",
        headers={
            "Origin": "https://anonymizer.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://anonymizer.example.com"


def test_cors_middleware_rejects_unconfigured_origin(app_env, monkeypatch):
    from app.core import config as cfg
    from app.main import create_application

    monkeypatch.setenv("CORS_ORIGINS", "https://anonymizer.example.com")
    cfg.get_settings.cache_clear()
    app = create_application()
    r = TestClient(app).options(
        "/health",
        headers={
            "Origin": "https://evil.example.net",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in r.headers


# ---------------------------------------------------------------------------
# Settings API no longer exposes cloud-provider fields
# ---------------------------------------------------------------------------


def test_settings_api_has_no_cloud_provider_fields(app_env, seed_user, auth_headers, client):
    uid, _ = seed_user("root", role="admin")
    r = client.get("/settings", headers=auth_headers(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "openai_model" not in body
    assert "openai_base_url" not in body
    assert body["ollama_model"] == "qwen2.5:7b-instruct"
