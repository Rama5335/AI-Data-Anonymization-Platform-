"""Factory for the LLM client.

Picks ``OllamaClient`` or ``MockLLMClient`` based on
``Settings.llm_provider``. Centralised here so the API and the
validation script build the same client.

The mock client can be pre-populated from a JSON file pointed to by
``LLM_MOCK_FIXTURES`` (used by ``validate_phase6.ps1``). Each entry
in the file is::

    {"column_name": "email", "data_type": "nvarchar", "response": {...}}

The factory loads the file on first use, idempotently.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from app.classification.llm_client import (
    LLMClient,
    MockLLMClient,
    OllamaClient,
    OpenAIClient,
)
from app.core.config import Settings, get_settings


_FAKE_KEYS = {
    "id": "Non Sensitive",
    "guid": "Non Sensitive",
    "createdat": "Non Sensitive",
    "updatedat": "Non Sensitive",
    "total": "Non Sensitive",
    "amount": "Non Sensitive",
    "count": "Non Sensitive",
    "status": "Non Sensitive",
}


def _default_fixture_for(column_name: str, data_type: str) -> dict | None:
    """Cheap keyword-based fixture for the mock provider.

    Lets the validator (and ad-hoc exploration) get plausible results
    without hand-curating fixtures for every column.
    """
    cn = column_name.lower()
    if cn in _FAKE_KEYS:
        return None  # Let the fixture be Non Sensitive (the mock default).
    if "email" in cn:
        return {
            "sensitivity": "Email",
            "confidence": 0.95,
            "reasoning": "Column name suggests an email address.",
            "suggested_strategy": "Fake Email",
        }
    if "name" in cn or cn == "fullname":
        return {
            "sensitivity": "Name",
            "confidence": 0.92,
            "reasoning": "Column name suggests a personal name.",
            "suggested_strategy": "Fake Name",
        }
    if "phone" in cn:
        return {
            "sensitivity": "Phone",
            "confidence": 0.9,
            "reasoning": "Column name suggests a phone number.",
            "suggested_strategy": "Masked Phone",
        }
    if "address" in cn:
        return {
            "sensitivity": "Address",
            "confidence": 0.9,
            "reasoning": "Column name suggests a postal address.",
            "suggested_strategy": "Preserve Format",
        }
    if "birth" in cn or "dob" in cn:
        return {
            "sensitivity": "Birth Date",
            "confidence": 0.85,
            "reasoning": "Column name suggests a date of birth.",
            "suggested_strategy": "Preserve Format",
        }
    if "ssn" in cn or "nationalid" in cn or "national_id" in cn:
        return {
            "sensitivity": "National ID",
            "confidence": 0.95,
            "reasoning": "Column name suggests a national identifier.",
            "suggested_strategy": "Hash",
        }
    if "iban" in cn or "bank" in cn:
        return {
            "sensitivity": "IBAN",
            "confidence": 0.9,
            "reasoning": "Column name suggests a bank account number.",
            "suggested_strategy": "Tokenization",
        }
    if "credit" in cn or "card" in cn or "ccv" in cn or "cvv" in cn:
        return {
            "sensitivity": "Credit Card",
            "confidence": 0.95,
            "reasoning": "Column name suggests payment card data.",
            "suggested_strategy": "Tokenization",
        }
    if "passport" in cn:
        return {
            "sensitivity": "Passport",
            "confidence": 0.9,
            "reasoning": "Column name suggests a passport number.",
            "suggested_strategy": "Hash",
        }
    if "salary" in cn or "wage" in cn or "income" in cn:
        return {
            "sensitivity": "Salary",
            "confidence": 0.9,
            "reasoning": "Column name suggests a financial figure.",
            "suggested_strategy": "Random Replacement",
        }
    if "company" in cn or "employer" in cn:
        return {
            "sensitivity": "Company Name",
            "confidence": 0.85,
            "reasoning": "Column name suggests an employer / company name.",
            "suggested_strategy": "Fake Name",
        }
    return None


def build_llm_client(settings: Settings | None = None) -> LLMClient:
    """Build the configured LLM client.

    The Ollama case is intentionally not cached — it holds an HTTP
    connection whose lifespan should match the request. The mock case
    *is* a process-singleton so tests can pre-load fixtures via
    :func:`get_mock_client` and have them reach the API endpoint.
    """
    settings = settings or get_settings()
    if settings.llm_provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_request_timeout_seconds,
        )
    if settings.llm_provider == "openai":
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise ValueError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY to be set in the environment."
            )
        return OpenAIClient(
            api_key=api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.openai_request_timeout_seconds,
        )
    if settings.llm_provider == "mock":
        return get_mock_client()
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")


@lru_cache
def get_mock_client() -> MockLLMClient:
    """A process-singleton MockLLMClient for tests.

    Real LLM clients are not cached because they hold an HTTP
    connection; the mock holds only in-memory state and benefits from
    a single shared instance for ergonomic test setup.
    """
    client = MockLLMClient()
    _load_fixtures(client)
    _load_default_heuristics(client)
    return client


def _load_fixtures(client: MockLLMClient) -> None:
    """Load fixtures from ``LLM_MOCK_FIXTURES`` (JSON list), if set."""
    path = os.environ.get("LLM_MOCK_FIXTURES")
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cn = entry.get("column_name")
        dt = entry.get("data_type")
        resp = entry.get("response")
        if not (isinstance(cn, str) and isinstance(dt, str) and isinstance(resp, dict)):
            continue
        client.set_response(cn, dt, resp)


def _load_default_heuristics(client: MockLLMClient) -> None:
    """Pre-populate the mock with the built-in keyword heuristics.

    Done once at process start; if a column doesn't match, the mock
    falls back to its built-in default of "Non Sensitive".
    """
    for column in ("Id", "TotalCents", "Count", "Status"):
        for dtype in ("int", "bigint", "decimal", "bit", "datetime2"):
            client.set_response(
                column,
                dtype,
                {
                    "sensitivity": "Non Sensitive",
                    "confidence": 0.9,
                    "reasoning": "Identifier or non-PII column.",
                    "suggested_strategy": "Preserve Format",
                },
            )
    for col, dt in [
        ("Email", "nvarchar"),
        ("Phone", "nvarchar"),
        ("Address", "nvarchar"),
        ("BirthDate", "date"),
        ("DOB", "date"),
        ("Salary", "decimal"),
        ("Passport", "nvarchar"),
        ("NationalId", "nvarchar"),
        ("IBAN", "nvarchar"),
        ("FullName", "nvarchar"),
        ("CompanyName", "nvarchar"),
    ]:
        fixture = _default_fixture_for(col, dt)
        if fixture is not None:
            client.set_response(col, dt, fixture)
