"""End-to-end live validation: drive the full pipeline through the API.

Run from backend/:

    python tools/validate_e2e.py

The script:
  1. /health
  2. /schema/discover
  3. /sampling/tables/{Customers,CustomerNotes}
  4. /classification/jobs/{job_id}  (real Ollama)
  5. /approvals/jobs/{job_id}/pending
  6. /backups  (with BACKUP_DIR set to the in-container path)
  7. /workflow/jobs/{job_id}/transition  (full state machine)
  8. /audit/jobs/{job_id}  (count + distinct event types)
  9. /workflow/phases
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import requests


CONN: dict[str, Any] = {
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

API = "http://127.0.0.1:8765"


def banner(s: str) -> None:
    print()
    print("=" * 72)
    print(s)
    print("=" * 72)


def step(s: str) -> None:
    print()
    print(f"--- {s}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-classify",
        action="store_true",
        help="Skip the slow classification step (real Ollama).",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip the live backup step (needs a writable in-container path).",
    )
    args = parser.parse_args()

    # 0. health
    banner("0. health")
    r = requests.get(f"{API}/health", timeout=5)
    print("GET /health ->", r.status_code, r.text.strip())

    # 1. schema discover
    banner("1. schema/discover")
    t0 = time.time()
    r = requests.post(f"{API}/schema/discover", json={"connection": CONN}, timeout=30)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    print(
        "POST /schema/discover ->",
        r.status_code,
        "job_id=",
        job_id,
        f"elapsed={time.time() - t0:.1f}s",
    )

    # 2. sample
    banner("2. /sampling/tables/{Customers, CustomerNotes}")
    job_body = requests.get(f"{API}/schema/job/{job_id}", timeout=10).json()
    by_name = {t["table_name"]: t for t in job_body["tables"]}
    for target in ("Customers", "CustomerNotes"):
        tid = by_name[target]["id"]
        t0 = time.time()
        r = requests.post(
            f"{API}/sampling/tables/{tid}",
            json={"connection": CONN, "row_count": 5, "max_bytes_per_value": 512},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        print(
            f"  sample {target}: status={r.status_code} rows={body['row_count']} "
            f"cached={body['cached']} elapsed={time.time() - t0:.1f}s"
        )

    # 3. classification (real Ollama)
    banner("3. /classification/jobs/{id}  (real Ollama)")
    if args.skip_classify:
        print("  skipped (--skip-classify)")
    else:
        t0 = time.time()
        r = requests.post(
            f"{API}/classification/jobs/{job_id}",
            json={"max_retries": 2},
            timeout=600,
        )
        r.raise_for_status()
        body = r.json()
        print(
            f"  status={r.status_code} classified={body['classified_count']} "
            f"failed={body['failed_count']} elapsed={time.time() - t0:.1f}s"
        )
        for r0 in body["results"]:
            print(
                f"    {r0['schema_name']}.{r0['table_name']}.{r0['column_name']}: "
                f"{r0['sensitivity']} -> {r0['suggested_strategy']} "
                f"(conf={r0['confidence']:.0%}, attempts={r0['attempts']}, ok={r0['ok']})"
            )

    # 4. approvals (the API exposes the queue; submission requires a
    # real user_id which we don't have in this smoke test).
    banner("4. /approvals/jobs/{id}/pending")
    r = requests.get(f"{API}/approvals/jobs/{job_id}/pending", timeout=10)
    r.raise_for_status()
    pending = r.json()["items"]
    print(f"  pending={len(pending)}")
    for it in pending:
        print(
            f"    {it['schema_name']}.{it['table_name']}.{it['column_name']}: "
            f"{it['sensitivity']} -> {it['suggested_strategy']}"
        )

    # 5. backup
    banner("5. /backups  (BACKUP DATABASE)")
    if args.skip_backup:
        print("  skipped (--skip-backup)")
    else:
        t0 = time.time()
        r = requests.post(
            f"{API}/backups",
            json={"connection": CONN, "database": "anonymizer_app", "keep": 3},
            timeout=120,
        )
        if r.status_code == 503:
            print(f"  BACKUP failed: {r.json()['detail']}")
            print(
                "  (the in-container path may not be writable; the API's BACKUP_DIR "
                "must point at the path MSSQL can reach.)"
            )
        else:
            r.raise_for_status()
            body = r.json()
            print(
                f"  status={r.status_code} verified={body['verified']} "
                f"path={body['backup_path']} size={body['size_bytes']}B "
                f"elapsed={time.time() - t0:.1f}s"
            )

    # 6. workflow advance
    banner("6. /workflow/jobs/{id}/transition  (full state machine)")
    phases = [
        "discovery",
        "schema",
        "sample",
        "classify",
        "waiting_for_approval",
        "ready_for_backup",
        "backup",
        "mask",
        "update",
        "audit",
        "completed",
    ]
    for p in phases:
        r = requests.post(
            f"{API}/workflow/jobs/{job_id}/transition",
            json={"to_phase": p},
            timeout=10,
        )
        r.raise_for_status()
        print(f"  -> {r.json()['state']['phase']} ({r.status_code})")

    # 7. final workflow state
    banner("7. /workflow/jobs/{id}  (final state)")
    r = requests.get(f"{API}/workflow/jobs/{job_id}", timeout=10)
    r.raise_for_status()
    state = r.json()
    print(f"  phase={state['phase']}  is_stale={state['is_stale']}")

    # 8. audit log
    banner("8. /audit/jobs/{id}  (full audit trail)")
    r = requests.get(f"{API}/audit/jobs/{job_id}?limit=200", timeout=10)
    r.raise_for_status()
    items = r.json()["items"]
    print(f"  total events: {len(items)}")
    distinct = sorted({e["event_type"] for e in items})
    for et in distinct:
        n = sum(1 for e in items if e["event_type"] == et)
        print(f"    {n:>3} x  {et}")

    # 9. workflow phases
    banner("9. /workflow/phases")
    r = requests.get(f"{API}/workflow/phases", timeout=10)
    r.raise_for_status()
    for from_p, to_ps in r.json().items():
        print(f"  {from_p} -> {', '.join(to_ps) if to_ps else '(terminal)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
