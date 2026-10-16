"""FastAPI security dependencies.

* ``CurrentUser`` (typed dict) — the authenticated user record.
* ``get_current_user`` — reads the ``Authorization: Bearer <token>``
  header, verifies the JWT, and returns the matching ``User`` row.
  Raises ``HTTPException(401)`` for missing / invalid tokens.
* ``require_role`` — factory for a dependency that enforces a role.
"""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings
from app.database.session import session_scope
from app.models.entities import User
from app.security.tokens import verify_access_token


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must be 'Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1]


def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> User:
    """Validate the JWT and return the User row it identifies."""
    token = _extract_bearer_token(authorization)
    settings = get_settings()
    try:
        subject = verify_access_token(
            token, settings.jwt_secret_key.get_secret_value(), settings.jwt_algorithm
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    session = session_scope()
    try:
        user = session.get(User, subject)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token subject does not match a known user.",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is inactive.",
            )
        return user
    finally:
        session.close()


def require_role(role: str):
    """Dependency factory: require a specific role."""

    def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role != role and user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role '{role}'.",
            )
        return user

    return _check
