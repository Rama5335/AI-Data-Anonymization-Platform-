"""Development-only password reset for a local metadata DB user.

The application has no password-reset flow (by design — passwords are
Argon2-hashed and irreversible). This script is a manual, local-only
escape hatch for operators who lost a local dev account's password.

Safety guards (intentionally strict):
  * Refuses to run when APP_ENV == "production".
  * Refuses to run unless the metadata database is SQLite, i.e. the
    local/development database. It will never touch a remote database.
  * Only updates the password_hash of an existing user. It never
    creates, deletes, or deactivates users, and it never prints hashes.

Usage (run from the backend/ directory, from a developer shell):

    .venv\\Scripts\\python.exe scripts\\reset_password.py testuser "new-password-12345"

The new password must be at least 8 characters (same rule as the API's
CreateUserRequest).
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import session_scope
from app.models.entities import User
from app.security.passwords import hash_password


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: reset_password.py <username> <new-password>", file=sys.stderr)
        return 2

    username, new_password = sys.argv[1], sys.argv[2]

    settings = get_settings()
    if settings.app_env == "production":
        print("refusing: APP_ENV=production; this script is development-only", file=sys.stderr)
        return 2

    db_url = settings.database_url.get_secret_value()
    if not db_url.startswith("sqlite"):
        print(
            f"refusing: metadata DB is not SQLite (got {db_url.split(':', 1)[0]!r}); "
            "this script only resets local/development databases",
            file=sys.stderr,
        )
        return 2

    if len(new_password) < 8:
        print("refusing: new password must be at least 8 characters", file=sys.stderr)
        return 2

    with session_scope() as session:
        user = session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if user is None:
            print(f"no such user: {username!r} (nothing changed)", file=sys.stderr)
            return 1
        user.password_hash = hash_password(new_password)
        session.commit()
        print(f"password updated for user {username!r} (local dev only)")
        print("log in via the UI or POST /auth/login; the old password no longer works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
