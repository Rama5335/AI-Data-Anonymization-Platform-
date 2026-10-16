# Phase 3 Validation Report

**Date:** 2026-07-23
**Scope:** AI Data Anonymization Platform — Phase 3 (MSSQL connectivity & discovery)
**Verdict:** ✅ **PASS — live validation successful against a disposable Docker MSSQL container.**

---

## 1. What was validated

Phase 3 from `plan.md` §3 (Database Discovery) and `implement_plan.md` §3
(MSSQL Connectivity & Discovery Agent), exercised end-to-end against a real
SQL Server 2022 instance running in Docker, not a mock.

The validation script (`backend/scripts/validate_phase3.ps1`) was the source
of truth. It:

1. Starts `mcr.microsoft.com/mssql/server:2022-latest` in a one-shot
   container on `127.0.0.1:1433` (SA password, `ACCEPT_EULA=Y`).
2. Waits for SQL Server to accept logins.
3. Creates the `anonymizer_app` database (if missing).
4. Runs `alembic upgrade head` against it via `DATABASE_URL` (env override).
5. Boots the FastAPI app on `127.0.0.1:8765` in the background.
6. Hits `/health` and every `/discovery/*` route through `Invoke-WebRequest`.
7. Tears the container down in a `finally` block — never persists.

The container is removed at the end of every run; no Docker state, no DB
data, no credentials survive the run.

## 2. Live run output

```
Starting MSSQL container ai-anonymizer-mssql-validation...
Waiting for MSSQL...
Waiting for MSSQL on 127.0.0.1:1433 to accept logins....... ready.
Creating anonymizer_app database if missing...
anonymizer_app ready
Running alembic upgrade head...
Launching FastAPI on port 8765...
Probing endpoints...
  GET  /health                       -> 200: {"status":"ok"}
  POST /discovery/test-connection    -> 200: {"success":true}
  POST /discovery/databases          -> 200: {"databases":["anonymizer_app","master","model","msdb","tempdb"]}
  POST /discovery/schemas            -> 200: {"schemas":["db_accessadmin","db_backupoperator",...,"INFORMATION_SCHEMA","sys"]}
  POST /discovery/objects?object_type=TABLE -> 200: {"objects":[{"schema_name":"dbo","name":"alembic_version","object_type":"BASE TABLE"},{"schema_name":"dbo","name":"approvals","object_type":"BASE TABLE"},{"schema_name":"dbo","name":"audit_logs","object_type":"BASE TABLE"... ]}
  POST /discovery/objects?object_type=VIEW  -> 200: {"objects":[]}
Phase 3 validation: PASS
Stopping MSSQL container...
```

Every endpoint returns **200** with real data from the live SQL Server:

- The five databases returned by `/discovery/databases` are the actual
  `sys.databases` rows of the running instance.
- The schemas list is the actual `sys.schemas` content of `anonymizer_app`.
- The `TABLE` object list is the actual `INFORMATION_SCHEMA.TABLES` content
  of `anonymizer_app` — and includes the `alembic_version` table, which
  proves the migrations actually ran against this database, not a mock.

## 3. Static checks (re-run after all changes)

```
$ pytest -q
................                                            [100%]
16 passed in 1.39s

$ ruff check .
All checks passed!

$ black --check .
All done! ✨ 🍰 ✨
25 files would be left unchanged.
```

## 4. What changed in this session

The handoff claimed Phase 3 was complete. After reading the code, two
gaps and one bug were found and fixed; everything else was left as-is.

### 4.1 Wired the missing Discovery API

The handoff listed "Discovery API" as implemented, but `app/main.py`
exposed only `/health`. Added:

- `app/api/discovery.py` — `APIRouter` with four read-only routes:
  - `POST /discovery/test-connection`
  - `POST /discovery/databases`
  - `POST /discovery/schemas`
  - `POST /discovery/objects?object_type=TABLE|VIEW`
- `app/database/url.py` — `build_mssql_url()` builds a one-shot
  `mssql+pyodbc` URL from a typed `ConnectionDetails` payload. Password
  is `SecretStr`; URL is never logged.
- `app/main.py` — includes the router.
- `app/schemas/discovery.py` — expanded with `ConnectionDetails`,
  `DatabaseListResponse`, `SchemaListResponse`, `ObjectInfo`,
  `ObjectListResponse`.

### 4.2 Fixed latent bug in `MssqlDiscoveryClient.list_objects`

`INFORMATION_SCHEMA.TABLES.TABLE_TYPE` stores the value `BASE TABLE`,
not `TABLE`, so the previous query (`TABLE_TYPE = :object_type` with
`object_type="TABLE"`) returned zero rows. The public `TABLE` / `VIEW`
allow-list is preserved; the value is now translated to the catalog
value internally. `app/database/mssql.py:53`.

### 4.3 Renamed `schema` → `schema_name` in the API surface

Pydantic v2 emits a `UserWarning` when a `BaseModel` field is named
`schema` (it shadows `BaseModel.schema()`). Renamed consistently across
`DatabaseObject` (the dataclass) and `ObjectInfo` (the API response).
Matches the existing `ClassificationResult.schema_name` field in
`app/models/entities.py:69`.

### 4.4 Tests

- `tests/test_discovery_api.py` (new, 8 tests) — covers every route,
  503 on driver failure, 400 on bad object type, 422 on missing
  password, and 200 happy paths.
- `tests/test_url_and_table_mapping.py` (new, 3 tests) — covers
  `build_mssql_url()` query options, the `password` validation
  rejection, and the `TABLE` → `BASE TABLE` translation.
- `tests/test_application.py`, `tests/test_mssql_discovery.py`,
  `tests/test_security.py` — unchanged, still pass.

### 4.5 Tooling hygiene

- Pinned `pyproject.toml` so `pytest` and `black` only see project
  sources — the venvs were being scanned and `black` was trying to
  reformat 1300+ site-packages files.
- Silenced a starlette/httpx deprecation warning that was cluttering
  pytest output (`filterwarnings` in `pyproject.toml`).
- Made `migrations/env.py` honor `DATABASE_URL` from the environment
  so the same migration can drive production and disposable test
  targets without committing secrets to `alembic.ini`.

### 4.6 Validation script

`backend/scripts/validate_phase3.ps1` — the end-to-end live validator
described in §1. Idempotent: removes any pre-existing container with
the same name before starting. Cleans up its working directory in
`finally`.

## 5. Pre-flight that had to happen on this machine

The handoff assumed Docker + WSL2 + the ODBC driver were already in
place. They were not. The following had to be set up before the script
could run:

| Item | How |
| --- | --- |
| `Microsoft ODBC Driver 18 for SQL Server` 18.6.2.1 | `winget install --id Microsoft.msodbcsql.18 -e` (no admin) |
| WSL2 feature | `dism /enable-feature` (admin) + `shutdown /r /t 0` (admin) |
| WSL2 Linux kernel | Booted cleanly from the kernel bundled with Windows 11 24H2; no extra `wsl --update` needed |
| Docker Desktop 4.83.0 daemon | Pre-installed by the user; required the WSL2 backend to be active |

After those four, `docker run --rm hello-world` worked, and the
validation script passed.

## 6. Acceptance against `plan.md` §3 (Database Discovery)

| Requirement | Status | Where |
| --- | --- | --- |
| Connect to MSSQL Server | ✅ | `MssqlDiscoveryClient` + `app/database/url.py` |
| Test connection | ✅ | `MssqlDiscoveryClient.test_connection` + `/discovery/test-connection` |
| Browse databases | ✅ | `list_databases` + `/discovery/databases` (validated live) |
| Browse tables | ✅ | `list_objects("TABLE")` + `/discovery/objects?object_type=TABLE` (validated live) |
| Browse views | ✅ | `list_objects("VIEW")` + `/discovery/objects?object_type=VIEW` (validated live, returned `[]` because no views exist yet — endpoint works) |
| Browse schemas | ✅ | `list_schemas` + `/discovery/schemas` (validated live) |
| Read-only — never mutate target | ✅ | All queries are catalog reads; no DDL/DML path exists in `MssqlDiscoveryClient` |

## 7. Open items (out of Phase 3 scope)

- Phase 4+ features (Schema Agent with column metadata, sampling, etc.)
  remain unimplemented by design.
- The `/discovery/*` routes take connection details in the request body
  rather than persisting servers; server registration persistence is a
  later phase.
- `View` discovery returned an empty list because the test database
  has no views. The endpoint is exercised and returns 200.

## 8. Reproducing this run

From `backend`:

```powershell
.\scripts\validate_phase3.ps1
```

Expected exit: script ends with `Phase 3 validation: PASS`. Container
is removed on exit.
