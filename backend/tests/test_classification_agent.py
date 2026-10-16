"""Unit tests for the classification agent, the API, and the LLM client."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.classification.llm_client import (
    LLMResponseError,
    LLMTimeout,
    MockLLMClient,
    _extract_json_object,
)
from app.core.config import get_settings
from app.main import app
from app.models import Base
from app.schemas.classification import (
    LLMClassification,
    coerce_dict_to_classification,
)
from app.security.tokens import create_access_token

client = TestClient(app)


def _auth_token(user_id: str) -> dict[str, str]:
    settings = get_settings()
    token = create_access_token(
        subject=user_id,
        secret=settings.jwt_secret_key.get_secret_value(),
        expires_in_minutes=10,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _prepare_env(monkeypatch, tmp_path, name: str, llm_provider: str = "mock") -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-with-at-least-32-bytes-xx")
    monkeypatch.setenv(
        "CREDENTIAL_ENCRYPTION_KEY",
        "O7ZpK9d3v8s2eRfTjYwHxLqBnVcMgUaI0pEhKlS9XzAo=",
    )
    monkeypatch.setenv("LLM_PROVIDER", llm_provider)
    from app.core import config as config_module
    from app.database import session as session_module

    config_module.get_settings.cache_clear()
    session_module.get_engine.cache_clear()
    session_module.get_session_factory.cache_clear()
    (
        session_module.session_scope.cache_clear()
        if hasattr(session_module.session_scope, "cache_clear")
        else None
    )
    from app.database.session import get_engine

    Base.metadata.create_all(get_engine())


def _bootstrap_with_sample(
    monkeypatch, tmp_path, llm_provider: str = "mock"
) -> tuple[str, str, str, str, MockLLMClient]:
    """Create user, server, connection, job, table, columns, and a sample.

    Returns (job_id, table_id, email_column_id, user_id, mock_client).
    """
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope
    from app.models.entities import (
        Connection,
        DiscoveredColumn,
        DiscoveredSample,
        DiscoveredTable,
        Job,
        Server,
        User,
    )

    _prepare_env(monkeypatch, tmp_path, "classification_agent.db", llm_provider)
    mock = get_mock_client()
    mock.reset()
    mock.set_response(
        "email",
        "nvarchar",
        {
            "sensitivity": "Email",
            "confidence": 0.97,
            "reasoning": "Column name and sample values look like email addresses.",
            "suggested_strategy": "Fake Email",
        },
    )
    mock.set_response(
        "fullname",
        "nvarchar",
        {
            "sensitivity": "Name",
            "confidence": 0.93,
            "reasoning": "Column name is FullName; samples are personal names.",
            "suggested_strategy": "Fake Name",
        },
    )
    mock.set_response(
        "id",
        "int",
        {
            "sensitivity": "Non Sensitive",
            "confidence": 0.9,
            "reasoning": "Integer primary key; not PII.",
            "suggested_strategy": "Preserve Format",
        },
    )

    with session_scope() as session:
        user = User(username="classifier", password_hash="!stub", is_active=True)
        session.add(user)
        session.flush()
        server = Server(name="ephemeral-discovery", host="mssql.local", port=1433, auth_type="sql")
        session.add(server)
        session.flush()
        connection = Connection(
            server_id=server.id,
            database_name="appdb",
            encrypted_connection_string="!stub",
        )
        session.add(connection)
        session.flush()
        job = Job(
            connection_id=connection.id,
            requested_by_user_id=user.id,
            status="running",
            workflow_state={},
        )
        session.add(job)
        session.flush()
        table = DiscoveredTable(
            job_id=job.id,
            schema_name="dbo",
            table_name="Customers",
            object_type="BASE TABLE",
        )
        session.add(table)
        session.flush()

        columns: dict[str, str] = {}
        for ord_, (name, dt, length) in enumerate(
            [
                ("Id", "int", None),
                ("FullName", "nvarchar", 200),
                ("Email", "nvarchar", 255),
            ],
            start=1,
        ):
            col = DiscoveredColumn(
                table_id=table.id,
                name=name,
                ordinal_position=ord_,
                data_type=dt,
                is_nullable=False,
                character_maximum_length=length,
            )
            session.add(col)
            session.flush()
            columns[name] = col.id

        sample_rows = [
            {
                "Id": {"value": 1, "truncated": False, "original_length": None},
                "FullName": {"value": "Ada Lovelace", "truncated": False, "original_length": 12},
                "Email": {"value": "ada@example.com", "truncated": False, "original_length": 15},
            },
            {
                "Id": {"value": 2, "truncated": False, "original_length": None},
                "FullName": {"value": "Grace Hopper", "truncated": False, "original_length": 12},
                "Email": {"value": "grace@example.com", "truncated": False, "original_length": 17},
            },
        ]
        sample = DiscoveredSample(
            job_id=job.id,
            table_id=table.id,
            row_count=2,
            sampled_columns=["Id", "FullName", "Email"],
            max_bytes_per_value=512,
            rows=sample_rows,
        )
        session.add(sample)
        session.commit()
        return job.id, table.id, columns["Email"], user.id, mock


def test_extract_json_object_handles_bare_json() -> None:
    assert _extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_handles_fenced_json() -> None:
    body = '```json\n{"a": 2}\n```'
    assert _extract_json_object(body) == {"a": 2}


def test_extract_json_object_handles_prose_wrapped_json() -> None:
    body = 'Here you go:\n{"a": 3}\nThanks.'
    assert _extract_json_object(body) == {"a": 3}


def test_extract_json_object_rejects_empty() -> None:
    with pytest.raises(LLMResponseError):
        _extract_json_object("")


def test_extract_json_object_rejects_no_json() -> None:
    with pytest.raises(LLMResponseError):
        _extract_json_object("I cannot help with that.")


def test_coerce_dict_to_classification_accepts_canonical() -> None:
    out = coerce_dict_to_classification(
        {
            "sensitivity": "Email",
            "confidence": 0.9,
            "reasoning": "r",
            "suggested_strategy": "Fake Email",
        }
    )
    assert isinstance(out, LLMClassification)
    assert out.sensitivity == "Email"
    assert out.suggested_strategy == "Fake Email"


def test_coerce_dict_to_classification_accepts_lowercase_keys() -> None:
    out = coerce_dict_to_classification(
        {
            "SENSITIVITY": "Name",
            "CONFIDENCE": 0.5,
            "REASONING": "r",
            "SUGGESTED_STRATEGY": "Fake Name",
        }
    )
    assert out.sensitivity == "Name"


def test_coerce_dict_to_classification_rejects_unknown_sensitivity() -> None:
    with pytest.raises(Exception):
        coerce_dict_to_classification(
            {
                "sensitivity": "Galactic Passport",
                "confidence": 0.5,
                "reasoning": "r",
                "suggested_strategy": "Hash",
            }
        )


def test_mock_client_routes_by_column_name() -> None:
    mock = MockLLMClient()
    mock.set_response(
        "email",
        "nvarchar",
        {
            "sensitivity": "Email",
            "confidence": 0.9,
            "reasoning": "r",
            "suggested_strategy": "Fake Email",
        },
    )
    out = mock.complete_json(
        "system",
        "table: dbo.Customers\ncolumn: Email\ndata_type: nvarchar",
    )
    assert out["sensitivity"] == "Email"


def test_classification_agent_classifies_one_column(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope

    job_id, _, email_col_id, _user_id, shared = _bootstrap_with_sample(monkeypatch, tmp_path)
    # Reuse the same shared mock so the fixtures the bootstrap set are
    # visible to the agent. (Both factory and agent call go through the
    # cached mock now.)
    with session_scope() as session:
        agent = ClassificationAgent(session, shared)
        outcome = agent.classify_column(job_id, email_col_id)

    assert outcome.ok is True
    assert outcome.sensitivity == "Email"
    assert outcome.suggested_strategy == "Fake Email"
    assert outcome.attempts == 1
    assert outcome.error is None
    get_mock_client.cache_clear()  # isolate the next test


def test_classification_agent_retries_on_malformed_response(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope

    job_id, _, email_col_id, _user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    shared = get_mock_client()
    # First two responses are genuinely malformed (no JSON at all); the
    # third is a clean JSON object. The parser should retry twice and
    # then succeed.
    shared.set_raw_response("I cannot help with that.")
    shared.set_raw_response("I still cannot help.")
    shared.set_raw_response(
        '{"sensitivity":"Email","confidence":0.91,"reasoning":"r","suggested_strategy":"Fake Email"}'
    )
    with session_scope() as session:
        agent = ClassificationAgent(session, shared)
        outcome = agent.classify_column(job_id, email_col_id, max_retries=5)
    assert outcome.ok is True
    assert outcome.attempts == 3
    get_mock_client.cache_clear()


def test_classification_agent_surfaces_failure_after_retries(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope

    job_id, _, email_col_id, _user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    shared = get_mock_client()
    shared.set_raw_response("nope")
    shared.set_raw_response("still nope")
    shared.set_raw_response("definitely nope")
    with session_scope() as session:
        agent = ClassificationAgent(session, shared)
        outcome = agent.classify_column(job_id, email_col_id, max_retries=3)
    assert outcome.ok is False
    assert outcome.attempts == 3
    assert outcome.error is not None
    get_mock_client.cache_clear()


def test_classification_agent_surfaces_transport_error(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope

    job_id, _, email_col_id, _user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    shared = get_mock_client()
    shared.set_error(LLMTimeout("Ollama request timed out after 120s"))
    with session_scope() as session:
        agent = ClassificationAgent(session, shared)
        outcome = agent.classify_column(job_id, email_col_id)
    assert outcome.ok is False
    assert "timed out" in (outcome.error or "")
    get_mock_client.cache_clear()


def test_classification_agent_upserts_on_second_call(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope
    from app.models.entities import ClassificationResult
    from sqlalchemy import select

    job_id, _, email_col_id, _user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    shared = get_mock_client()
    # First call: change the mock to a different response.
    shared.set_response(
        "email",
        "nvarchar",
        {
            "sensitivity": "Email",
            "confidence": 0.9,
            "reasoning": "r1",
            "suggested_strategy": "Fake Email",
        },
    )
    with session_scope() as session:
        first = ClassificationAgent(session, shared).classify_column(job_id, email_col_id)
        first_id = first.classification_id

    shared.set_response(
        "email",
        "nvarchar",
        {
            "sensitivity": "Email",
            "confidence": 0.99,
            "reasoning": "r2",
            "suggested_strategy": "Hash",
        },
    )
    with session_scope() as session:
        second = ClassificationAgent(session, shared).classify_column(job_id, email_col_id)
        assert second.classification_id == first_id
        assert second.reasoning == "r2"
        assert second.suggested_strategy == "Hash"

    with session_scope() as session:
        rows = list(
            session.execute(
                select(ClassificationResult).where(ClassificationResult.id == first_id)
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].confidence == 0.99
    get_mock_client.cache_clear()


def test_classification_agent_classify_table(monkeypatch, tmp_path) -> None:
    from app.agents.classification import ClassificationAgent
    from app.classification.factory import get_mock_client
    from app.database.session import session_scope

    job_id, table_id, _, _user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    shared = get_mock_client()
    with session_scope() as session:
        outcomes = ClassificationAgent(session, shared).classify_table(job_id, table_id)
    names = {o.column_name for o in outcomes}
    assert names == {"Id", "FullName", "Email"}
    by_name = {o.column_name: o for o in outcomes}
    assert by_name["Email"].sensitivity == "Email"
    assert by_name["FullName"].sensitivity == "Name"
    assert by_name["Id"].sensitivity == "Non Sensitive"
    for o in outcomes:
        assert o.ok is True
    get_mock_client.cache_clear()


def test_classification_endpoint_classifies_one_column(monkeypatch, tmp_path) -> None:
    job_id, _, email_col_id, user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    response = client.post(
        f"/classification/columns/{email_col_id}",
        headers=_auth_token(user_id),
        json={"max_retries": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == job_id
    assert body["classified_count"] == 1
    assert body["failed_count"] == 0
    assert body["results"][0]["sensitivity"] == "Email"
    assert body["results"][0]["ok"] is True


def test_classification_endpoint_classifies_whole_job(monkeypatch, tmp_path) -> None:
    job_id, _, _, user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    response = client.post(
        f"/classification/jobs/{job_id}", headers=_auth_token(user_id), json={"max_retries": 2}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == job_id
    assert body["classified_count"] == 3
    assert body["failed_count"] == 0
    by_name = {r["column_name"]: r for r in body["results"]}
    assert by_name["Email"]["sensitivity"] == "Email"
    assert by_name["FullName"]["sensitivity"] == "Name"
    assert by_name["Id"]["sensitivity"] == "Non Sensitive"


def test_classification_endpoint_returns_job_classifications(monkeypatch, tmp_path) -> None:
    job_id, _, email_col_id, user_id, _ = _bootstrap_with_sample(monkeypatch, tmp_path)
    client.post(
        f"/classification/columns/{email_col_id}",
        headers=_auth_token(user_id),
        json={"max_retries": 2},
    )
    response = client.get(f"/classification/jobs/{job_id}", headers=_auth_token(user_id))
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert len(body["results"]) >= 1


def test_classification_endpoint_rejects_unknown_column(tmp_path, monkeypatch) -> None:
    _prepare_env(monkeypatch, tmp_path, "unknown_column.db")
    from app.database.session import session_scope
    from app.models.entities import User

    with session_scope() as session:
        user = User(username="testuser", password_hash="!stub", is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id
    response = client.post(
        "/classification/columns/00000000-0000-0000-0000-000000000000",
        headers=_auth_token(user_id),
        json={"max_retries": 2},
    )
    assert response.status_code == 404


def test_ollama_client_sends_format_json(monkeypatch) -> None:
    """Black-box: confirm the OllamaClient issues the expected JSON payload."""
    captured: dict[str, Any] = {}

    class _FakeResp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "message": {
                    "content": '{"sensitivity": "Email", "confidence": 0.9, "reasoning": "r", "suggested_strategy": "Fake Email"}'
                }
            }

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResp()

    monkeypatch.setattr("app.classification.llm_client.requests.post", fake_post)
    from app.classification.llm_client import OllamaClient

    client_obj = OllamaClient("http://localhost:11434", "qwen2.5:7b-instruct", timeout_seconds=5)
    out = client_obj.complete_json("system-prompt", "user-prompt")

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["json"]["format"] == "json"
    assert captured["json"]["model"] == "qwen2.5:7b-instruct"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["role"] == "user"
    assert out["sensitivity"] == "Email"


def test_ollama_client_maps_timeout(monkeypatch) -> None:
    import requests as req

    def fake_post(*a, **kw):
        raise req.Timeout("slow")

    monkeypatch.setattr("app.classification.llm_client.requests.post", fake_post)
    from app.classification.llm_client import LLMTimeout, OllamaClient

    c = OllamaClient("http://localhost:11434", "x", timeout_seconds=1)
    with pytest.raises(LLMTimeout):
        c.complete_json("s", "u")


def test_ollama_client_maps_connection_error(monkeypatch) -> None:
    import requests as req

    def fake_post(*a, **kw):
        raise req.ConnectionError("nope")

    monkeypatch.setattr("app.classification.llm_client.requests.post", fake_post)
    from app.classification.llm_client import LLMUnavailable, OllamaClient

    c = OllamaClient("http://localhost:11434", "x", timeout_seconds=1)
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u")


def test_prompt_template_includes_no_sql_directive() -> None:
    """The prompt must explicitly forbid SQL generation."""
    from app.classification.prompts import SYSTEM_PROMPT, build_user_prompt

    assert "DO NOT generate SQL" in SYSTEM_PROMPT
    user = build_user_prompt(
        schema_name="dbo",
        table_name="Customers",
        column_name="Email",
        data_type="nvarchar",
        is_nullable=False,
        sample_values=[
            {"value": "ada@example.com", "truncated": False, "original_length": 15},
        ],
    )
    assert "Email" in user
    assert "ada@example.com" in user
