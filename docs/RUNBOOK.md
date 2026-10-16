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

## Switch from mock LLM to real Ollama

```bash
$env:LLM_PROVIDER = "ollama"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:OLLAMA_MODEL = "qwen2.5:7b-instruct"
# Restart the API.
```

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

* **Unit (every push / PR):** `.github/workflows/tests.yml` —
  pytest + ruff + black + frontend `tsc -b && vite build`.
* **Live integration (weekly + manual):**
  `.github/workflows/integration.yml` — boots Docker MSSQL, pulls
  qwen2.5:7b-instruct, runs the live validators.
