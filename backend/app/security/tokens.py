"""JWT issue and verification helpers."""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt


def create_access_token(
    subject: str, secret: str, expires_in_minutes: int, algorithm: str = "HS256"
) -> str:
    """Issue a signed, expiring access token for one application user."""
    if not subject:
        raise ValueError("Token subject must not be empty.")
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=expires_in_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def verify_access_token(token: str, secret: str, algorithm: str = "HS256") -> str:
    """Verify a token and return its subject."""
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise jwt.InvalidTokenError("Token subject is missing.")
    return subject
