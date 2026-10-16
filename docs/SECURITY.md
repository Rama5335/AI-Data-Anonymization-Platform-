# Security model

This document describes the controls that protect the target database
and the platform's own metadata. It complements `docs/ARCHITECTURE.md`
(how the pieces fit) and `docs/RUNBOOK.md` (how to operate them).

The overriding invariant:

> **No target-data modification without (1) a valid authorization, (2)
> an explicit approval, and (3) a successfully verified backup. Every
> relevant operation is audited, and no secret is ever stored or
> returned.**

## 1. Authentication

* Passwords are hashed with Argon2 (`pwdlib[argon2]`); the plaintext
  password is never stored, logged, returned, or audited.
* A successful `POST /auth/login` returns a signed JWT (HS256 by
  default, `JWT_SECRET_KEY`) carrying only the user id as `sub`.
* `JWT_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` are read from the
  environment; there are no defaults, so the app refuses to start
  without them. Both are `SecretStr` and are never rendered in logs.
* Token lifetime is bounded by `JWT_ACCESS_TOKEN_MINUTES` (default 30).

### Bootstrap

If `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` are set,
the first login that touches the user table ensures that account exists
as an active `admin`. If the account already exists but the configured
password does not match the stored hash, the hash is updated so an
operator can recover from a mistyped first-run password without deleting
the user. Existing users are never deleted or deactivated and their role
is never changed. Remove both variables after the first successful
login; while they remain set they are the source of truth for this
account's password.

## 2. Authorization

Two roles exist: `user` and `admin`. `require_role("admin")` also
accepts a role value of `"admin"`; a plain `user` is rejected with
**403**.

| Endpoint | Minimum role |
| --- | --- |
| `POST /auth/login`, `GET /health` | none (public) |
| `POST /users` | admin |
| `GET /users/me` | authenticated |
| `POST /discovery/*`, `POST /schema/*` | authenticated |
| `POST /sampling/*`, `POST /classification/*`, `GET /classification/jobs/*` | authenticated |
| `POST /approvals`, `GET /approvals/jobs/*` | authenticated |
| `POST /masking/*`, `POST /updates/plan` | authenticated |
| `GET/POST /workflow/*`, `GET /settings` | authenticated |
| `GET/POST /servers`, `GET /servers/{id}`, `POST /servers/{id}/probe` | authenticated |
| `POST/PATCH/DELETE` server management | admin |
| `POST /backups`, `GET /backups` | admin |
| `POST /updates/apply` | admin |
| `POST /updates/rollback` | admin |
| `GET /audit`, `GET /audit/jobs/{id}` | admin |

The **approval model is single-approver** (any active user may approve;
`approved_by_user_id` is recorded). This is a documented existing
decision (`docs/PHASES.md`), kept as-is; the hardening work did not
change it, so approval endpoints require authentication only.

### Job attribution

`POST /schema/discover` accepts an optional `requested_by_user_id`. It
is honored only when it equals the authenticated caller or when the
caller is an admin. Any other value is rejected with **403**, so a job
cannot be attributed to another user.

## 3. Backup-before-update (the hard gate)

A real (non-dry-run) `POST /updates/apply` is refused unless **all** of
the following hold:

1. `confirm: true` — the operator explicitly approved.
2. `expected_backup_path` is non-empty, resolves to a path **inside**
   `BACKUP_DIR`, and the file exists on disk.
3. A persisted `BackupRecord` exists for that exact path with:
   * `verified = true` (i.e. `RESTORE VERIFYONLY` succeeded), and
   * `database_name` equal to the target database, and
   * `job_id` either `NULL` (it is then bound to the executing job) or
     equal to the executing job.

If any condition fails, the update aborts **before the target is
touched** and an audit row `safe_update.backup_gate` (outcome
`failure`) is written with the reason.

`BackupAgent` persists a `BackupRecord` for **every** attempt — success
(`verified=true`) and failure (`verified=false` with the stage:
`connect` / `backup` / `verify`). A failed or unverified backup can
never authorize an update.

Dry-run (`dry_run: true`) skips the gate because it does not write to
the target; it is the operator's preview step.

## 4. Execution safety (Safe Update)

* `confirm` gate + backup gate as above.
* SQL is **never** built from data values: the update uses bound
  parameters (`SET [col] = ?`), and every identifier passes a
  `[A-Za-z0-9_]` safety check before being bracket-quoted.
* Only columns that have an `approve`/`override` approval and are
  neither rejected nor excluded enter the plan; there is no code path
  that updates an unapproved column.
* Updates run inside an explicit transaction. On any driver error,
  post-execution validation failure, or unexpected exception, the
  agent **rolls back** and audits `safe_update.rollback` +
  `safe_update.error`.
* **Post-execution validation:** after each batch the agent re-reads
  the affected rows and confirms each now holds the expected masked
  value. A row that still holds its original value (and differs from
  the expected masked value) fails validation → rollback. Values that
  merely differ due to server-side type coercion are accepted, so
  validation does not produce false failures.
* On success the masking job's salt/token key is wiped, making
  tokenization irreversible.

### Rollback (restore)

`POST /updates/rollback` **requires admin** and refuses to touch the
target unless the requested backup exists under `BACKUP_DIR` and has a
*verified* `BackupRecord` for the requested database. Every attempt is
audited (`rollback.start`, `rollback.complete`, `rollback.failed`) with
the caller's identity. On a restore failure the agent makes a
best-effort attempt to return the database to `MULTI_USER` so it is not
left stuck in `SINGLE_USER`.

## 5. Audit trail

* `AuditLog` is **append-only by contract**: `AuditAgent` exposes only
  `write`, `for_job`, `all` — there is no update or delete path, and
  the API exposes no mutation route for audit rows.
* Reading audit records is **admin-only**; `limit` is bounded
  (`1..1000`).
* `AuditAgent.write` sanitizes every `details` payload centrally using
  `app/security/redaction.py`:
  * keys matching password/secret/token/api-key/credential/connection
    string patterns are replaced with `[REDACTED]`;
  * free-text values have embedded secrets (`PWD=...`, `Bearer ...`,
    `sk-...`, JWTs) redacted and are truncated to 500 characters.
* Audited operations include: `auth.login` (success/failure, no
  password), `auth.user_create`, `backup.complete`, `backup.failed`,
  `safe_update.plan`, `safe_update.start`, `safe_update.complete`,
  `safe_update.rollback`, `safe_update.error`, `safe_update.backup_gate`,
  and the `rollback.*` events.

## 6. Secret handling

* `CREDENTIAL_ENCRYPTION_KEY` (32-byte urlsafe-base64) encrypts stored
  connection strings. Per-request credentials for discovery/backup/
  update are used only for the lifetime of the HTTP call and are never
  persisted.
* Driver/exception text returned to clients is passed through
  `redact_text` so a connection string fragment or password cannot
  appear in an HTTP response.
* Secrets are never logged. CI does not receive production secrets; the
  unit suite generates throwaway keys and uses a temporary SQLite
  metadata DB.

## 7. No cloud AI / no generated SQL

* The default and supported AI path is local (Ollama). No target data
  leaves the network.
* The LLM never produces SQL: classification prompts forbid it, and the
  update SQL is built and parameterized by the application itself.

## 8. Known limitations

* **Single-approver** model (no separation of duties); documented
  existing decision, unchanged here.
* **No rate limiting / lockout** on `/auth/login` yet — only audit
  logging of failures.
* **SQLite metadata DB** is used for local dev/tests; production uses
  MSSQL. SQLite's driver ignores some column types but the ORM +
  Alembic migrations are authoritative.
* **Backup dir visibility:** the gate requires the `.bak` file to be
  visible from the application process. When MSSQL runs in Docker with
  a container-only backup directory, mount the same volume into the API
  container so verification can see the file.
* **Verification is `RESTORE VERIFYONLY`** (integrity of the backup
  media), not a test-restore. A passing verify does not, by itself,
  prove a restore will succeed against a live database.
* Boolean/integer columns are stored differently across SQLite and
  MSSQL; the safety logic relies on Python truthiness where relevant.
* `docker/docker-compose.yml` contains hardcoded **development**
  credentials. They are acceptable for the local disposable stack but
  must be replaced for any shared deployment (see `docker/.env.prod.example`).

## 9. Running the security tests

```bash
cd backend
pytest -q                 # full suite (includes the security tests below)
pytest tests/test_authz.py tests/test_backup_gate.py \
       tests/test_rollback.py tests/test_audit_api.py \
       tests/test_anonymization_correctness.py -q
```

These cover: forged/malformed/unknown/expired tokens, inactive users,
role enforcement on every admin route, unauthenticated reads,
attribution spoofing, the five backup-gate rejection reasons plus the
happy path, backup metadata persistence on success/failure, rollback
pre-flight refusals and restore auditing, audit sanitization and
append-only guarantees, and anonymization correctness (excluded/rejected/
unapproved columns never enter the plan; validation failure rolls back).
