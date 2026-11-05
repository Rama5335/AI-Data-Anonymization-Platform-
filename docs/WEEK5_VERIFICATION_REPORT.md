# Week 5 — Final Verification Report

**Date:** 2026-11-05  
**Project:** AI Data Anonymization Platform  
**Repository:** `D:\AI-Data-Anonymization-Platform\github-repo`  
**Scope:** Finish the missing Week 5 features, run the internal verification
gate, fix defects found, prepare deployment assets, and record what was
verified versus what remains untested.

**Status:** Verification complete for the automated/local scope. Deployment
assets are prepared but **nothing was deployed, committed, or pushed**.

---

## 1. Executive summary

### Verified in this run

| Check | Result |
| --- | --- |
| Backend unit/integration suite (`pytest -q`) | **247 passed, 4 skipped** |
| Backend lint (`ruff check .`) | **All checks passed** |
| Backend byte-compile (`compileall -q app tests tools`) | **exit 0** |
| Frontend lint (`npm run lint`) | **passed** |
| Frontend production build (`npm run build`) | **passed** (`45 modules transformed`) |
| Docker Compose dev config (`docker compose ... config --quiet`) | **exit 0** |
| Docker Compose prod config (`docker compose ... config --quiet`) | **exit 0** |
| Secret/database/backup ignore rules | **verified with `git check-ignore`** |
| Cloud AI provider removal | **verified by code search and tests** |

### Not verified in this run

These are explicitly **not** reported as passing:

* A live inference call to a real Ollama daemon with `qwen2.5:7b-instruct`.
* Live MSSQL validators that need a reachable SQL Server instance.
* Docker image builds (`docker compose build` / `docker build`).
* Caddyfile syntax validation or a real TLS/reverse-proxy deployment.
* Browser end-to-end and visual role-gating checks.
* Remediation of the frontend `npm audit` advisories.

The 4 skipped tests are the tests that require live infrastructure; they were
not converted to mocks just to make the count green.

---

## 2. Features implemented and verified

### 2.1 Local-only AI guarantee

* Removed the OpenAI provider and all OpenAI configuration fields from the
  backend settings, provider factory, settings API, frontend Settings screen,
  and API typings.
* `LLM_PROVIDER` now accepts only `ollama` or `mock`.
* `OLLAMA_BASE_URL` is validated at startup and rejects:
  * public DNS names and public IP addresses,
  * URLs containing embedded credentials,
  * non-HTTP(S) schemes,
  * malformed URLs without a hostname.
* Accepted endpoints are loopback/private IPs, `localhost`,
  `host.docker.internal`, `gateway.docker.internal`, and single-label Docker
  service names such as `http://ollama:11434`.
* The default model is standardized as `qwen2.5:7b-instruct` across compose
  files, Dockerfiles, scripts, and documentation.

### 2.2 Configurable CORS

* `CORS_ORIGINS` is a comma-separated allow-list parsed into
  `Settings.cors_origin_list`.
* `app.main` passes the parsed list to FastAPI's `CORSMiddleware`.
* Defaults contain only the local Vite development origins; production
  compose requires `CORS_ORIGINS` to be set explicitly.
* Tests cover allowed-origin and rejected-origin preflight requests.

### 2.3 Health and readiness

* `GET /health` — liveness only; never touches a dependency.
* `GET /health/ready` — runs `SELECT 1` against the metadata database.
  * HTTP 200 with `{"status":"ok","database":"ok"}` when ready.
  * HTTP 503 with a generic `{"status":"unavailable","database":"unreachable"}`
    body when the database is unreachable.
  * Driver text, connection strings, and credentials are never returned.
* Docker healthchecks in both compose files and `backend.Dockerfile` use
  `/health/ready`.

### 2.4 Security and authorization

* Discovery endpoints that previously surfaced raw driver exceptions now
  return redacted HTTP 503 responses for `databases`, `schemas`, and
  `objects`.
* Frontend role gating was completed:
  * Backup, Audit, and Users tabs are hidden for non-admins.
  * Server create/update/delete controls require admin.
  * Live masking/update apply controls are disabled for non-admins.
  * The bootstrap-admin hint was removed from the login screen.
* The backup gate, append-only audit trail, parameterized updates, rollback,
  and identifier validation remain intact.

### 2.5 Repository hygiene

* `.gitignore` now covers `.env`, `*.db`, `*.sqlite*`, `backups/`, `dist/`,
  editor/OS files, and virtual environments.
* `.env.example`, `docker/.env.prod.example`, and other example files remain
  visible to Git.
* No real `.env`, database file, backup, or build output is tracked.

### 2.6 Documentation and deployment assets

* Updated `README.md`, `README.merged.md`, `docs/RUNBOOK.md`,
  `docs/SECURITY.md`, `docs/PER_LANGUAGE_LLM.md`,
  `docs/ITEMS_1_4_6_7.md`, and `docker/README.md`.
* Added/updated:
  * `.env.example` with local-only AI, CORS, backup, and bootstrap notes.
  * `docker/.env.prod.example` as a safe production template.
  * `docker/docker-compose.yml` and `docker/docker-compose.prod.yml` with
    model, CORS, healthcheck, and build-context fixes.
  * `docker/Caddyfile` routing frontend traffic and `/api/*` to the backend.
  * `docker/backend.Dockerfile` readiness healthcheck.
  * `docker/frontend.Dockerfile` reproducible `npm ci` install.

---

## 3. Bugs fixed

1. **Cloud provider path was still reachable.** OpenAI settings, client code,
   factory branch, UI fields, and docs were removed; only `ollama` and `mock`
   are valid.
2. **Ollama URL accepted arbitrary remote endpoints.** Added a startup
   validator that enforces local/private-only inference endpoints.
3. **CORS was hard-coded to Vite ports.** Added `CORS_ORIGINS` and wired it
   into the FastAPI middleware.
4. **Health endpoint was liveness-only.** Added `/health/ready` with a
   database probe and redacted 503 response.
5. **Discovery errors could leak driver/credential text.** Added redacted 503
   handling and regression tests.
6. **`.env` was effectively tracked because of a `..env` typo in
   `.gitignore`.** Corrected the pattern and added database/backup/build
   exclusions.
7. **Non-admin users could see or trigger admin-only UI.** Tabs and mutations
   are now gated in the frontend.
8. **Production compose built from the wrong context and Caddy did not route
   the API.** Build contexts and Caddy routing were corrected.
9. **Ruff had no deterministic rule set and reported 117 pre-existing
   errors.** Added an explicit rule selection and applied import sorting; the
   gate is now clean.

---

## 4. Files changed

The working tree contains **66 modified tracked files**, **2 pre-existing
workflow deletions**, and **4 untracked paths**. The key changes are grouped
below.

### Backend

* `backend/app/core/config.py` — local-only Ollama validation, CORS parsing,
  provider restriction.
* `backend/app/main.py` — CORS middleware, `/health`, `/health/ready`.
* `backend/app/classification/factory.py`,
  `backend/app/classification/llm_client.py` — OpenAI removal; ollama/mock
  only.
* `backend/app/api/settings.py` — cloud-provider fields removed.
* `backend/app/api/discovery.py` — redacted 503 handling.
* `backend/app/agents/*`, `backend/app/api/*`, `backend/app/models/*`,
  `backend/app/schemas/*`, `backend/migrations/env.py` — supporting
  consistency/import-order changes from the verification pass.
* `backend/tests/test_config_and_health.py` — **new** config, CORS, and
  readiness tests.
* `backend/tests/test_discovery_api.py` — redaction regression tests.
* `backend/tests/*` — existing tests updated for the current behavior.
* `backend/pyproject.toml` — deterministic Ruff configuration.
* `backend/tools/*`, `backend/scripts/dev_up.ps1`, `backend/start_dev.bat` —
  model/command consistency.

### Frontend

* `frontend/src/app.tsx` — admin tab filtering, admin props, generic footer.
* `frontend/src/components/Settings.tsx` — OpenAI fields removed.
* `frontend/src/components/ServersTab.tsx` — admin-only server mutations.
* `frontend/src/components/ExecutionMonitor.tsx` — non-admin apply disabled.
* `frontend/src/components/BackupCenter.tsx`,
  `frontend/src/components/SchemaViewer.tsx`,
  `frontend/src/auth/LoginPage.tsx`,
  `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts` — related UI/type
  cleanup.

### Deployment and documentation

* `.env.example`, `.gitignore`
* `README.md`, `README.merged.md`
* `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`
* `docker/backend.Dockerfile`, `docker/frontend.Dockerfile`
* `docker/Caddyfile`, `docker/README.md`
* `docker/.env.prod.example` — **new**
* `docs/RUNBOOK.md`, `docs/SECURITY.md`, `docs/PER_LANGUAGE_LLM.md`,
  `docs/ITEMS_1_4_6_7.md`
* `docs/WEEK5_VERIFICATION_REPORT.md` — **new** (this report)

### Untracked / pre-existing items

* `backend/tests/test_config_and_health.py` — **new** test module.
* `docker/.env.prod.example` — **new** safe production template.
* `docs/WEEK5_VERIFICATION_REPORT.md` — **new** (this report).
* `backend/.github/` — untracked copy of workflow files; not created or
  modified by this verification pass.
* The two root `.github/workflows/*.yml` deletions were already present in
  the working tree before this pass and were not restored.
* No commit or push was performed.

---

## 5. Test results

### 5.1 Backend suite

Command:

```powershell
cd backend
.\.cpython311-venv\Scripts\python.exe -m pytest -q
```

Result:

```text
247 passed, 4 skipped in 97.72s
```

A repeat run with skip reasons (`pytest -q -rs`) reported:

```text
SKIPPED [1] tests/test_backup_agent.py:80: live BACKUP DATABASE requires a non-Docker MSSQL (Docker is covered by validate_phase9.ps1)
SKIPPED [1] tests/test_backup_agent.py:90: live BACKUP DATABASE requires a non-Docker MSSQL (Docker is covered by validate_phase9.ps1)
SKIPPED [1] tests/test_backup_agent.py:102: live BACKUP DATABASE requires a non-Docker MSSQL (Docker is covered by validate_phase9.ps1)
SKIPPED [1] tests/test_servers_api.py:208: local MSSQL not reachable in this environment: tuple index out of range

247 passed, 4 skipped in 104.06s
```

Baseline before the Week 5 changes was **223 passed, 4 skipped**; the net
increase is 24 tests (22 config/CORS/health tests plus 2 discovery-redaction
tests).

### 5.2 Backend lint and compile

```powershell
cd backend
.\.cpython311-venv\Scripts\python.exe -m ruff check .
.\.cpython311-venv\Scripts\python.exe -m compileall -q app tests tools
```

Result:

```text
All checks passed!
compileall_exit=0
```

### 5.3 Frontend

```powershell
cd frontend
npm run lint
npm run build
```

Result:

```text
eslint .            -> no errors
tsc -b && vite build
45 modules transformed
dist/index.html                 0.77 kB
dist/assets/index-CiO_6TWm.css 20.03 kB
dist/assets/index-BG5CI5G8.js 209.37 kB
built in 1.04s
```

`npm ci` had been run successfully in this verification cycle. It reported
`15 vulnerabilities (10 moderate, 5 high)`; no automatic audit fix was applied
because `npm audit fix --force` can introduce breaking major-version changes.

### 5.4 Docker Compose configuration

Both files were parsed with dummy values only; no real secrets are recorded
here.

```powershell
docker compose -f docker/docker-compose.yml config --quiet
docker compose -f docker/docker-compose.prod.yml config --quiet
```

Result:

```text
dev_compose_exit=0
prod_compose_exit=0
```

This validates interpolation and YAML structure, **not** that the images
build or that the stack starts.

---

## 6. Security verification

| Item | Result |
| --- | --- |
| Real `.env` tracked? | No — `git check-ignore -v backend/.env` matches `.gitignore` |
| Metadata DB tracked? | No — `backend/app.db` is ignored by `*.db` |
| Backups tracked? | No — `backups/` and `**/backups/` are ignored |
| Frontend build output tracked? | No — `**/dist/` is ignored |
| Production env template visible? | Yes — `docker/.env.prod.example` is explicitly unignored |
| `.env.example` visible? | Yes — explicitly unignored |
| Cloud AI providers supported? | No — only `ollama` and `mock`; remote URLs rejected |
| CORS wildcard? | No — explicit origin list, production requires `CORS_ORIGINS` |
| Readiness error leakage? | No — generic 503 body; test asserts the secret is absent |
| Discovery error leakage? | No — redacted 503 responses; regression tests assert credentials do not appear |
| Admin-only UI actions? | Backend enforces RBAC; frontend hides/gates the corresponding controls |

No secret values, database credentials, or tokens were printed in this report
or written into tracked files.

---

## 7. Deployment preparation

Prepared, **not deployed**:

* `.env.example` documents every required variable and safe placeholders.
* `docker/.env.prod.example` documents production values and Docker secrets.
* `docker/docker-compose.yml` provides the local stack with a disposable MSSQL
  container, local Ollama access, and readiness healthchecks.
* `docker/docker-compose.prod.yml` provides the production topology:
  Caddy TLS termination, internal-only backend/frontend, Ollama service,
  Docker secrets, and no host-exposed backend/frontend ports.
* `docker/Caddyfile` serves the frontend and proxies `/api/*` to the backend.
* `docs/RUNBOOK.md` contains:
  * health/readiness commands,
  * new-machine setup,
  * production deployment checklist,
  * mock-to-Ollama switch instructions,
  * backup/restore notes.
* `README.md` contains the quick-start and quality-gate commands.

Deployment assumptions that still require an operator:

* A real metadata MSSQL instance exists and `alembic upgrade head` has run.
* Docker secrets named `jwt_secret_key` and `credential_encryption_key` exist.
* `CORS_ORIGINS` is set to the real HTTPS origin.
* `qwen2.5:7b-instruct` is pulled into the Ollama volume.
* The backup directory is writable by both the API and MSSQL.
* DNS points at the Caddy host and ports 80/443 are reachable.

---

## 8. Tests not run / unverified assumptions

1. **Live Ollama inference** — no request was made to a real daemon in this
   run. The automated suite uses `LLM_PROVIDER=mock`.
2. **Live MSSQL validators** — the four skipped tests need a reachable SQL
   Server; the Docker-based phase validators were not executed.
3. **Docker image builds** — only `docker compose config` was run. Base-image
   availability, `npm ci` inside the image, ODBC driver installation, and
   runtime startup were not exercised.
4. **Caddyfile validation** — no `caddy validate` or real TLS handshake was
   performed.
5. **Browser E2E / visual checks** — role-based tab hiding and disabled
   controls were reviewed in code and covered by build-time type checking, but
   not clicked through in a browser.
6. **`npm audit` remediation** — 15 advisories remain; no dependency upgrade
   was attempted.
7. **Production restore drill** — `RESTORE VERIFYONLY` is not a test-restore;
   no live restore was performed.
8. **Login rate limiting/lockout** — not implemented; documented as a known
   limitation.

---

## 9. Remaining tasks

Recommended before a public deployment:

1. Run `docker compose -f docker/docker-compose.yml up -d --build` on a clean
   machine and confirm both healthchecks become healthy.
2. Run a live classification against Ollama with `qwen2.5:7b-instruct` and
   confirm the timeout/retry behavior on a slow first call.
3. Run the live MSSQL validators (`validate_phase3/5/6/9.ps1`) against a
   disposable database.
4. Validate `docker/Caddyfile` with `caddy validate` and test a real HTTPS
   origin.
5. Perform a browser smoke test for admin/non-admin role gating.
6. Review and, where safe, upgrade the 15 npm advisories.
7. Decide whether to add login rate limiting/lockout and separation of duties
   for approvals (currently documented limitations, not regressions).
8. Remove `BOOTSTRAP_ADMIN_*` variables after the first successful login.

---

## 10. Git state

* Changes are **uncommitted**.
* Nothing was pushed to GitHub.
* `git status --short` shows 66 modified tracked files, 2 pre-existing
  workflow deletions, and 4 untracked paths.
* The pre-existing root workflow deletions and `backend/.github/` copy were
  left untouched.
* Real secrets and local databases remain ignored and were not read into the
  report.
