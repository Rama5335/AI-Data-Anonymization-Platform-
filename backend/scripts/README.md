# Phase 3 live validation scripts.

These scripts spin up a disposable MSSQL Server in Docker, point the
backend at it, run Alembic migrations, and exercise the read-only
discovery endpoints end-to-end. They do **not** touch any non-disposable
database and are safe to run repeatedly.

> Requires Docker Desktop (or any Docker engine) on the host. The MSSQL
> image and ODBC driver stay local; no cloud resources are contacted.

Run from the `backend/` directory:

```powershell
.\scripts\validate_phase3.ps1
```
