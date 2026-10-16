"""Authorization-boundary tests.

Covers the security posture required by the security-hardening phase:

* forged / malformed / unknown-subject JWTs are rejected (401);
* inactive users are rejected (403);
* every admin-gated endpoint refuses a ``role="user"`` token (403);
* previously-unauthenticated read endpoints require a token;
* job attribution cannot be spoofed by a non-admin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _conn_payload() -> dict:
    return {
        "host": "127.0.0.1",
        "port": 1433,
        "auth_type": "sql",
        "username": "sa",
        "password": "CHANGE_ME_SA_PASSWORD",
        "database": "anonymizer_app",
        "driver": "ODBC Driver 18 for SQL Server",
        "encrypt": True,
        "trust_server_certificate": True,
        "timeout_seconds": 5,
    }


# (method, path, json-body) tuples for every admin-gated endpoint.
ADMIN_ROUTES = [
    ("POST", "/backups", {"connection": _conn_payload()}),
    ("GET", "/backups", None),
    ("POST", "/updates/apply", None),  # body validated after authz; 403 wins
    ("POST", "/updates/rollback", None),
    ("GET", "/audit", None),
    ("GET", "/audit/jobs/some-job", None),
    ("POST", "/users", {"username": "bobby", "password": "bobby-pwd-123", "role": "user"}),
    ("POST", "/servers", {"name": "s", "host": "h", "port": 1433, "auth_type": "sql"}),
    ("PATCH", "/servers/srv-not-real", {"host": "h2"}),
    ("DELETE", "/servers/srv-not-real", None),
]


# ---------------------------------------------------------------------------
# token validation
# ---------------------------------------------------------------------------


def test_forged_token_with_wrong_secret_rejected(app_env):
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.dqDBjECPpA7ZLQy4lcviFPPYvbrUabpUo4BZcviAdU0"
    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_malformed_token_rejected(app_env):
    r = client.get("/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
    r2 = client.get("/users/me", headers={"Authorization": "Token abc"})
    assert r2.status_code == 401


def test_token_for_unknown_subject_rejected(app_env, make_token):
    token = make_token("00000000-0000-0000-0000-000000000000")
    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_inactive_user_forbidden(app_env, seed_user, auth_headers):
    uid, _ = seed_user("ghost", active=False)
    r = client.get("/users/me", headers=auth_headers(uid))
    assert r.status_code == 403


def test_expired_token_rejected(app_env, seed_user, make_token):
    uid, _ = seed_user("alice")
    token = make_token(uid, expires_in_minutes=-5)
    r = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# role enforcement on admin-gated endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_non_admin_forbidden_on_admin_routes(app_env, seed_user, auth_headers, method, path, body):
    uid, _ = seed_user("alice", role="user")
    r = client.request(method, path, headers=auth_headers(uid), json=body)
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_unauthenticated_forbidden_on_admin_routes(app_env, method, path, body):
    r = client.request(method, path, json=body)
    assert r.status_code == 401, r.text


def test_admin_passes_role_check_on_audit(app_env, seed_user, auth_headers):
    """Admin tokens are accepted (route executes; no 401/403)."""
    uid, _ = seed_user("root", role="admin")
    r = client.get("/audit", headers=auth_headers(uid))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# previously-unauthenticated read endpoints now require a token
# ---------------------------------------------------------------------------


def test_approvals_pending_requires_auth(app_env):
    assert client.get("/approvals/jobs/any/pending").status_code == 401


def test_approvals_list_requires_auth(app_env):
    assert client.get("/approvals/jobs/any").status_code == 401


def test_updates_plan_requires_auth(app_env):
    r = client.post("/updates/plan", json={"job_id": "j", "connection": _conn_payload()})
    assert r.status_code == 401


def test_server_probe_requires_auth(app_env):
    r = client.post("/servers/any/probe", json={"connection": _conn_payload()})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# job attribution cannot be spoofed
# ---------------------------------------------------------------------------


def test_non_admin_cannot_attribute_job_to_other_user(app_env, seed_user, auth_headers):
    victim_id, _ = seed_user("victim", role="user")
    actor_id, _ = seed_user("actor", role="user")
    r = client.post(
        "/schema/discover",
        headers=auth_headers(actor_id),
        json={"connection": _conn_payload(), "requested_by_user_id": victim_id},
    )
    assert r.status_code == 403


def test_admin_may_attribute_job_to_other_user(app_env, seed_user, auth_headers):
    """Admin attribution is allowed; the request proceeds past the
    attribution check (the MSSQL call itself then fails with 503 because
    no server is listening, which is fine and expected in unit CI)."""
    victim_id, _ = seed_user("victim", role="user")
    admin_id, _ = seed_user("root", role="admin")
    r = client.post(
        "/schema/discover",
        headers=auth_headers(admin_id),
        json={"connection": _conn_payload(), "requested_by_user_id": victim_id},
    )
    assert r.status_code in (200, 503), r.text


def test_user_may_attribute_job_to_self(app_env, seed_user, auth_headers):
    actor_id, _ = seed_user("actor", role="user")
    r = client.post(
        "/schema/discover",
        headers=auth_headers(actor_id),
        json={"connection": _conn_payload(), "requested_by_user_id": actor_id},
    )
    assert r.status_code in (200, 503), r.text
