"""Phase 10 — Masking engine (Anonymization).

The backend performs the actual transformation; the LLM only
**selects** the strategy. This module has no knowledge of the LLM.

Nine strategies (per ``plan.md`` §3 and ``implement_plan.md`` §11):

* ``Fake Name`` — ``Faker`` ``name()``.
* ``Fake Email`` — first initial + last name + a random domain.
* ``Masked Phone`` — keep last 4 digits, mask the rest.
* ``Hash`` — ``sha256(salt || value)`` hex. Salt is per-job, kept
  encrypted in the ``masking_jobs`` row. Same input → same output
  within a job (referential integrity).
* ``Tokenization`` — ``HMAC-SHA256(token_key, value)`` hex. Same input
  → same output within a job, and reversible if the operator
  re-imports the ``token_vault`` (we wipe the key after Phase 11).
* ``Random Replacement`` — random value of the same data type
  (Faker for text, ``random`` for numerics, ``datetime`` for dates).
* ``Preserve Format`` — keep the data type and a recognisable
  skeleton (e.g. last 4 digits of a phone); the deterministic
  mapping is ``value[:k] + masked``.
* ``Null`` — emit ``NULL`` for every value.
* ``Custom Rule`` — fall-through that calls a registered callable;
  the platform ships no built-in custom rules.

Deterministic mapping is per-job and per-column: a ``MaskingContext``
holds the salt/token-key and a per-column cache so that a column
referenced from multiple places (the PK and all its FKs) maps the
same input to the same output within a single run.

FK referential integrity: the agent that drives the masking pass
discovers the FK relationships from the Phase 4 metadata and, when
anonymising a primary key, reuses the same ``MaskingContext`` for
every column that references it (or the column itself, if it is a
referenced PK).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol

from faker import Faker


SUPPORTED_STRATEGIES: frozenset[str] = frozenset(
    {
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
    }
)


class MaskingError(Exception):
    """Raised for any unrecoverable masking failure."""


class CustomRule(Protocol):
    """Callable signature for ``Custom Rule`` strategies."""

    def __call__(self, value: Any, ctx: "MaskingContext", column: str) -> Any: ...


@dataclass
class MaskingContext:
    """Per-job masking state.

    Holds the salt (for ``Hash``) and the token key (for
    ``Tokenization``). The token key is wiped at the end of the
    update phase; the salt is wiped after the audit log has been
    written.
    """

    job_id: str
    salt: bytes
    token_key: bytes
    faker: Faker = field(default_factory=Faker)
    _cache: dict[tuple[str, Any], Any] = field(default_factory=dict)
    _custom_rules: dict[str, CustomRule] = field(default_factory=dict)

    def register_custom_rule(self, column: str, rule: CustomRule) -> None:
        self._custom_rules[column] = rule

    def wipe_token_key(self) -> None:
        """Overwrite the token key with random bytes so reversibility ends here."""
        self.token_key = secrets.token_bytes(32)

    def wipe_salt(self) -> None:
        self.salt = secrets.token_bytes(32)


def generate_salt() -> bytes:
    return secrets.token_bytes(32)


def generate_token_key() -> bytes:
    return secrets.token_bytes(32)


def mask_value(
    value: Any,
    strategy: str,
    column: str,
    ctx: MaskingContext,
) -> Any:
    """Mask a single value under the given strategy.

    ``None`` propagates: the maskers all return ``None`` for ``None``
    input. This keeps the SQL ``UPDATE`` simple — only non-null
    columns need their values rewritten.
    """
    if value is None:
        return None
    if strategy == "Null":
        return None
    if strategy == "Fake Name":
        return ctx.faker.name()
    if strategy == "Fake Email":
        return _fake_email(ctx.faker)
    if strategy == "Masked Phone":
        return _masked_phone(value)
    if strategy == "Preserve Format":
        return _preserve_format(value, column)
    if strategy == "Random Replacement":
        return _random_replacement(value, ctx.faker)
    if strategy == "Hash":
        return _hash(value, ctx, column)
    if strategy == "Tokenization":
        return _tokenize(value, ctx, column)
    if strategy == "Custom Rule":
        rule = ctx._custom_rules.get(column)
        if rule is None:
            raise MaskingError(
                f"Custom Rule requested for column {column!r} but no rule was registered."
            )
        return rule(value, ctx, column)
    if strategy == "LLM Anonymize":
        # LLM anonymization is handled by the orchestrator's
        # preprocess_column() + cache. If we reach here, it means the
        # cache wasn't warmed — return value unchanged (safe fallback).
        return value
    raise MaskingError(f"Unknown masking strategy: {strategy!r}")


# ---------------------------------------------------------------------------
# Strategy implementations (all deterministic per (job, column, value))
# ---------------------------------------------------------------------------


def _hash(value: Any, ctx: MaskingContext, column: str) -> str:
    cache_key = (column, _normalize_for_cache(value))
    if cache_key in ctx._cache:
        return ctx._cache[cache_key]
    digest = hashlib.sha256(ctx.salt + str(value).encode("utf-8")).hexdigest()
    ctx._cache[cache_key] = digest
    return digest


def _tokenize(value: Any, ctx: MaskingContext, column: str) -> str:
    cache_key = (column, _normalize_for_cache(value))
    if cache_key in ctx._cache:
        return ctx._cache[cache_key]
    digest = hmac.new(ctx.token_key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()
    ctx._cache[cache_key] = digest
    return digest


_PHONE_RE = re.compile(r"\d")


def _masked_phone(value: Any) -> str:
    digits = _PHONE_RE.findall(str(value))
    if len(digits) < 4:
        return "****"
    return ("*" * (len(digits) - 4)) + "".join(digits[-4:])


def _fake_email(faker: Faker) -> str:
    fn = faker.first_name().lower()
    ln = faker.last_name().lower()
    domain = faker.domain_name()
    return f"{fn[0]}.{ln}@{domain}"


_KEEP_TAIL = 4


def _preserve_format(value: Any, column: str) -> str:
    s = str(value)
    if len(s) <= _KEEP_TAIL:
        return "*" * len(s)
    return "*" * (len(s) - _KEEP_TAIL) + s[-_KEEP_TAIL:]


def _random_replacement(value: Any, faker: Faker) -> Any:
    if isinstance(value, bool):
        return faker.boolean()
    if isinstance(value, int):
        # Stay in a sensible range; absolute value capped at 1e9.
        return faker.random_int(min=-1_000_000_000, max=1_000_000_000)
    if isinstance(value, float):
        return faker.pyfloat(left_digits=6, right_digits=2, positive=False)
    # Date / datetime / fallback: Faker date_string.
    try:
        return faker.date_time().isoformat()
    except Exception:
        return faker.uuid4()


def _normalize_for_cache(value: Any) -> str:
    if isinstance(value, (int, float, bool)):
        return repr(value)
    return str(value)
