"""Item 1 verification: real users, JWT, real-user-id in audit log.

Runs against the already-running API at 127.0.0.1:8765 (pid was 19728
when this script was written; do not restart the API for this).

DoD checks performed in order:

  1. POST /auth/login with BOOTSTRAP_ADMIN_USERNAME/PASSWORD -> JWT (user A)
  2. GET /users/me with user A's JWT -> returns a real user record with a UUID
  3. POST /approvals with user A's JWT -> approved_by_user_id == A's UUID
  4. GET /audit/jobs/{id} -> user_id field is populated with A's UUID
  5. Create a SECOND user (via POST /users) and log in as that user (user B)
  6. Repeat steps 3-4 as user B on a different approval row (same job)
  7. A's audit entries show A's UUID; B's audit entries show B's UUID
     (no cross-contamination)
  8. Clear PASS/FAIL per check (printed inline above)

The script does NOT restart the API. It assumes:
  * the API was started with BOOTSTRAP_ADMIN_USERNAME=<admin-username>
    and BOOTSTRAP_ADMIN_PASSWORD=<admin-password>
  * a fresh /schema/discover will create the only job in the test
"""

from __future__ import annotations

import os
import sys
import time

import requests


API = "http://127.0.0.1:8765"
ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "CHANGE_ME")
USER_B_USERNAME = os.environ.get("VERIFY_USER_B_USERNAME", "verify_userb")
USER_B_PASSWORD = os.environ.get("VERIFY_USER_B_PASSWORD", "CHANGE_ME")

CONN = {
    "host": "127.0.0.1",
    "port": 1433,
    "auth_type": "sql",
    "username": "sa",
    "password": os.environ.get("MSSQL_SA_PASSWORD", "CHANGE_ME"),
    "database": "anonymizer_app",
    "driver": "ODBC Driver 18 for SQL Server",
    "encrypt": True,
    "trust_server_certificate": True,
    "timeout_seconds": 10,
}


def fail(msg: str) -> bool:
    return False


checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, msg: str = "") -> None:
    checks.append((name, ok, msg))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}{(': ' + msg) if msg else ''}")


def expect_status(r, want: int, label: str) -> tuple[bool, str]:
    if r.status_code != want:
        return False, f"status={r.status_code} (want {want}); body={r.text[:300]}"
    return True, f"status={r.status_code}"


def main() -> int:
    # Sanity: API up.
    r = requests.get(f"{API}/health", timeout=5)
    if r.status_code != 200:
        print("API is down at", API, "->", r.status_code, r.text[:200])
        return 2

    # ---- CHECK 1: login as A ----
    print()
    print("CHECK 1: POST /auth/login (user A = bootstrap admin 'root')")
    r = requests.post(
        f"{API}/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        record("1. login A returns JWT", False, f"status={r.status_code}; body={r.text[:300]}")
        return 1
    body = r.json()
    A_token = body["access_token"]
    A_id = body["user"]["id"]
    A_role = body["user"]["role"]
    if not A_token or not A_id or A_role != "admin":
        record("1. login A returns JWT", False, f"missing fields in response: {body}")
        return 1
    record("1. login A returns JWT", True, f"user_id={A_id}, role={A_role}")

    # ---- CHECK 2: /users/me with A's JWT ----
    print()
    print("CHECK 2: GET /users/me with A's JWT")
    r = requests.get(
        f"{API}/users/me",
        headers={"Authorization": f"Bearer {A_token}"},
        timeout=5,
    )
    ok, msg = expect_status(r, 200, "/users/me A")
    if not ok:
        record("2. /users/me returns real A", False, msg)
        return 1
    me = r.json()
    if me["id"] != A_id or me["username"] != ADMIN_USERNAME or me["role"] != "admin":
        record(
            "2. /users/me returns real A",
            False,
            f"got {me}, expected id={A_id} username={ADMIN_USERNAME} role=admin",
        )
    else:
        record(
            "2. /users/me returns real A",
            True,
            f"id={me['id']}, username={me['username']}, role={me['role']}",
        )

    # ---- Setup: discover + sample + classify (one job, two approvals) ----
    print()
    print("Setup: discover + sample + classify (one job shared by A and B)")
    r = requests.post(f"{API}/schema/discover", json={"connection": CONN}, timeout=30)
    if r.status_code != 200:
        record("setup.discover", False, f"status={r.status_code}; body={r.text[:200]}")
        return 1
    job_id = r.json()["job_id"]
    print(f"  job_id={job_id}")

    job_body = requests.get(f"{API}/schema/job/{job_id}", timeout=10).json()
    by_name = {t["table_name"]: t for t in job_body["tables"]}
    for tname in ("Customers", "CustomerNotes"):
        tid = by_name[tname]["id"]
        sr = requests.post(
            f"{API}/sampling/tables/{tid}",
            json={"connection": CONN, "row_count": 5, "max_bytes_per_value": 512},
            timeout=30,
        )
        if sr.status_code != 200:
            record(f"setup.sample.{tname}", False, f"status={sr.status_code}; body={sr.text[:200]}")
            return 1
    print("  sampled Customers + CustomerNotes")

    print("  classifying (real Ollama, may take ~70s)...")
    t0 = time.time()
    cr = requests.post(
        f"{API}/classification/jobs/{job_id}",
        json={"max_retries": 1},
        timeout=600,
    )
    classify_elapsed = time.time() - t0
    if cr.status_code != 200:
        record("setup.classify", False, f"status={cr.status_code}; body={cr.text[:200]}")
        return 1
    classification_results = cr.json()["results"]
    print(f"  classified {len(classification_results)} columns in {classify_elapsed:.1f}s")
    if len(classification_results) < 2:
        record(
            "setup.classify",
            False,
            f"need >= 2 classification rows; got {len(classification_results)}",
        )
        return 1
    A_target = classification_results[0]
    B_target = classification_results[1]

    # ---- CHECK 3: POST /approvals as A ----
    print()
    print("CHECK 3: POST /approvals as A on the first column")
    r = requests.post(
        f"{API}/approvals",
        headers={"Authorization": f"Bearer {A_token}"},
        json={"classification_id": A_target["id"], "decision": "approve"},
        timeout=10,
    )
    ok, msg = expect_status(r, 200, "approval A")
    if not ok:
        record("3. A's approval has A's user_id", False, msg)
        return 1
    a_resp = r.json()
    if a_resp.get("approved_by_user_id") != A_id:
        record(
            "3. A's approval has A's user_id",
            False,
            f"approved_by_user_id={a_resp.get('approved_by_user_id')!r}, want {A_id!r}",
        )
    else:
        record(
            "3. A's approval has A's user_id",
            True,
            f"approved_by_user_id={a_resp['approved_by_user_id']}",
        )

    # ---- CHECK 4: /audit/jobs shows A's UUID ----
    print()
    print("CHECK 4: /audit/jobs -> A's approval event has user_id=A")
    r = requests.get(f"{API}/audit/jobs/{job_id}", timeout=10)
    if r.status_code != 200:
        record("4. audit has A's user_id", False, f"status={r.status_code}")
        return 1
    items = r.json()["items"]
    a_approval_events = [
        e
        for e in items
        if e.get("event_type", "").startswith("approval.") and e.get("user_id") == A_id
    ]
    if not a_approval_events:
        # Show what we have for debugging.
        seen = [(e.get("event_type"), e.get("user_id")) for e in items]
        record(
            "4. audit has A's user_id",
            False,
            f"no approval event for A in {len(items)} events; seen={seen}",
        )
    else:
        record(
            "4. audit has A's user_id",
            True,
            f"{len(a_approval_events)} approval event(s) with user_id={A_id}",
        )

    # ---- CHECK 5: create B + log in as B ----
    print()
    print("CHECK 5: create user B (non-admin) and log in")
    r = requests.post(
        f"{API}/users",
        headers={"Authorization": f"Bearer {A_token}"},
        json={"username": USER_B_USERNAME, "password": USER_B_PASSWORD, "role": "user"},
        timeout=10,
    )
    if r.status_code == 201:
        B_id = r.json()["id"]
        record("5a. create B via /users", True, f"B_id={B_id}")
    elif r.status_code == 409:
        # Re-run of the script: B already exists. Log in to recover id.
        lr = requests.post(
            f"{API}/auth/login",
            json={"username": USER_B_USERNAME, "password": USER_B_PASSWORD},
            timeout=10,
        )
        if lr.status_code != 200:
            record("5a. create B via /users", False, f"409 and re-login failed: {lr.text[:200]}")
            return 1
        B_id = lr.json()["user"]["id"]
        record("5a. create B via /users", True, f"B already existed; recovered B_id={B_id}")
    else:
        record("5a. create B via /users", False, f"status={r.status_code}; body={r.text[:300]}")
        return 1

    r = requests.post(
        f"{API}/auth/login",
        json={"username": USER_B_USERNAME, "password": USER_B_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        record("5b. login B returns JWT", False, f"status={r.status_code}; body={r.text[:200]}")
        return 1
    body = r.json()
    B_token = body["access_token"]
    if body["user"]["id"] != B_id:
        record("5b. login B returns JWT", False, f"id mismatch: {body['user']['id']} vs {B_id}")
        return 1
    if B_id == A_id:
        record("5b. login B returns JWT", False, f"B_id==A_id=={A_id} (collision)")
        return 1
    record("5b. login B returns JWT", True, f"B_id={B_id} (distinct from A_id={A_id})")

    # ---- CHECK 6: B submits an approval on the second column ----
    print()
    print("CHECK 6: POST /approvals as B on the second column")
    r = requests.post(
        f"{API}/approvals",
        headers={"Authorization": f"Bearer {B_token}"},
        json={"classification_id": B_target["id"], "decision": "approve"},
        timeout=10,
    )
    ok, msg = expect_status(r, 200, "approval B")
    if not ok:
        record("6a. B's approval has B's user_id", False, msg)
        return 1
    b_resp = r.json()
    if b_resp.get("approved_by_user_id") != B_id:
        record(
            "6a. B's approval has B's user_id",
            False,
            f"approved_by_user_id={b_resp.get('approved_by_user_id')!r}, want {B_id!r}",
        )
    else:
        record(
            "6a. B's approval has B's user_id",
            True,
            f"approved_by_user_id={b_resp['approved_by_user_id']}",
        )

    # CHECK 6b: audit log for this job shows BOTH users correctly.
    r = requests.get(f"{API}/audit/jobs/{job_id}", timeout=10)
    if r.status_code != 200:
        record("6b. audit has both A and B", False, f"status={r.status_code}")
        return 1
    items = r.json()["items"]
    a_events = [
        e
        for e in items
        if e.get("user_id") == A_id and e.get("event_type", "").startswith("approval.")
    ]
    b_events = [
        e
        for e in items
        if e.get("user_id") == B_id and e.get("event_type", "").startswith("approval.")
    ]
    if not a_events or not b_events:
        record(
            "6b. audit has both A and B",
            False,
            f"a_events={len(a_events)} b_events={len(b_events)}",
        )
    else:
        record(
            "6b. audit has both A and B",
            True,
            f"{len(a_events)} for A, {len(b_events)} for B",
        )

    # ---- CHECK 7: attribution — no cross-contamination ----
    print()
    print("CHECK 7: attribution — A's approvals only A; B's only B; no leak")
    # Pull the full audit log again for this job.
    items = r.json()["items"]
    approval_events = [e for e in items if e.get("event_type", "").startswith("approval.")]
    a_approvals = [e for e in approval_events if e.get("user_id") == A_id]
    b_approvals = [e for e in approval_events if e.get("user_id") == B_id]
    a_impostors = [e for e in approval_events if e.get("user_id") not in (A_id, B_id)]
    a_on_b_column = [
        e for e in approval_events if e.get("id") == b_resp["id"] and e.get("user_id") != B_id
    ]
    b_on_a_column = [
        e for e in approval_events if e.get("id") == a_resp["id"] and e.get("user_id") != A_id
    ]
    a_approvals_correctly_attributed = len(a_approvals) >= 1 and all(
        e["user_id"] == A_id for e in a_approvals
    )
    b_approvals_correctly_attributed = len(b_approvals) >= 1 and all(
        e["user_id"] == B_id for e in b_approvals
    )
    if (
        a_approvals_correctly_attributed
        and b_approvals_correctly_attributed
        and not a_impostors
        and not a_on_b_column
        and not b_on_a_column
    ):
        record(
            "7. attribution correct (no leak, no impostors)",
            True,
            f"a_approvals={len(a_approvals)} b_approvals={len(b_approvals)} impostors=0 a_on_b=0 b_on_a=0",
        )
    else:
        record(
            "7. attribution correct (no leak, no impostors)",
            False,
            f"a_approvals={len(a_approvals)} b_approvals={len(b_approvals)} impostors={len(a_impostors)} a_on_b={len(a_on_b_column)} b_on_a={len(b_on_a_column)}",
        )

    # ---- Summary ----
    print()
    print("=" * 78)
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"SUMMARY: {passed}/{total} checks passed")
    print("=" * 78)
    for name, ok, msg in checks:
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {name}: {msg}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
