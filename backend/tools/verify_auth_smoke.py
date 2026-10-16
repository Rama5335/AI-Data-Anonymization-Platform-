"""Auth / RBAC smoke verification for the ``Authentication`` deliverable.

Runs against an **already-running** API at ``127.0.0.1:8765`` and needs
no target MSSQL, no Ollama, and no network beyond localhost. It proves
the authentication + authorization contract end-to-end over HTTP:

  1. ``GET /health`` is public.
  2. ``GET /openapi.json`` (Swagger) is served.
  3. Protected endpoints reject a missing token with 401.
  4. A wrong password is rejected with 401.
  5. The bootstrap admin can log in and receives an ``admin`` JWT.
  6. ``GET /users/me`` returns the authenticated user.
  7. An admin can read the audit log and create a user.
  8. A duplicate username is rejected with 409.
  9. A non-admin user can log in.
 10. A non-admin is forbidden (403) from admin-only routes.
 11. A non-admin still reaches authenticated-only routes.
 12. A garbage token is rejected with 401.

Usage (from ``backend/``), with the API up and
``BOOTSTRAP_ADMIN_USERNAME=root`` / ``BOOTSTRAP_ADMIN_PASSWORD=<admin-password>``:

    python tools/verify_auth_smoke.py
"""

from __future__ import annotations

import os
import sys

import requests

API = os.environ.get("SMOKE_API", "http://127.0.0.1:8765")
ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "root")
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "CHANGE_ME")
NONADMIN_USERNAME = "smoke_user"
NONADMIN_PASSWORD = "smoke-user-pwd-12345"

checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = "") -> None:
    checks.append((name, ok, msg))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(': ' + msg) if msg else ''}")


def main() -> int:  # noqa: C901 - a linear checklist reads better here
    try:
        requests.get(f"{API}/health", timeout=5)
    except requests.RequestException as exc:
        print(f"API is not reachable at {API}: {exc}")
        return 2

    # 1. public health
    r = requests.get(f"{API}/health", timeout=5)
    record(
        "1. GET /health public",
        r.status_code == 200 and r.json().get("status") == "ok",
        f"status={r.status_code}",
    )

    # 2. Swagger / OpenAPI
    r = requests.get(f"{API}/openapi.json", timeout=5)
    paths = r.json().get("paths", {}) if r.status_code == 200 else {}
    record(
        "2. Swagger/OpenAPI served",
        r.status_code == 200 and "/auth/login" in paths,
        f"{len(paths)} paths",
    )

    # 3. protected endpoint without token -> 401
    r = requests.get(f"{API}/users/me", timeout=5)
    record("3. /users/me without token -> 401", r.status_code == 401, f"status={r.status_code}")

    # 4. wrong password -> 401
    r = requests.post(
        f"{API}/auth/login", json={"username": ADMIN_USERNAME, "password": "nope"}, timeout=10
    )
    record("4. login wrong password -> 401", r.status_code == 401, f"status={r.status_code}")

    # 5. bootstrap admin login
    r = requests.post(
        f"{API}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200 or r.json()["user"]["role"] != "admin":
        record(
            "5. bootstrap admin login -> admin JWT",
            False,
            f"status={r.status_code}; body={r.text[:200]}",
        )
        return 1
    admin_token = r.json()["access_token"]
    record("5. bootstrap admin login -> admin JWT", True, "role=admin")
    admin_h = {"Authorization": f"Bearer {admin_token}"}

    # 6. /users/me with token
    r = requests.get(f"{API}/users/me", headers=admin_h, timeout=5)
    record(
        "6. /users/me with token -> 200",
        r.status_code == 200 and r.json().get("username") == ADMIN_USERNAME,
        f"status={r.status_code}",
    )

    # 7. admin-only routes
    r = requests.get(f"{API}/audit?limit=5", headers=admin_h, timeout=10)
    record("7a. admin GET /audit -> 200", r.status_code == 200, f"status={r.status_code}")

    r = requests.post(
        f"{API}/users",
        headers=admin_h,
        json={"username": NONADMIN_USERNAME, "password": NONADMIN_PASSWORD, "role": "user"},
        timeout=10,
    )
    created = r.status_code == 201
    record(
        "7b. admin POST /users -> 201 or 409 (idempotent)",
        r.status_code in (201, 409),
        f"status={r.status_code}",
    )

    # 8. duplicate -> 409
    r = requests.post(
        f"{API}/users",
        headers=admin_h,
        json={"username": NONADMIN_USERNAME, "password": NONADMIN_PASSWORD, "role": "user"},
        timeout=10,
    )
    record("8. duplicate username -> 409", r.status_code == 409, f"status={r.status_code}")

    # 9. non-admin login
    r = requests.post(
        f"{API}/auth/login",
        json={"username": NONADMIN_USERNAME, "password": NONADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200 or r.json()["user"]["role"] != "user":
        record("9. non-admin login -> 200", False, f"status={r.status_code}; body={r.text[:200]}")
        return 1
    user_token = r.json()["access_token"]
    user_h = {"Authorization": f"Bearer {user_token}"}
    record("9. non-admin login -> 200", True, "role=user")

    # 10. non-admin forbidden from admin routes
    for path, method in (("/audit", "get"), ("/backups", "get")):
        rr = getattr(requests, method)(f"{API}{path}", headers=user_h, timeout=10)
        record(
            f"10. non-admin {method.upper()} {path} -> 403",
            rr.status_code == 403,
            f"status={rr.status_code}",
        )

    # 11. non-admin reaches authenticated-only route
    r = requests.get(f"{API}/settings", headers=user_h, timeout=10)
    record("11. non-admin GET /settings -> 200", r.status_code == 200, f"status={r.status_code}")

    # 12. garbage token -> 401
    r = requests.get(
        f"{API}/users/me", headers={"Authorization": "Bearer not-a-real-token"}, timeout=5
    )
    record("12. garbage token -> 401", r.status_code == 401, f"status={r.status_code}")

    print()
    print("=" * 72)
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"SUMMARY: {passed}/{len(checks)} checks passed")
    print("=" * 72)
    for name, ok, msg in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {msg}")
    if not created:
        # 409 means the script already ran; not a failure, but note it.
        print("  (note: smoke user already existed from a previous run)")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
