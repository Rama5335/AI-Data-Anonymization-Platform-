# Runbook

Common operations and how to do them safely.

## Run the stack

```bash
pwsh backend/scripts/dev_up.ps1       # MSSQL + API + Vite on 127.0.0.1
```

Stop:

```bash
Get-Process -Name python,node | Where-Object { $_.Path -like "*cpython311-venv*" -or $_.Path -like "*nodejs*" } | Stop-Process -Force
docker rm -f ai-anonymizer-mssql-validation
```

## Health and readiness

```bash
curl http://127.0.0.1:8765/health          # liveness: process is serving HTTP
curl http://127.0.0.1:8765/health/ready    # readiness: metadata DB answers SELECT 1
```

`/health` never touches a dependency. `/health/ready` returns HTTP 503
with a generic JSON body when the metadata database is unreachable; use
it for Docker/orchestrator healthchecks and load-balancer membership.

## New-machine setup

1. Install Python 3.11, Node.js LTS, and Ollama.
2. Clone/copy the repository (never copy a real `.env` or `app.db`).
3. Create the backend virtualenv and install dependencies:
   ```bash
   cd backend
   python -m venv .cpython311-venv
   # Windows: .\.cpython311-venv\Scripts\activate
   pip install -e ".[dev]"
   ```
4. Copy `.env.example` to `backend/.env` and fill in **new** values for
   `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, and `DATABASE_URL`.
5. Run `alembic upgrade head` to create/update the metadata schema.
6. Pull the model: `ollama pull qwen2.5:7b-instruct`.
7. Start the API (`uvicorn app.main:app --host 127.0.0.1 --port 8765`)
   and the frontend (`cd frontend && npm ci && npm run dev`).
8. Create the first admin with `BOOTSTRAP_ADMIN_USERNAME` /
   `BOOTSTRAP_ADMIN_PASSWORD`, then remove those variables and restart.

`.env`, `app.db`, `backups/`, and `dist/` are git-ignored. Verify with
`git check-ignore -v backend/.env` before committing.

## Production deployment checklist (prepare, do not deploy from this repo)

- [ ] Set `APP_ENV=production`.
- [ ] Generate fresh `JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY`;
      store them as Docker secrets (see `docker/entrypoint.sh`).
- [ ] Point `DATABASE_URL` at the metadata MSSQL instance; do **not**
      reuse a developer SQLite file.
- [ ] Set `CORS_ORIGINS` to the public HTTPS origin, for example
      `https://anonymizer.example.com` (never `*`).
- [ ] Keep `OLLAMA_BASE_URL` local/private
      (`http://ollama:11434` inside compose or
      `http://host.docker.internal:11434` for host Ollama). Public URLs
      are rejected at startup.
- [ ] Pull `qwen2.5:7b-instruct` (or set `OLLAMA_MODEL` to a model you
      have pulled) before the first classification request.
- [ ] Mount a backup volume visible to both MSSQL and the API container;
      `BACKUP_DIR` must contain the `.bak` files the gate verifies.
- [ ] Set `BACKUP_KEEP`, `SAFE_UPDATE_BATCH_SIZE`, and
      `WORKFLOW_APPROVAL_TIMEOUT_HOURS` explicitly.
- [ ] Terminate TLS at Caddy (or another reverse proxy) and expose only
      the proxy; backend/frontend stay on the internal network.
- [ ] Configure the healthcheck to use `/health/ready`.
- [ ] Remove bootstrap admin variables after the first successful login.
- [ ] Review the known limitations in `docs/SECURITY.md` (single
      approver, no login rate limiting, `RESTORE VERIFYONLY`, npm audit
      advisories).

## Switch from mock LLM to real Ollama

```bash
$env:LLM_PROVIDER = "ollama"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5:7b-instruct"
# Restart the API.
```

Only local/private endpoints are accepted. `LLM_PROVIDER=openai` (or any
cloud provider) is rejected by configuration validation; the platform
never sends schema/sample data to a public inference endpoint.

To verify: hit `GET /health` and then `POST /classification/columns/{id}`
on a known column. A real model call is much slower than the mock
(several seconds per column on first run, then faster as the model
warms up).

## Run a single live validator

```bash
pwsh backend/scripts/validate_phase5.ps1
```

Each script:
1. Starts the disposable MSSQL container.
2. Runs `alembic upgrade head`.
3. Boots the FastAPI app.
4. Exercises the phase's endpoints and asserts behaviour.
5. Tears the container down on exit.

## Run all unit tests

```bash
cd backend
pytest -q
ruff check .
python -m compileall -q app tests tools
```

Frontend gate:

```bash
cd frontend
npm ci
npm run lint
npm run build
```

## Run a specific live test against an existing container

```bash
# In one shell: start the API.
$env:DATABASE_URL = "mssql+pyodbc://sa:<SA_PASSWORD>@127.0.0.1:1433/anonymizer_app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
$env:JWT_SECRET_KEY = "..."
$env:CREDENTIAL_ENCRYPTION_KEY = "..."
uvicorn app.main:app

# In another shell: hit the API.
curl http://127.0.0.1:8765/health
```

## Recover from a wiped masking job

Masking jobs are wiped automatically after a successful `apply` (the
token key is destroyed; `Token Vault` rows become useless). If you
need to run a re-classification:

1. Issue a fresh `/schema/discover` to get a new `job_id`.
2. Re-classify, re-approve, re-backup, re-apply.

There is no way to "rewind" a wipe — by design.

## Backups

* Default directory: `./backups/`.
* Default retention: 5 files per database (FIFO).
* Override with `BACKUP_DIR` and `BACKUP_KEEP` env vars.
* The validator's MSSQL container bind-mounts `./backups/` to
  `/var/opt/mssql/backups/` so `BACKUP DATABASE TO DISK = N'...bak'`
  has a writable path.

## Troubleshooting

### "BACKUP DATABASE reported success but no .bak file was written"

The target is in a Docker container whose filesystem is isolated
from the host. Mount a shared volume:

```bash
docker run -d --name mssql -p 1433:1433 \
  -v ${PWD}/backups:/var/opt/mssql/backups \
  -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=<SA_PASSWORD> \
  mcr.microsoft.com/mssql/server:2022-latest
```

Then point `BACKUP_DIR` at `/var/opt/mssql/backups` (the in-container
path) when running the API inside the container, or at the host
path when running the API on the host.

### "Cannot decrypt masking job secrets"

The application-level `CREDENTIAL_ENCRYPTION_KEY` changed (or the
metadata DB was restored from a different environment). Old
`masking_jobs` rows become unreadable. The recovery is a new
`/schema/discover` and `/classification/jobs/{id}` cycle.

### "Ollama request timed out after 120s"

The model is loading or the prompt is large. Increase
`OLLAMA_REQUEST_TIMEOUT_SECONDS`. On first request, qwen2.5:7b
takes ~30s to load into the GPU/CPU.

### "Phase 5 LOB truncation: value > cap"

The SQL-side CAST is working as designed. The cell's `truncated:
true` flag and `original_length` are authoritative. If you need a
larger cap, raise `max_bytes_per_value` in the sampling request
body (default 512).

## Re-baseline the development database

```bash
pwsh backend/scripts/validate_phase6.ps1
```

This drops and re-creates `anonymizer_app`, runs the latest
migration, and re-seeds the fixtures.

## CI

CI workflow definitions live under `.github/workflows/` when present in the
checkout:

* **Unit (every push / PR):** `tests.yml` — pytest + ruff + frontend
  `tsc -b && vite build`.
* **Live integration (weekly + manual):** `integration.yml` — boots Docker
  MSSQL, pulls qwen2.5:7b-instruct, runs the live validators.

If the workflows are absent from a snapshot, run the same commands locally
from `backend/` and `frontend/` (see "Run all unit tests" above).
