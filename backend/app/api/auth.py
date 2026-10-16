"""Auth API (Item 1).

* ``POST /auth/login`` — verify password, issue JWT.
* ``POST /users`` — create a new user (admin-only by default; bootstrapping
  uses the first-ever-user path via ``BOOTSTRAP_ADMIN_USERNAME``).
* ``GET  /users/me`` — return the current user.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.database.session import session_scope
from app.models.entities import User
from app.security.deps import get_current_user, require_role
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token

router = APIRouter(tags=["auth"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user", pattern="^(user|admin)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


def _user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _ensure_bootstrap_admin() -> None:
    """Ensure the operator-configured bootstrap admin is usable.

    When ``BOOTSTRAP_ADMIN_USERNAME`` and ``BOOTSTRAP_ADMIN_PASSWORD``
    are both set:

    * if the user does not exist yet, it is created as an active
      ``admin``;
    * if the user already exists but the configured password does not
      match the stored hash, the hash is updated.

    The second branch lets an operator recover from a mistyped or
    shell-mangled first-run bootstrap password without deleting the
    account. Existing users are never deleted or deactivated, and their
    role is never changed. Unset both variables after the first
    successful login; while they remain set they are the source of
    truth for this account's password.
    """
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not username or not password:
        return
    session = session_scope()
    try:
        existing = session.execute(
            select(User).where(User.username == username)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    is_active=True,
                    role="admin",
                )
            )
            session.commit()
        elif not verify_password(password, existing.password_hash):
            existing.password_hash = hash_password(password)
            session.commit()
    finally:
        session.close()


def _audit_login(
    session,
    *,
    outcome: str,
    user_id: str | None,
    username: str,
    reason: str | None = None,
) -> None:
    """Record an authentication outcome; never raises over the login flow."""
    try:
        from app.agents.audit import AuditAgent

        AuditAgent(session).write(
            event_type="auth.login",
            outcome=outcome,
            user_id=user_id,
            details={"username": username, "reason": reason},
        )
    except Exception:  # noqa: BLE001 - audit trouble must not mask login behavior
        pass


@router.post("/auth/login", response_model=TokenOut)
def login(req: LoginRequest) -> TokenOut:
    _ensure_bootstrap_admin()
    session = session_scope()
    try:
        user = session.execute(
            select(User).where(User.username == req.username)
        ).scalar_one_or_none()
        if (
            user is None
            or not user.is_active
            or not verify_password(req.password, user.password_hash)
        ):
            # Audit the failure (no password or hash ever recorded).
            _audit_login(
                session,
                outcome="failure",
                user_id=None,
                username=req.username,
                reason="invalid_credentials",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
            )
        settings = get_settings()
        token = create_access_token(
            subject=user.id,
            secret=settings.jwt_secret_key.get_secret_value(),
            expires_in_minutes=settings.jwt_access_token_minutes,
            algorithm=settings.jwt_algorithm,
        )
        _audit_login(
            session,
            outcome="success",
            user_id=user.id,
            username=user.username,
        )
        return TokenOut(
            access_token=token,
            token_type="bearer",
            expires_in=settings.jwt_access_token_minutes * 60,
            user=_user_to_out(user),
        )
    finally:
        session.close()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    req: CreateUserRequest,
    _admin: Annotated[User, Depends(require_role("admin"))],
) -> UserOut:
    session = session_scope()
    try:
        user = User(
            username=req.username,
            password_hash=hash_password(req.password),
            is_active=True,
            role=req.role,
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username {req.username!r} already exists.",
            ) from exc
        session.refresh(user)
        try:
            from app.agents.audit import AuditAgent

            AuditAgent(session).write(
                event_type="auth.user_create",
                outcome="success",
                user_id=_admin.id,
                details={"username": user.username, "role": user.role},
            )
        except Exception:  # noqa: BLE001 - never mask user creation
            pass
        return _user_to_out(user)
    finally:
        session.close()


@router.get("/users/me", response_model=UserOut)
def get_me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return _user_to_out(user)
