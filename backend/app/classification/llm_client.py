"""LLM client abstractions.

Three implementations:

* ``OllamaClient`` — talks to a local Ollama daemon over HTTP using
  ``POST /api/chat`` with ``format: "json"`` to force JSON-mode output.
* ``OpenAIClient`` — talks to the OpenAI Chat Completions API (or any
  OpenAI-compatible endpoint) over HTTP using ``response_format``
  ``json_object`` to force JSON-mode output. Used when
  ``LLM_PROVIDER=openai``.
* ``MockLLMClient`` — deterministic, fixture-driven; used in tests and
  when ``LLM_PROVIDER=mock``. Can be configured to fail in controlled
  ways so the retry/validation pipeline is exercised end-to-end.

The contract is intentionally narrow: ``complete_json(system, user)`` is
the only public method on every client, returning a parsed JSON dict or
raising a typed error. **Nothing in this module ever executes LLM output
as SQL or shell.**
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import requests
from loguru import logger


class LLMError(Exception):
    """Base class for LLM client errors."""


class LLMTimeout(LLMError):
    """The LLM took longer than the configured timeout."""


class LLMUnavailable(LLMError):
    """The LLM service is unreachable or refused the request."""


class LLMResponseError(LLMError):
    """The LLM returned something we cannot use (empty body, non-JSON, etc.)."""


class LLMClient(ABC):
    """Abstract base for LLM clients.

    Every implementation must return a parsed JSON dict from
    ``complete_json``. Validation against a downstream schema happens
    in the agent layer, not here.
    """

    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Send a chat to the LLM and return the parsed JSON body."""

    def anonymize_batch(
        self,
        system: str,
        user: str,
    ) -> list[str]:
        """Send a batch anonymization request; return a list of anonymized values.

        Default implementation calls ``complete_json`` and extracts a
        ``results`` list. Subclasses can override for streaming or
        batch-optimized endpoints.
        """
        body = self.complete_json(system, user)
        results = body.get("results")
        if not isinstance(results, list):
            raise LLMResponseError(
                f"Anonymization response missing 'results' list: {list(body.keys())}"
            )
        return [str(v) if v is not None else "" for v in results]


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a possibly noisy LLM response.

    Handles three common shapes:

    * Bare JSON: ``{"sensitivity": "Email", ...}``
    * Fenced JSON: ``\\`\\`\\`json ... \\`\\`\\`\\``
    * JSON with prose around it (best effort: find the first ``{`` and
      parse from there to the matching ``}``).
    """
    text = text.strip()
    if not text:
        raise LLMResponseError("LLM returned an empty response body.")

    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()

    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Last resort: find the first '{' and the matching '}'.
    start = text.find("{")
    if start == -1:
        raise LLMResponseError(f"LLM response contained no JSON object: {text[:200]!r}")
    depth = 0
    for end in range(start, len(text)):
        ch = text[end]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    raise LLMResponseError(f"LLM response had no parseable JSON: {exc}") from exc
    raise LLMResponseError("LLM response had no balanced JSON object.")


class OllamaClient(LLMClient):
    """Ollama HTTP client (https://github.com/ollama/ollama/blob/main/docs/api.md).

    Uses ``POST /api/chat`` with ``format: "json"`` to coerce JSON-mode
    output, and the ``qwen2.5`` model's tendency to obey system
    prompts. The response body's ``message.content`` is then handed to
    ``_extract_json_object`` for a final defensive parse.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 120,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        url = f"{self._base_url}/api/chat"
        payload = {
            "model": self._model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
        except requests.Timeout as exc:
            raise LLMTimeout(
                f"Ollama request timed out after {self._timeout}s " f"(model={self._model})"
            ) from exc
        except requests.ConnectionError as exc:
            raise LLMUnavailable(f"Cannot reach Ollama at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            raise LLMUnavailable(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]!r}")

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Ollama returned a non-JSON envelope: {exc}") from exc

        content = (body.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("Ollama returned an empty message.content.")

        try:
            return _extract_json_object(content)
        except LLMResponseError as exc:
            logger.warning(
                "Ollama response was not strict JSON; extracted best-effort: {}",
                exc,
            )
            raise


class OpenAIClient(LLMClient):
    """OpenAI Chat Completions client.

    Uses ``POST /v1/chat/completions`` with ``response_format: {"type":
    "json_object"}`` to coerce JSON-mode output. Works with the official
    OpenAI API and any OpenAI-compatible gateway that honors
    ``response_format``.

    The response body's ``choices[0].message.content`` is handed to
    ``_extract_json_object`` for a final defensive parse.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self._timeout)
        except requests.Timeout as exc:
            raise LLMTimeout(
                f"OpenAI request timed out after {self._timeout}s (model={self._model})"
            ) from exc
        except requests.ConnectionError as exc:
            raise LLMUnavailable(f"Cannot reach OpenAI at {self._base_url}: {exc}") from exc

        if resp.status_code != 200:
            raise LLMUnavailable(f"OpenAI returned HTTP {resp.status_code}: {resp.text[:200]!r}")

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"OpenAI returned a non-JSON envelope: {exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                f"OpenAI response missing choices[0].message.content: {exc}"
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("OpenAI returned an empty message.content.")

        try:
            return _extract_json_object(content)
        except LLMResponseError as exc:
            logger.warning(
                "OpenAI response was not strict JSON; extracted best-effort: {}",
                exc,
            )
            raise


class MockLLMClient(LLMClient):
    """Deterministic, fixture-driven LLM client.

    Configure a fixed JSON body per (column_name, data_type) pair via
    :py:meth:`set_response`. If a call does not match a registered
    fixture, a default response is returned. Tests can also use
    :py:meth:`set_raw_response` to feed back malformed text and
    exercise the parser/retry path, or :py:meth:`set_error` to fail
    a specific call with a typed LLMError.
    """

    def __init__(self, default_response: dict[str, Any] | None = None) -> None:
        self._fixtures: dict[tuple[str, str], dict[str, Any]] = {}
        self._raw_responses: list[str] = []
        self._errors: list[LLMError] = []
        self._call_log: list[dict[str, str]] = []
        self._default = default_response or {
            "sensitivity": "Non Sensitive",
            "confidence": 0.5,
            "reasoning": "No signal: default mock response.",
            "suggested_strategy": "Preserve Format",
        }

    def reset(self) -> None:
        """Clear all fixtures, queued responses, queued errors, and call log.

        Intended for test setup: bootstrap functions call this to make
        the cached mock start with a known-clean state.
        """
        self._fixtures.clear()
        self._raw_responses.clear()
        self._errors.clear()
        self._call_log.clear()

    def set_response(
        self,
        column_name: str,
        data_type: str,
        response: dict[str, Any],
    ) -> None:
        self._fixtures[(column_name.lower(), data_type.lower())] = response

    def set_raw_response(self, raw: str) -> None:
        """Queue a raw response string for the next call (for retry tests)."""
        self._raw_responses.append(raw)

    def set_error(self, error: LLMError) -> None:
        """Queue a typed error for the next call."""
        self._errors.append(error)

    @property
    def call_log(self) -> list[dict[str, str]]:
        """Inspection helper for tests."""
        return list(self._call_log)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self._call_log.append({"system": system, "user": user})

        if self._errors:
            err = self._errors.pop(0)
            raise err

        if self._raw_responses:
            raw = self._raw_responses.pop(0)
            return _extract_json_object(raw)

        # Match against fixtures by scanning the user prompt for the
        # pattern ``column: <name>`` and ``data_type: <name>``.
        col_match = re.search(r"column:\s*([^\n,;]+)", user, re.IGNORECASE)
        dt_match = re.search(r"data_type:\s*([^\n,;]+)", user, re.IGNORECASE)
        col = (col_match.group(1).strip() if col_match else "").lower()
        dt = (dt_match.group(1).strip() if dt_match else "").lower()
        fixture = self._fixtures.get((col, dt))
        if fixture is not None:
            return dict(fixture)
        for (fc, fdt), resp in self._fixtures.items():
            if fc == col or fdt == dt:
                return dict(resp)
        return dict(self._default)
