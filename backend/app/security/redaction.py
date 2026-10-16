"""Redaction helpers for audit details and API error responses.

The platform must never persist or return secrets (passwords, tokens,
full connection strings) or raw sensitive sample values. These helpers
are deliberately conservative: they redact *secret-shaped* values and
truncate long strings, but keep the operational text that operators
need (table/column names, error categories) intact.

They are used by:

* ``app.agents.audit`` — every audit ``details`` payload is sanitized
  before it is committed.
* ``app.api.*`` — driver/exception text returned to clients is passed
  through :func:`redact_text` so a connection string or password can
  never appear in an HTTP response.
"""

from __future__ import annotations

import re
from typing import Any

# Keys whose value must never be stored/returned. Matching is
# case-insensitive and substring-based so ``db_password``,
# ``access_token``, ``apiKey`` etc. are all caught.
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api[_-]?key|apiKey|authorization|"
    r"credential|connection[_-]?string|private[_-]?key)",
    re.IGNORECASE,
)

# Values that look like embedded secrets inside free text.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ODBC / ADO fragments: PWD=...; or Password=...
    re.compile(r"(?i)\bPWD\s*=\s*[^;]+"),
    re.compile(r"(?i)\bPass(?:word)?\s*=\s*[^;]+"),
    re.compile(r"(?i)\bUID\s*=\s*[^;]+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)Basic\s+[A-Za-z0-9+/=]+"),
    # OpenAI-style keys
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    # JWTs (three base64url segments)
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
)

REDACTED = "[REDACTED]"
_MAX_STR_LEN = 500


def redact_text(text: str, *, max_len: int = _MAX_STR_LEN) -> str:
    """Return ``text`` with secret-shaped substrings replaced.

    The result is truncated to ``max_len`` characters to bound the
    amount of third-party (driver) text we store or return.
    """
    if not isinstance(text, str):
        text = str(text)
    out = text
    for pattern in _SECRET_VALUE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out


def sanitize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Sanitize an audit ``details`` mapping in place-safe form.

    * Keys that name a secret are replaced with ``[REDACTED]``.
    * String values are passed through :func:`redact_text`.
    * Other JSON-scalar values are kept (they carry counts, flags,
      table/column names and statuses needed for the audit trail).
    * Nested dicts/lists are sanitized recursively.
    """
    if not details:
        return {}
    out: dict[str, Any] = {}
    for key, value in details.items():
        key_str = str(key)
        if _SECRET_KEY_RE.search(key_str):
            out[key_str] = REDACTED
            continue
        if isinstance(value, str):
            out[key_str] = redact_text(value)
        elif isinstance(value, dict):
            out[key_str] = sanitize_details(value)
        elif isinstance(value, (list, tuple)):
            out[key_str] = [
                (
                    sanitize_details(v)
                    if isinstance(v, dict)
                    else (redact_text(v) if isinstance(v, str) else v)
                )
                for v in value
            ]
        else:
            out[key_str] = value
    return out


def safe_error_message(exc: BaseException, *, category: str = "operation_failed") -> str:
    """Return a redacted, bounded message for an exception.

    The message keeps the original (developer-authored or driver)
    text but strips secret-shaped fragments so it is safe to return to
    an API client and to store in the audit trail. Callers that need a
    fixed, non-revealing message should pass ``category`` and ignore
    the text themselves.
    """
    detail = redact_text(str(exc))
    return f"{category}: {detail}" if category else detail
