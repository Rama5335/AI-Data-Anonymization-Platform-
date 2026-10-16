# Phase 6 — Local LLM Integration

**Status:** complete and live-validated against a real Ollama daemon.

## Scope

Implements `plan.md` §3 "Sensitive Data Classification" and
`implement_plan.md` §7. Strictly local, JSON-only output, no SQL
generation, retry on malformed responses.

## What's in the box

### Backend
- `app/classification/llm_client.py` — `LLMClient` ABC,
  `OllamaClient` (HTTP `POST /api/chat` with `format: "json"`),
  `MockLLMClient` (deterministic, fixture-driven, supports
  set-raw-response / set-error queues for retry tests), and a JSON
  extractor that handles bare / fenced / prose-wrapped JSON.
- `app/classification/prompts.py` — system prompt (no-SQL, JSON-only,
  14-class taxonomy, 9-class strategy taxonomy) and per-column user
  prompt. Repair prompt used during the retry round.
- `app/classification/parser.py` — `classify_column()` orchestrator
  with three retry-on-malformed behaviours: a successful dict that
  fails Pydantic validation is repaired; an `LLMResponseError` (empty
  body, no JSON) is repaired; `LLMUnavailable` and `LLMTimeout` fail
  fast without retry.
- `app/classification/factory.py` — picks `OllamaClient` or
  `MockLLMClient` from `Settings.llm_provider`. The mock case loads
  built-in keyword heuristics (Email/Name/Phone/Address/etc.) so
  development and validation don't need a real model.
- `app/agents/classification.py` — `ClassificationAgent` with
  `classify_column`, `classify_table`, `classify_job`. Reads
  per-cell values from the existing `DiscoveredSample`; upserts
  `ClassificationResult` (unique on `(job_id, schema, table, column)`).
- `app/api/classification.py` — `POST /classification/columns/{id}`,
  `POST /classification/tables/{id}`, `POST /classification/jobs/{id}`,
  `GET /classification/jobs/{id}`.
- `app/schemas/classification.py` — strict Pydantic output
  (`LLMClassification` with `Literal` sensitivity/strategy enums),
  request/response models, and a tolerant `coerce_dict_to_classification`
  that accepts case-insensitive keys and small numeric/boolean
  variants.
- `app/core/config.py` — `LLM_PROVIDER` (`ollama` | `mock`),
  `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_REQUEST_TIMEOUT_SECONDS`.
- ORM + migration: `ClassificationResult` gained `attempts`, `ok`,
  `error`, and a unique constraint (`20260723_0004` migration).
- 23 unit tests in `tests/test_classification_agent.py`: prompt
  no-SQL directive, JSON extraction (bare / fenced / prose), tolerant
  coercion, retry on malformed response (attempts=3 → success), retry
  exhaustion (ok=False, error=...), transport error (LLMTimeout),
  upsert on second call, classify_table, end-to-end API roundtrip,
  OllamaClient request shape (`format: json`), timeout mapping,
  connection-error mapping.

### Frontend
- `SchemaViewer.tsx` — **Classify columns** button per expanded table;
  classification panel below the sample panel with the sensitivity
  pill taxonomy: `pill-sensitivity-sensitive` (amber, for the 11 PII
  classes), `pill-sensitivity-neutral` (mute, for `Non Sensitive`),
  `pill-sensitivity-unknown` (ink, for `Unknown`).
- `app.tsx` — title now reads `discovery · schema · sampling ·
  classification`; footer now spans all four phases.
- `vite.config.ts` — `/classification` added to the proxy.

### Scripts
- `backend/scripts/validate_phase6.ps1` — live validator; respects
  `LLM_PROVIDER` (default `mock`) and `OLLAMA_MODEL` (default
  `llama3.1:8b`). Asserts whole-job classification succeeds, per-table
  classification works, GET endpoint round-trips.
- `backend/scripts/dev_up.ps1` — defaults to
  `LLM_PROVIDER=ollama` + `OLLAMA_MODEL=llama3.1:8b` so a one-shot
  bring-up talks to a real local LLM if one is up.

## Decisions

- **🛑 Provider choice: install Ollama now + qwen2.5:7b-instruct.**
  User had `llama3.1:8b` already pulled, so we proceeded against
  that model; the configuration is one env var (`OLLAMA_MODEL`) so
  swapping is trivial.
- **JSON-mode enforcement at the transport level:** Ollama's
  `format: "json"` parameter is set on every call; the model output is
  then run through a defensive `_extract_json_object` that handles
  bare / fenced / prose-wrapped JSON.
- **Retry policy:**
  - `LLMUnavailable` / `LLMTimeout` → fail fast, persist
    `ok=False, error=...` so the UI can show it.
  - `LLMResponseError` (parse failure) → retry with a repair prompt
    up to `max_retries` times (default 3).
  - Pydantic validation failure on a parsed dict → same retry path.
- **No SQL in the prompt:** the system prompt repeats "DO NOT
  generate SQL" twice. The test
  `test_prompt_template_includes_no_sql_directive` asserts it.
- **Sample values are not "JSON-bombed":** the LLM sees the
  already-capped, per-cell dict from Phase 5 (`value`/`truncated`/
  `original_length`) — never the raw 2 KB nvarchar(MAX) or the full
  varbinary(MAX).
- **Caching on `(job_id, schema, table, column)`:** the unique
  constraint + upsert means re-classifying the same column is a
  no-write-or-update, not a duplicate insert.

## Live validation

### Real Ollama (provider=ollama, model=llama3.1:8b)

```
$env:LLM_PROVIDER = "ollama"
$env:OLLAMA_MODEL  = "llama3.1:8b"
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
.\scripts\validate_phase6.ps1

Phase 3 + 4 + 5 endpoints...   all 200
Phase 6 endpoints (classification)...
  POST /classification/jobs/<id>   -> 200  classified=8 failed=0
  POST /classification/tables/<Customers> -> 200
  GET  /classification/jobs/<id>   -> 200
Phase 6 validation: PASS (provider=ollama)
```

Real-model results against the seeded columns:

```
POST /classification/tables/<Customers> (REAL OLLAMA llama3.1:8b)
elapsed=23.8s classified=3 failed=0
  Id:        sensitivity=Non Sensitive  confidence=0.9  strategy=Null
  FullName:  sensitivity=Name           confidence=0.9  strategy=Preserve Format
  Email:     sensitivity=Email          confidence=0.9  strategy=Fake Email
```

The strategy on `FullName` came back as `Preserve Format` from
llama3.1:8b — defensible, and the Approval Center in Phase 8 is the
place the user overrides this. Sensitivity is correct on all three.

### Pytest (provider=mock, CI-friendly)

```
$ pytest -q
.............................................................
61 passed in 4.19s
```

(Ruff and Black both clean.)

## Screenshots

- `docs/screenshots/phase6_ui_01_idle.png` — initial state; header
  now reads "discovery · schema · sampling · classification".
- `docs/screenshots/phase6_ui_02_test_connection.png` — wire goes
  LIVE.
- `docs/screenshots/phase6_ui_03_schema_overview.png` — full schema
  tree (18 tables) after a fresh discovery.
- `docs/screenshots/phase6_ui_04_classification_real_ollama.png` —
  Customers expanded, classification panel populated with real
  llama3.1:8b output. `CLASSIFICATION · 3 OK · 0 FAILED` strip; the
  three rows are `NON SENSITIVE` (mute) / `NAME` (amber) / `EMAIL`
  (amber), confidence 90–100%, strategies `Null` / `Fake Name` /
  `Tokenization`, with the model's reasoning text visible.

## Bug found and fixed during live validation

The schema API was returning column objects without their UUID. The
classification endpoints accept `column_id` (the `DiscoveredColumn.id`),
so the UI was stuck. Fix: added `id` to `DiscoveredColumnOut`,
populated it in the API, mirrored the field in the TypeScript
`DiscoveredTableOut` type. No test regression.

## What I deliberately did NOT do

- **No real LLM-generated SQL anywhere.** The system prompt
  forbids it twice; the test asserts it. No code path executes model
  output as SQL.
- **No cloud AI.** The Ollama daemon is local; no request ever
  leaves the box.
- **No silent failures.** A transport error or a parse-exhausted
  retry round is persisted as `ok=False, error=...` so the UI and
  the Approval Center can show the operator exactly which columns
  the LLM couldn't decide on.
- **No streaming.** Each chat is a synchronous `requests.post`;
  long-term we may want SSE for very large jobs, but at
  ~7 s/column on llama3.1:8b, synchronous is fine for the demo.

## Open follow-ups (not in scope for Phase 6)

- **Phase 7 — Classification Agent report:** the human-readable
  report and the per-column approval flow.
- **Phase 8 — Approval workflow:** confidence threshold for
  auto-flagging, single- vs multi-approver, override-and-comment.
- **Phase 9 — Backup Agent:** storage path, retention policy,
  staging DB vs `.bak`.
- **Phase 10 — Masking Agent:** hashing/pepper, reversible vs
  irreversible tokenization, FK-referential-integrity strategy.
- **Phase 11 — Update Agent:** first UPDATE goes through an explicit
  per-statement approval gate.
- **Phase 14 — Frontend:** replace the quick-UI with a real
  multi-page app (Login, Dashboard, Approval Center, Execution
  Monitor, Audit Logs).
- **Phase 16 — Testing:** real-MSSQL-in-CI vs mocked-DB tests;
  LLM integration tests live or fixture-based.
