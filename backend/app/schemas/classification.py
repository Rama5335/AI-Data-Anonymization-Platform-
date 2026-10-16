"""Pydantic schemas for the classification subsystem.

These are the *wire* contracts for the LLM output and for the
``/classification/*`` API endpoints. The 14 sensitivity classes and
10 anonymization strategies are fixed by ``plan.md`` §3; both are
enforced via ``Literal`` so any drift surfaces immediately.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SENSITIVITY_CLASSES = (
    "Name",
    "Email",
    "Phone",
    "Address",
    "National ID",
    "Passport",
    "IBAN",
    "Credit Card",
    "Salary",
    "Birth Date",
    "Company Name",
    "Non Sensitive",
    "Unknown",
)
SENSITIVITY_LITERAL = Literal[
    "Name",
    "Email",
    "Phone",
    "Address",
    "National ID",
    "Passport",
    "IBAN",
    "Credit Card",
    "Salary",
    "Birth Date",
    "Company Name",
    "Non Sensitive",
    "Unknown",
]

STRATEGY_CLASSES = (
    "Fake Name",
    "Fake Email",
    "Masked Phone",
    "Hash",
    "Tokenization",
    "Random Replacement",
    "Preserve Format",
    "Null",
    "Custom Rule",
    "LLM Anonymize",
)
STRATEGY_LITERAL = Literal[
    "Fake Name",
    "Fake Email",
    "Masked Phone",
    "Hash",
    "Tokenization",
    "Random Replacement",
    "Preserve Format",
    "Null",
    "Custom Rule",
    "LLM Anonymize",
]


class LLMClassification(BaseModel):
    """The strict JSON contract every LLM response must satisfy.

    Returned by the parser after a successful retry round. The agent
    layer persists this to the ``classification_results`` table.
    """

    sensitivity: SENSITIVITY_LITERAL
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1, max_length=2000)
    suggested_strategy: STRATEGY_LITERAL


class ClassifyColumnRequest(BaseModel):
    """Trigger a classification for a single discovered column."""

    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Maximum number of attempts the parser will make to coerce a "
            "malformed response into a valid LLMClassification."
        ),
    )


class ClassifyJobRequest(BaseModel):
    """Trigger classification for every column in a job's discovered tables."""

    table_id: str | None = Field(
        default=None,
        description=(
            "Optional table scope. If omitted, every discovered table in " "the job is classified."
        ),
    )
    max_retries: int = Field(default=3, ge=1, le=10)
    include_non_sampleable: bool = Field(
        default=False,
        description=(
            "Classify columns even when no sample row was collected for "
            "their table. Off by default because the LLM works best with "
            "concrete values to look at."
        ),
    )


class ClassificationResultOut(BaseModel):
    """Persisted classification result returned by the API."""

    id: str
    job_id: str
    schema_name: str
    table_name: str
    column_name: str
    sensitivity: str
    confidence: float
    reasoning: str
    suggested_strategy: str
    attempts: int
    ok: bool
    error: str | None
    created_at: datetime
    updated_at: datetime


class ClassificationRunResponse(BaseModel):
    """Outcome of a single classification run (one column or one job)."""

    job_id: str
    results: list[ClassificationResultOut]
    classified_count: int
    failed_count: int


class JobClassificationResponse(BaseModel):
    """Full set of persisted classifications for a job."""

    job_id: str
    results: list[ClassificationResultOut]
    status: str = "completed"
    pending_columns: int = 0


def coerce_dict_to_classification(data: dict[str, Any]) -> LLMClassification:
    """Best-effort coerce an arbitrary dict into an LLMClassification.

    Accepts case-insensitive keys and small numeric/boolean variants
    for confidence. Raises ``ValueError`` if the input cannot be
    coerced — the caller should treat that as a retryable parse error.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    norm = {str(k).lower().strip(): v for k, v in data.items()}

    sensitivity = norm.get("sensitivity") or norm.get("class") or norm.get("label")
    if not isinstance(sensitivity, str):
        raise ValueError("missing 'sensitivity' string")

    confidence = norm.get("confidence", norm.get("score"))
    if isinstance(confidence, bool):
        confidence = 1.0 if confidence else 0.0
    if not isinstance(confidence, (int, float)):
        raise ValueError("missing 'confidence' number")
    confidence = float(confidence)

    reasoning = norm.get("reasoning") or norm.get("rationale") or norm.get("reason")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("missing 'reasoning' string")

    strategy = (
        norm.get("suggested_strategy") or norm.get("strategy") or norm.get("suggestedStrategy")
    )
    if not isinstance(strategy, str):
        raise ValueError("missing 'suggested_strategy' string")

    return LLMClassification(
        sensitivity=sensitivity,
        confidence=confidence,
        reasoning=reasoning.strip(),
        suggested_strategy=strategy,
    )
