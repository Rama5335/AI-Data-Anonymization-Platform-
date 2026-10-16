# Phase 5 — Data Sampling

**Status:** complete and live-validated.

## Scope

Implements `plan.md` §3 "Data Sampling" and `implement_plan.md` §6.
Read-only sample of up to N rows per discovered table, with a configurable
per-value byte cap and explicit truncation flags.

## What's in the box

### Backend
- `app/database/sampling.py` — `MssqlSampler.fetch_samples()` and
  `ColumnSpec`. Issues `SELECT TOP (@n) <expr> AS [col] ... FROM
  [schema].[table] WITH (NOLOCK)`. Per-column CAST is applied at the SQL
  layer for any sizeable column whose declared length exceeds the cap
  (`nvarchar`/`varchar`/`nchar`/`char`/`text`/`ntext`/`xml` use
  `CAST(CAST(col AS NVARCHAR(MAX)) AS NVARCHAR(N))`; `varbinary`/`binary`/
  `image` use `CAST(CAST(col AS VARBINARY(MAX)) AS VARBINARY(N))`). The
  original column size is also fetched via `LEN(col) AS [col__orig]`
  and folded into the response as `original_length`.
- `app/agents/sampling.py` — `SamplingAgent.sample_table()`. Cached
  per `(job_id, table_id)`. View sampling is opt-in via
  `include_views=True`. Cached re-reads apply the caller's
  `excluded_column_names` filter on top of the stored rows so callers
  can request the same table with different exclusion sets without
  re-hitting the source.
- `app/api/sampling.py` — `POST /sampling/tables/{table_id}`.
- `app/schemas/sampling.py` — Pydantic request/response models.
- `app/models/entities.py` — `DiscoveredSample` (JSON rows, no PII in
  metadata; the rows are sample slices with `value`/`truncated`/
  `original_length` and never include the original LOBs).
- `migrations/versions/20260723_0003_discovered_samples.py` — the new
  table, unique on `(job_id, table_id)`.
- `app/main.py` — wires the sampling router.
- 15 unit tests in `tests/test_sampling_agent.py` covering: sampler
  SQL construction (NOLOCK, TOP, CAST, LEN), cell coercion for
  strings / binary / null / int / datetime, view rejection, view opt-in,
  cache hit, cache hit with exclusion filter, 404 for unknown table,
  422 for invalid input, end-to-end API roundtrip.

### Frontend
- `frontend/src/components/SchemaViewer.tsx` — each table row gets a
  **Sample 5 rows** button (accent style) when expanded. The sample
  appears below as a separate hairline-bordered table with column
  headers, a "SAMPLE · N ROWS · CAP 512B · FRESH/CACHED" header, and a
  per-cell display that shows the `⏷<original_length>` truncation
  marker in the amber accent color.
- `frontend/src/lib/types.ts` and `frontend/src/lib/api.ts` — typed
  fetch for the new endpoint.
- `frontend/vite.config.ts` — adds `/sampling` to the proxy.

### Scripts
- `backend/scripts/validate_phase5.ps1` — superset of Phase 4: starts
  Docker MSSQL, creates `anonymizer_app`, seeds Customers / Orders /
  CustomerNotes (the last with `nvarchar(MAX)` + `varbinary(MAX)`
  columns and a 2 KB note), runs `alembic upgrade head`, boots the
  API, and exercises every endpoint. Asserts: 5-row Customers sample,
  cached second call, CustomerNotes truncation (`truncated: true`,
  `original_length: 2048`), and exclusion-set honored on cached call.
- `backend/scripts/dev_up.ps1` — boots MSSQL + API + Vite in one
  command (no teardown) for live UI work.

## Decisions

- **🛑 Large-object policy: truncate to a byte cap (default 512).**
  Bounded character/binary columns whose declared length already fits
  under the cap are read as-is; only `MAX` / `text` / `ntext` / `xml`
  / oversized columns are CAST at the SQL layer. The original length
  is also fetched (`LEN(col)`) so the response can flag
  `truncated: true` and report `original_length: <bytes>` even after
  the value has been cut down.
- **View policy:** views are off by default; `include_views=True`
  opts a single call in.
- **Isolation:** `WITH (NOLOCK)`. The plan explicitly allows it; we
  will switch to `READ_COMMITTED_SNAPSHOT` in Phase 11 when writes
  arrive.
- **Cache key:** `(job_id, table_id)`. Different exclusion sets are
  supported by filtering the cached rows at response time, so we never
  re-sample the source for a per-call parameter.

## Live validation

```
$env:DATABASE_URL = "mssql+pyodbc://sa:<SA_PASSWORD>@127.0.0.1:1433/anonymizer_app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
$env:JWT_SECRET_KEY = "<generate-a-32-byte-secret>"
$env:CREDENTIAL_ENCRYPTION_KEY = (random 32-byte b64)
.\scripts\validate_phase5.ps1
```

Output (trimmed):

```
Phase 3 endpoints...
  GET /health -> 200
  POST /discovery/test-connection -> 200 {"success":true}
  POST /discovery/databases -> 200 {"databases":["anonymizer_app","master","model","msdb","tempdb"]}
  POST /discovery/objects?object_type=TABLE -> 200 (18 tables)
Phase 4 endpoints...
  POST /schema/discover -> 200 tables=18 columns=112 fks=18 idx=37 cons=12
Phase 5 endpoints (sampling)...
  POST /sampling/tables/<Customers>  -> 200 row_count=5 sampled_columns=[Id,FullName,Email] cached=False
  POST /sampling/tables/<Customers>  -> 200 row_count=5 sampled_columns=[Id,FullName,Email] cached=True
  POST /sampling/tables/<CustomerNotes> -> 200 row_count=2
    row[0].NoteText: truncated=true, original_length=2048
    row[0].Attachment: hex=128 chars (64 bytes), truncated=false
    row[1].NoteText: "Short note about order #2.", truncated=false
  POST /sampling/tables/<Customers> (excluded_column_names=[Email]) -> cached=true, sampled_columns=[Id,FullName]
Phase 5 validation: PASS
```

### Pytest

```
$ pytest -q
..........................................
38 passed in 3.62s
```

(Ruff and Black both clean.)

## Screenshots

- `docs/screenshots/phase5_ui_01_idle.png` — initial state; wire is
  IDLE; header now reads "discovery · schema · sampling".
- `docs/screenshots/phase5_ui_02_test_connection.png` — wire goes LIVE
  after the test-connection probe.
- `docs/screenshots/phase5_ui_03_databases.png` — list-databases.
- `docs/screenshots/phase5_ui_04_schema_overview.png` — full schema
  tree (18 tables) after a fresh discovery.
- `docs/screenshots/phase5_ui_05_customers_sampled.png` — Customers
  expanded with the **Sample 5 rows** panel showing all 5 rows of
  bounded columns (no truncation markers, original lengths
  match).
- `docs/screenshots/phase5_ui_06_customer_notes_truncated.png` —
  CustomerNotes expanded; the LOB sample panel shows the 2-row
  result: row 1's `NoteText` is rendered as
  `XXXX…XXXX ⏷2048` (amber flag color) and `Attachment` is
  hex-encoded with no truncation; row 2's `NoteText` is the short
  note with no marker. The header strip reads
  `SAMPLE · 2 ROWS · CAP 512B · FRESH`.

## What I deliberately did NOT do

- **No view default-on.** Views can be unbounded materializations; the
  default is off and `include_views=True` is required per call.
- **No streaming.** Each sample is fully materialised server-side; the
  cap and bounded types already keep the worst case ≤ a few hundred KB
  per table. If a real target needs multi-megabyte rows, that is a
  Phase 11 design problem, not a Phase 5 one.
- **No writes.** No DDL, no DML anywhere. The application database
  (`discovered_samples`) is the only thing that gets `INSERT`s from
  this code path, and that insert goes through SQLAlchemy/parameterised
  SQL via the metadata engine.
- **No LLM yet.** Sampling exists to give the Phase 7 classification
  agent bounded input; the LLM does not run during sampling.
