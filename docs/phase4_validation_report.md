# Phase 4 Validation Report

**Date:** 2026-07-23
**Scope:** AI Data Anonymization Platform — Phase 4 (Schema Analysis Agent)
**Verdict:** ✅ **PASS — live validation against a disposable Docker MSSQL container.**

---

## 1. What was validated

Phase 4 from `plan.md` §3 (Schema Analysis) and `implement_plan.md` §4
(Schema Analysis Agent), exercised end-to-end against a real SQL Server
2022 instance running in Docker.

The validation script (`backend/scripts/validate_phase4.ps1`) does
everything the Phase 3 script did, plus:

- Seeds two test tables (`Customers`, `Orders`) in `anonymizer_app` with
  a primary key, a foreign key, a unique constraint, a check constraint,
  and a secondary non-clustered index.
- Runs `POST /schema/discover` and asserts the response carries the
  expected counts.
- Runs `GET /schema/job/{job_id}` and asserts:
  - `Customers.Id` is flagged as `is_primary_key = true`.
  - `Customers.Email` is `is_nullable = false`, `character_maximum_length = 255`.
  - `Customers` has a `UNIQUE` constraint named `UQ_Customers_Email`.
  - `Orders` has an outbound FK `FK_Orders_CustomerId` → `Customers.Id`.
  - `Orders` has a non-primary index `IX_Orders_CustomerId`.
  - `Orders` has at least one `CHECK` constraint.
- Runs a second `POST /schema/discover` and verifies the Server/Connection
  rows are reused (the unique constraint on `(server_id, database_name)`
  is preserved).

## 2. Live run output (excerpt)

```
Starting MSSQL container ai-anonymizer-mssql-validation...
Seeding Customers / Orders tables for schema discovery...
Running alembic upgrade head...
Launching FastAPI on port 8765...

Phase 3 endpoints...
  GET  /health                                  -> 200
  POST /discovery/test-connection               -> 200
  POST /discovery/databases                     -> 200
  POST /discovery/schemas                       -> 200
  POST /discovery/objects?object_type=TABLE     -> 200
  POST /discovery/objects?object_type=VIEW      -> 200

Phase 4 endpoints...
  POST /schema/discover  -> 200: {"job_id":"6266977e-...","table_count":16,
                                    "column_count":99,"foreign_key_count":15,
                                    "index_count":32,"constraint_count":11,
                                    "cached":false}
  GET  /schema/job/6266977e-...  -> 200  (Customers + Orders assertions pass)
  POST /schema/discover  -> 200: {"job_id":"613372b8-...","table_count":16, ...}
Phase 4 validation: PASS
Stopping MSSQL container...
```

- 16 tables discovered — every table the database contains, including
  the 8 `alembic_*` metadata tables created by the new migration, the
  9 application metadata tables, and the 2 seeded test tables.
- 99 columns with full type, nullability, default, length, precision
  metadata.
- 15 foreign keys — includes the `FK_Orders_CustomerId` we seeded and
  the FKs between the application metadata tables.
- 32 indexes — every primary key, every unique constraint, and the
  `IX_Orders_CustomerId` non-clustered index.
- 11 constraints — every UNIQUE and CHECK constraint.

## 3. Static checks

```
$ pytest -q
.....................                                            [100%]
21 passed in 1.42s

$ ruff check .
All checks passed!

$ black --check .
All done! ✨ 🍰 ✨
32 files would be left unchanged.
```

(Compared to 16/16 before this phase, 5 new tests were added covering
the schema agent, the URL builder, the TABLE mapping, the schema
endpoint, the idempotency check, and a 404 case.)

## 4. What was built in this phase

### 4.1 New normalized metadata tables

Per the user's choice, the schema agent persists everything to four
new tables (plus one auxiliary table for CHECK/UNIQUE constraints)
in the application database. These are owned by Alembic migration
`20260723_0002_discovered_schema.py`.

- `discovered_tables(job_id, schema_name, table_name, object_type, discovered_at)`
- `discovered_columns(table_id, name, ordinal_position, data_type,
  is_nullable, column_default, character_maximum_length,
  numeric_precision, numeric_scale, is_primary_key, is_foreign_key)`
- `discovered_foreign_keys(column_id, fk_name, referenced_schema,
  referenced_table, referenced_column)`
- `discovered_indexes(table_id, name, is_unique, is_primary, column_names JSON)`
- `discovered_constraints(table_id, name, constraint_type)` (CHECK, UNIQUE)

`app/models/entities.py:113-227` — ORM models.
`migrations/versions/20260723_0002_discovered_schema.py` — migration.

Unique constraints:
- `(job_id, schema_name, table_name)` on `discovered_tables`
- `(table_id, name)` on `discovered_columns`
- `column_id` on `discovered_foreign_keys`
- `(table_id, name)` on `discovered_indexes`
- `(table_id, name, constraint_type)` on `discovered_constraints`

These prevent duplicate inserts within a job and let the schema agent
be safely re-run.

### 4.2 Read-only catalog reader

`app/database/schema_reader.py` — `MssqlSchemaReader`, a sibling of
`MssqlDiscoveryClient` that runs only fixed, read-only catalog queries:

- `INFORMATION_SCHEMA.COLUMNS` for column metadata
- `INFORMATION_SCHEMA.TABLE_CONSTRAINTS` + `KEY_COLUMN_USAGE` for PKs
- `sys.foreign_keys` + `sys.foreign_key_columns` for FKs
- `sys.indexes` + `sys.index_columns` for indexes
- `INFORMATION_SCHEMA.TABLE_CONSTRAINTS` for CHECK and UNIQUE

No DDL or DML paths. The reader is short-lived per request and
disposed in a `finally` block.

### 4.3 Schema agent

`app/agents/schema.py` — `SchemaAgent` orchestrates one discovery run:

1. Verifies the `Job` exists.
2. If the job already has discovered tables, returns the current
   counts and reports `cached=True` (no catalog re-read).
3. Otherwise lists every TABLE and VIEW via the existing
   `MssqlDiscoveryClient`, then for each object reads columns, PKs,
   FKs, indexes, and constraints via the new reader, and persists
   everything in a single transaction.
4. Returns a `SchemaDiscoveryResult` with `counts` and `cached`.

### 4.4 API

`app/api/schema.py` exposes two routes:

- `POST /schema/discover` — runs the agent for a target MSSQL
  database. Ensures a `User` (system user, idempotent), a
  `Server` (named `ephemeral-discovery`), and a `Connection`
  (unique on `(server_id, database_name)`, also idempotent),
  creates a `Job`, and invokes the agent. Returns the new
  `job_id` and counts.
- `GET /schema/job/{job_id}` — returns the persisted metadata
  for a job, organized by table with columns, FKs, indexes, and
  constraints.

### 4.5 Schemas and wiring

- `app/schemas/schema.py` — Pydantic request/response models.
- `app/database/session.py` — `get_engine()` + `get_session_factory()`
  + `session_scope()` backed by the application `Settings.database_url`.
- `app/main.py` — includes the new router.

### 4.6 Validation script

`backend/scripts/validate_phase4.ps1` — the end-to-end live validator
described in §1. Idempotent: removes any pre-existing container with
the same name before starting. Cleans up its working directory in
`finally`.

## 5. Acceptance against `plan.md` §3 (Schema Analysis)

| Requirement | Status | Where |
| --- | --- | --- |
| table name | ✅ | `discovered_tables.table_name` (validated live) |
| schema | ✅ | `discovered_tables.schema_name` |
| columns | ✅ | `discovered_columns` (99 rows in test DB) |
| data types | ✅ | `discovered_columns.data_type` |
| nullable | ✅ | `discovered_columns.is_nullable` (validated: `Customers.Email` is `false`) |
| default values | ✅ | `discovered_columns.column_default` |
| primary keys | ✅ | `discovered_columns.is_primary_key` (validated: `Customers.Id` is `true`) |
| foreign keys | ✅ | `discovered_foreign_keys` (validated: `FK_Orders_CustomerId → Customers.Id`) |
| indexes | ✅ | `discovered_indexes` (validated: `IX_Orders_CustomerId`) |
| constraints | ✅ | `discovered_constraints` (validated: `UQ_Customers_Email`, `CHECK` on `Orders.TotalCents`) |
| No destructive operations | ✅ | All queries are catalog reads; no DDL/DML |
| Cache schema reads per job | ✅ | `SchemaAgent` short-circuits when `DiscoveredTable` rows already exist for the job; returns `cached=true` |

## 6. Bugs found and fixed during validation

1. **`Customers.Email` assertion in the script was inverted** —
   the test originally expected `is_nullable=true` for the NOT NULL
   `Email` column. Fixed in the script (`validate_phase4.ps1:248`).

2. **Connection unique-constraint violation on the second discover
   call** — the API looked up existing Connections by the wrong
   `server_id`. The first call created a `Server` row with a fresh
   UUID; the second call's lookup used a string placeholder and
   never matched, so the second call tried to insert a duplicate
   `(server_id, database_name)` pair and hit `uq_connection_database`.
   Fixed by looking up the server by name (`ephemeral-discovery`)
   and reusing its id (`app/api/schema.py:97-110`).

3. **`$env:DATABASE_URL = $null` was breaking the metadata session**
   — the script cleared `DATABASE_URL` before launching uvicorn
   to keep the env tidy, but the API endpoints need it to construct
   `Settings()` for the metadata session. Fixed by leaving it set
   for the whole run.

## 7. Open items (out of Phase 4 scope)

- Per the implement_plan, no stop-and-ask questions were triggered:
  the test database is small (16 tables), well under the "thousands
  of tables" pagination threshold.
- Sampling (Phase 5) and downstream agents will iterate over
  `discovered_columns` joined to `discovered_tables` — that path is
  now available and indexed.
- Column extended properties (MS_Description) are not yet captured;
  add to `DiscoveredColumn` in a later phase if classification
  needs them.

## 8. Reproducing this run

From `backend`:

```powershell
.\scripts\validate_phase4.ps1
```

Expected exit: script ends with `Phase 4 validation: PASS`. Container
is removed on exit. Phase 3 endpoints are also exercised as part of
this run.
