"""Item 4 live verification: Servers UI (backend API + frontend component)."""

import os
import pathlib
import requests

BASE = "http://127.0.0.1:8765"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ok = 0
fail = 0
checks = []


def check(label: str, cond: bool, detail: str = ""):
    global ok, fail
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" ({detail})" if detail else ""))
    if cond:
        ok += 1
    else:
        fail += 1
    checks.append((label, cond))


# --- 1. Login as bootstrap admin ---
print("\n=== 1. Auth ===")
r = requests.post(f"{BASE}/auth/login", json={"username": "root", "password": os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "CHANGE_ME")})
check("POST /auth/login returns 200", r.status_code == 200, f"status={r.status_code}")
token = r.json().get("access_token", "")
check("access_token present", bool(token))
headers = {"Authorization": f"Bearer {token}"}

# --- 2. CRUD servers ---
print("\n=== 2. Servers CRUD ===")
r = requests.get(f"{BASE}/servers", headers=headers)
check("GET /servers returns 200", r.status_code == 200, f"status={r.status_code}")
initial_count = len(r.json())
check("initial server count is 0", initial_count == 0, f"count={initial_count}")

# Create server
r = requests.post(
    f"{BASE}/servers",
    json={
        "name": "Test-MSSQL",
        "host": "127.0.0.1",
        "port": 1433,
        "auth_type": "sql",
    },
    headers=headers,
)
check("POST /servers returns 201", r.status_code == 201, f"status={r.status_code}")
server = r.json()
server_id = server.get("id", "")
check("server has id", bool(server_id), f"id={server_id[:8]}")
check("server name matches", server.get("name") == "Test-MSSQL", f"name={server.get('name')}")
check("server host matches", server.get("host") == "127.0.0.1", f"host={server.get('host')}")

# List servers
r = requests.get(f"{BASE}/servers", headers=headers)
check(
    "GET /servers returns 1 server",
    r.status_code == 200 and len(r.json()) == 1,
    f"count={len(r.json())}",
)

# Update server
r = requests.patch(
    f"{BASE}/servers/{server_id}", json={"name": "Test-MSSQL-Updated"}, headers=headers
)
check("PATCH /servers/{id} returns 200", r.status_code == 200, f"status={r.status_code}")
check("name updated", r.json().get("name") == "Test-MSSQL-Updated", f"name={r.json().get('name')}")

# Get server
r = requests.get(f"{BASE}/servers/{server_id}", headers=headers)
check("GET /servers/{id} returns 200", r.status_code == 200, f"status={r.status_code}")

# Probe server (live MSSQL)
r = requests.post(
    f"{BASE}/servers/{server_id}/probe",
    json={
        "username": "sa",
        "password": os.environ.get("MSSQL_SA_PASSWORD", "CHANGE_ME"),
    },
    headers=headers,
)
check("POST /servers/{id}/probe returns 200", r.status_code == 200, f"status={r.status_code}")
probe = r.json()
check(
    "probe reachable (or driver unavailable)",
    probe.get("reachable") is True or "driver" in (probe.get("error") or "").lower(),
    f"reachable={probe.get('reachable')}, error={probe.get('error')}",
)

# Delete server
r = requests.delete(f"{BASE}/servers/{server_id}", headers=headers)
check("DELETE /servers/{id} returns 204", r.status_code == 204, f"status={r.status_code}")
r = requests.get(f"{BASE}/servers", headers=headers)
check("server count back to 0", len(r.json()) == 0, f"count={len(r.json())}")

# --- 3. Frontend build ---
print("\n=== 3. Frontend ===")
dist = REPO_ROOT / "frontend" / "dist" / "index.html"
check("frontend/dist/index.html exists", dist.exists())

src = (REPO_ROOT / "frontend" / "src" / "components" / "ServersTab.tsx").read_text()
check("ServersTab exports", "export function ServersTab" in src)
check("ServersTab accepts onActivate prop", "onActivate" in src)
check("ServersTab accepts activeServerId prop", "activeServerId" in src)
check("ServersTab has 'Use' button for activation", '"Use"' in src or "'Use'" in src)
check("ServersTab has 'Active' badge", '"Active"' in src or "'Active'" in src)

app = (REPO_ROOT / "frontend" / "src" / "app.tsx").read_text()
check("app.tsx imports ServersTab", 'from "./components/ServersTab"' in app)
check("app.tsx has 'servers' tab", '"servers"' in app)
check("app.tsx wires onActivate to ServersTab", "onActivate" in app)
check("app.tsx shows active server indicator", "activeServer" in app)

# --- Summary ---
print(f"\n{'='*50}")
print(f"  {ok} PASS, {fail} FAIL")
if fail == 0:
    print("  Item 4 (Servers UI) verified.")
else:
    print("  SOME CHECKS FAILED — review above.")
print(f"{'='*50}")
