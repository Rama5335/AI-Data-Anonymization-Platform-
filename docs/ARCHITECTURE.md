# Architecture

The platform runs as two services: a FastAPI backend and a Vite +
React frontend, plus an Ollama daemon for local inference. The
target MSSQL is reached via short-lived pyodbc connections that
live for one HTTP request and are then disposed.

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────┐
│  Frontend (Vite) │ -> │  Backend (FastAPI)│ -> │  Target MSSQL │
│  5174            │    │  8765             │    │  (user's DB)  │
└──────────────────┘    └──────────────────┘    └──────────────┘
                              │
                              v
                       ┌──────────────────┐
                       │  Ollama (LLM)    │
                       │  11434            │
                       └──────────────────┘
                              ^
                              |
                       ┌──────────────────┐
                       │  Application DB   │
                       │  (anonymizer_app) │
                       │  Same instance.   │
                       └──────────────────┘
```

The **application database** is the same MSSQL instance that the
target database lives on (per `plan.md` §1). It stores
`Server`/`Connection`/`Job`/`ClassificationResult`/`Approval`/
`AuditLog`/`Discovered*`/`MaskingJob`/`TokenVault`. Application
data never leaves the box; the LLM only ever sees sample rows that
were already capped at 512 bytes per cell in Phase 5. The full
metadata schema — every column, key, constraint, index and the ER
diagram — is documented in [`DATABASE.md`](DATABASE.md).

## Agent pipeline (Phase 13)

The workflow orchestrator is a small state machine over the
named phases in `plan.md` §8:

```
PENDING -> DISCOVERY -> SCHEMA -> SAMPLE -> CLASSIFY
       -> WAITING_FOR_APPROVAL  (pause: human reviews each column)
       -> READY_FOR_BACKUP -> BACKUP -> MASK -> UPDATE -> AUDIT
       -> COMPLETED | FAILED
```

`WAITING_FOR_APPROVAL` is the only pause. Every other transition
is automatic.

The orchestrator persists the current phase in
`Job.workflow_state`; a crash mid-workflow recovers from where it
left off. A job is flagged **stale** if it has been in
`WAITING_FOR_APPROVAL` longer than
`WORKFLOW_APPROVAL_TIMEOUT_HOURS` (default 7 days).

## Sensitive-data flow (Phases 5–11)

1. **Sampling** (`app/database/sampling.py`) — `SELECT TOP N ... WITH
   (NOLOCK)` with per-column CASTs for any `MAX`/LOB type. The
   `original_length` is fetched via `LEN(col) AS col__orig` so the
   API can flag `truncated: true` even after the value has been
   shortened.
2. **Classification** (`app/classification/llm_client.py`,
   `app/agents/classification.py`) — Ollama with
   `format: "json"`, JSON-mode enforced at the transport level;
   the parser retries on `LLMResponseError` and Pydantic validation
   failures (up to `max_retries`).
3. **Approval** (`app/agents/approval.py`) — single-approver;
   every classification row has zero or one approval; the queue is
   the natural input to the masking engine.
4. **Masking** (`app/agents/masking.py`,
   `app/agents/masking_orchestrator.py`) — 9 strategies, per-job
   salt (for `Hash`) + token key (for `Tokenization`); the
   orchestrator auto-propagates a PK's strategy to every FK that
   references it, so referential integrity is preserved within a
   run.
5. **Backup** (`app/agents/backup.py`) — `BACKUP DATABASE` with
   `RESTORE VERIFYONLY`; FIFO retention; raises if backup OR
   verify fails (per `implement_plan.md` §10).
6. **Update** (`app/agents/safe_update.py`) — explicit
   transactions, parameterized SQL, batched in
   `settings.safe_update_batch_size`, with a `confirm: true` +
   `expected_backup_path` gate.
7. **Audit** (`app/agents/audit.py`) — append-only; the application
   never UPDATEs or DELETEs an `AuditLog` row.

## Security boundaries

* The LLM **never** receives the full original row. Sample values
  are capped at 512 bytes per cell; binary values are hex-encoded;
  the prompt includes `truncated: true` and `original_length` so the
  model knows it is seeing a head.
* The masking engine **never** echoes LLM output. The LLM only
  selects a strategy; the backend performs the transformation
  deterministically.
* The safe-update agent **never** builds SQL from LLM output. The
  query is built from `MaskingContext` (schema-derived); parameters
  are bound positionally.
* The backup agent **never** writes to a path the operator didn't
  pass in `expected_backup_path`, and the path is safety-checked
  against a regex of safe characters.
* Credentials live only for the duration of one HTTP request.
  The application does not persist DB passwords to disk.

## Tech stack

* **Backend:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic,
  pyodbc, pydantic, loguru, cryptography, faker, requests.
* **LLM:** Ollama (`qwen2.5:7b-instruct` default; the unit tests
  use the `mock` provider so they don't need a model).
* **Frontend:** Vite 5, React 18, TypeScript, Tailwind 3. No
  external UI library; the design is a paper/ink/IBM Plex stack
  with a single teal accent.
* **MSSQL:** 2022 (compatible with 2019+).
* **CI:** GitHub Actions (`tests.yml` for unit, `integration.yml`
  for weekly live integration).
