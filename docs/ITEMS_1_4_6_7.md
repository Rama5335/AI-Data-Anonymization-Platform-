# Items 1, 4, 6, 7 — Completion Report

## Item 1: Authentication (JWT, RBAC)

**Status:** ✅ Complete — verified via `tools/verify_item1.py` (9/9 checks) and `tests/test_auth.py` (11 tests).

### What was built
- `User` model with `role` field (`user`/`admin`) and `migrations/20260723_0007_user_role.py`
- `app/security/{tokens,passwords}.py` — JWT creation/verification, Argon2 password hashing (`pwdlib`)
- `app/security/deps.py` — `get_current_user`, `require_role("admin")` FastAPI dependencies
- `app/api/auth.py`:
  - `POST /auth/login` — verify password, issue JWT. Bootstrap admin hook creates first user on startup
  - `POST /users` — create user (admin-only via `require_role("admin")`)
  - `GET /users/me` — return current user from JWT
- `app/api/approvals.py` updated: all endpoints now require JWT; `approver_user_id` extracted from token, not request body

### Key decisions
- **Bootstrap admin**: first `POST /auth/login` creates `root` user with `admin` role (idempotent)
- **JWT secret**: 32+ char hex string via `JWT_SECRET_KEY` env var
- **RBAC**: minimal — single `role` column, admin gate on `POST /users`

---

## Item 4: Servers UI (CRUD + Probe)

**Status:** ✅ Complete — verified via `tools/verify_item4.py` (26/26 checks) and `tests/test_servers_api.py` (10 tests + 1 skip).

### What was built
**Backend:**
- `app/api/servers.py` — all JWT-protected:
  - `GET /servers` — list all servers
  - `POST /servers` — create server (name, host, port, auth_type)
  - `GET /servers/{id}` — get server by ID
  - `PATCH /servers/{id}` — update server fields
  - `DELETE /servers/{id}` — delete (refuses if Connections reference it)
  - `POST /servers/{id}/probe` — short-lived connection test with provided credentials

**Frontend:**
- `components/ServersTab.tsx` — full CRUD UI:
  - Table listing all servers with name, host, port, auth, id
  - "New server" form (name, host, port, auth_type)
  - Edit server inline
  - Delete with confirmation
  - Probe panel (username/password inputs, reachable/unreachable result, database list)
  - "Use" button to activate a server for schema discovery
  - "Active" badge on the currently selected server
- Wired into `app.tsx` as "Servers" tab (visible to all authenticated users)
- `app.tsx` active server indicator: pill badge above ConnectionForm shows which registered server is active; "Clear" button to deselect
- Clicking "Use" sets the connection form's host/port and switches to the Schema tab

### Key decisions
- **Servers tab visible to all users** (not admin-only) — adding servers is operational
- **Credentials not stored** — probe sends username/password per-request, never persists them
- **"Use" activates by pre-filling ConnectionForm** — user still types credentials for the actual discovery

---

## Item 6: Per-Language LLM Documentation

**Status:** ✅ Complete — `docs/PER_LANGUAGE_LLM.md`.

### What was documented
- Supported providers: Ollama (default), Mock (testing), OpenAI-compatible (future)
- Model selection guidance (7-8B models are sufficient for PII classification)
- Licensing table (Llama 3.1 Community License, Apache 2.0 for Qwen/Mistral)
- Configuration surface: all env vars in `Settings` pydantic model
- How to change models (pull + env var + restart)
- How to add a new provider (implement `BaseLLMClient.complete()`, register in factory)

---

## Item 7: Production Docker Compose

**Status:** ✅ Complete — `docker/docker-compose.prod.yml` with supporting files.

### What was built
- `docker/docker-compose.prod.yml` — production stack:
  - **Caddy** reverse proxy (auto-TLS via Let's Encrypt, ports 80/443)
  - **Backend** with Docker secrets (not env vars) for JWT key and encryption key
  - **Frontend** (nginx, internal only)
  - **Ollama** (GPU-enabled, internal only)
  - Optional **Ollama API key gate** (nginx sidecar, commented by default)
- `docker/Caddyfile` — TLS config with public domain and internal fallback
- `docker/entrypoint.sh` — reads Docker secrets from `/run/secrets/` and exports as env vars
- `docker/.env.prod.example` — template for non-secret config
- `docker/backend.Dockerfile` updated — adds `entrypoint.sh`, switches from `CMD` to `ENTRYPOINT + CMD`
- `docker/README.md` updated — full production setup documentation

### Key decisions
- **Docker secrets** for all sensitive values (JWT key, encryption key, DB password)
- **No dev MSSQL in prod** — target MSSQL is external, configured through UI
- **Caddy auto-TLS** — simplest production TLS option, no manual cert management
- **GPU reservation** for Ollama — NVIDIA runtime with all GPUs
- **Optional API key gate** — commented out by default; useful for multi-tenant Docker networks

---

## Verification scripts

| Script | Checks | Result |
| --- | --- | --- |
| `tools/verify_item1.py` | 9 | 9/9 PASS |
| `tools/verify_item4.py` | 26 | 26/26 PASS |

## Test counts

| Suite | Tests | Status |
| --- | --- | --- |
| `tests/test_auth.py` | 11 | ✅ |
| `tests/test_servers_api.py` | 11 (1 skipped) | ✅ |
| Full backend | 136 pass, 4 skipped | ✅ |
| Frontend build | clean | ✅ |
| Backend lint (ruff + black) | clean | ✅ |
