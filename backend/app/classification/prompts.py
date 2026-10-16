"""Prompt construction for the classification agent.

The system prompt enforces three contracts that the rest of the
codebase relies on:

1. **No SQL.** The LLM is told, twice, that it must not generate SQL.
2. **JSON only.** The output is one JSON object with a fixed shape.
3. **No apologies / no markdown.** A refusal or a fenced code block
   is treated as a malformed response and the parser retries.

The user prompt carries only what the LLM needs: schema, sample
values (already capped at 512 bytes per cell in Phase 5), and a
``truncated``/``original_length`` marker so the model knows when it
is looking at a head, not a full value.
"""

from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are a data classification assistant. You classify database columns by \
sensitivity. Output exactly one JSON object and nothing else.

CRITICAL RULES
- DO NOT generate SQL. Do not write any query, DDL, DML, or shell command.
- Output JSON only. No markdown, no explanations, no apologies, no prefixes.
- The JSON object MUST have exactly these four keys:
    sensitivity: one of "Name", "Email", "Phone", "Address", \
"National ID", "Passport", "IBAN", "Credit Card", "Salary", \
"Birth Date", "Company Name", "Non Sensitive", "Unknown"
    confidence: number between 0.0 and 1.0
    reasoning: short string (one or two sentences) explaining the call
    suggested_strategy: one of "Fake Name", "Fake Email", \
"Masked Phone", "Hash", "Tokenization", "Random Replacement", \
"Preserve Format", "Null", "Custom Rule", "LLM Anonymize"
- If you are uncertain, choose "Unknown" with a low confidence.
- Use "Non Sensitive" only for clearly non-PII data (counters, booleans, \
internal status fields, audit timestamps without user identifiers).
"""


def build_user_prompt(
    *,
    schema_name: str,
    table_name: str,
    column_name: str,
    data_type: str,
    is_nullable: bool,
    sample_values: list[dict[str, Any]],
    extra_hints: str | None = None,
) -> str:
    """Render the per-column user prompt.

    ``sample_values`` is a list of cell dicts (the same shape that
    ``/sampling/tables/{id}`` returns) so the LLM sees truncation
    metadata. A column with no sample (e.g. when sampling is disabled)
    still gets a usable prompt — just with an explicit note.
    """
    lines = [
        "Column to classify:",
        f"  table: {schema_name}.{table_name}",
        f"  column: {column_name}",
        f"  data_type: {data_type}",
        f"  is_nullable: {str(is_nullable).lower()}",
    ]
    if extra_hints:
        lines.append(f"  extra_hints: {extra_hints}")

    if not sample_values:
        lines.append("")
        lines.append("No sample values were collected for this column.")
        lines.append(
            "Classify from the column name and data type only; "
            "use 'Unknown' with low confidence if you cannot decide."
        )
    else:
        lines.append("")
        lines.append("Sample values (already capped at the byte limit):")
        for i, cell in enumerate(sample_values, start=1):
            value = cell.get("value")
            truncated = cell.get("truncated", False)
            original_length = cell.get("original_length")
            value_repr = "null" if value is None else repr(value)
            tail = ""
            if truncated:
                tail = (
                    f"  [TRUNCATED: original_length={original_length} "
                    f"chars/bytes, you are seeing the head only]"
                )
            lines.append(f"  {i}. {value_repr}{tail}")

    lines.append("")
    lines.append("Return exactly one JSON object with the four required keys.")
    return "\n".join(lines)


def build_repair_prompt(previous_response: str, error: str) -> str:
    """A short follow-up that asks the LLM to fix a malformed response.

    Used in the retry round: the parser feeds the LLM its own previous
    body and the validation error, and asks for a corrected JSON object.
    """
    return (
        "Your previous response was rejected by the validator.\n"
        f"Error: {error}\n"
        "Your previous response was:\n"
        f"----\n{previous_response}\n----\n"
        "Return exactly one valid JSON object and nothing else. "
        "Do not include markdown, code fences, or prose around the JSON."
    )


# ---------------------------------------------------------------------------
# Anonymization prompts
# ---------------------------------------------------------------------------

ANONYMIZE_SYSTEM = """\
You are a data anonymization assistant. Your task is to replace sensitive \
values with realistic, privacy-safe fake data.

RULES
- Output exactly one JSON object with a single key: "results".
- "results" is an array of anonymized values, one per input value.
- Preserve the general type and format of the original values \
(e.g. keep phone numbers phone-shaped, dates date-shaped).
- Never return the original value unchanged.
- If a value is null or empty, return "".
- The "results" array MUST have exactly the same length as the input.
- DO NOT generate SQL. Output JSON only."""


def build_anonymize_user_prompt(
    *,
    table_name: str,
    column_name: str,
    data_type: str,
    sensitivity: str,
    strategy: str,
    values: list[Any],
) -> str:
    """Build the user prompt for batch LLM-driven anonymization."""
    lines = [
        "Anonymize the following column:",
        f"  table: {table_name}",
        f"  column: {column_name}",
        f"  data_type: {data_type}",
        f"  sensitivity: {sensitivity}",
        f"  strategy hint: {strategy}",
        "",
        "Values to anonymize (in order):",
    ]
    for i, v in enumerate(values, start=1):
        lines.append(f"  {i}. {repr(v) if v is not None else 'null'}")
    lines.append("")
    lines.append('Return exactly one JSON object: {"results": [anonymized values]}')
    return "\n".join(lines)
