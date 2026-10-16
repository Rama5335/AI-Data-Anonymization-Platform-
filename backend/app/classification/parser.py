"""LLM response parser + retry orchestration.

The contract is:

* Call ``LLMClient.complete_json(system, user)`` once.
* If the response parses and validates against ``LLMClassification``,
  return it.
* Otherwise, send a follow-up ``build_repair_prompt`` message that
  shows the LLM its own previous body and the validation error.
* Retry up to ``max_retries`` times.
* If every attempt fails, return a typed :class:`ClassificationFailure`
  so the agent can persist the failure and keep the workflow going.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic import ValidationError

from app.classification.llm_client import (
    LLMClient,
    LLMError,
    LLMResponseError,
    LLMTimeout,
    LLMUnavailable,
)
from app.classification.prompts import (
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.schemas.classification import (
    LLMClassification,
    coerce_dict_to_classification,
)


@dataclass(frozen=True)
class ClassificationSuccess:
    classification: LLMClassification
    attempts: int
    raw_response: str


@dataclass(frozen=True)
class ClassificationFailure:
    error: str
    attempts: int
    last_raw: str | None
    error_kind: str  # "validation" | "transport" | "unavailable"


def classify_column(
    client: LLMClient,
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    data_type: str,
    is_nullable: bool,
    sample_values: list[dict[str, Any]],
    max_retries: int = 3,
) -> ClassificationSuccess | ClassificationFailure:
    """Drive one classification with retry-on-malformed behavior."""
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    user = build_user_prompt(
        schema_name=schema_name,
        table_name=table_name,
        column_name=column_name,
        data_type=data_type,
        is_nullable=is_nullable,
        sample_values=sample_values,
    )
    last_raw: str | None = None
    last_error: str = ""
    last_kind: str = "validation"

    for attempt in range(1, max_retries + 1):
        try:
            payload = client.complete_json(SYSTEM_PROMPT, user)
        except (LLMUnavailable, LLMTimeout) as exc:
            return ClassificationFailure(
                error=str(exc),
                attempts=attempt,
                last_raw=last_raw,
                error_kind="unavailable" if isinstance(exc, LLMUnavailable) else "transport",
            )
        except LLMResponseError as exc:
            # The LLM responded with something we cannot parse (empty body,
            # no JSON, etc.). Retry with a repair prompt that asks the
            # model to fix its output.
            last_error = str(exc)
            last_kind = "validation"
            logger.warning(
                "Classification response unparseable for {}.{} (attempt {}/{}): {}",
                table_name,
                column_name,
                attempt,
                max_retries,
                last_error,
            )
            user = build_repair_prompt(last_raw or "<empty body>", last_error)
            continue
        except LLMError as exc:
            return ClassificationFailure(
                error=str(exc),
                attempts=attempt,
                last_raw=last_raw,
                error_kind="transport",
            )

        last_raw = _payload_to_repr(payload)
        try:
            classification = coerce_dict_to_classification(payload)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
            last_kind = "validation"
            logger.warning(
                "Classification parse failed for {}.{} (attempt {}/{}): {}",
                table_name,
                column_name,
                attempt,
                max_retries,
                last_error,
            )
            user = build_repair_prompt(last_raw, last_error)
            continue

        return ClassificationSuccess(
            classification=classification,
            attempts=attempt,
            raw_response=last_raw,
        )

    return ClassificationFailure(
        error=last_error or "exhausted retries without a parseable response",
        attempts=max_retries,
        last_raw=last_raw,
        error_kind=last_kind,
    )


def _payload_to_repr(payload: Any) -> str:
    """Compact repr of a parsed payload for logging / repair prompts."""
    import json

    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(payload)


__all__ = [
    "ClassificationFailure",
    "ClassificationSuccess",
    "classify_column",
    "LLMResponseError",  # re-export for convenience
]
