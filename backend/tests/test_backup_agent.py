"""Tests for the Backup agent (Phase 9).

Live ``BACKUP DATABASE`` tests require the disposable Docker MSSQL
container AND a path the container can actually write to (its
filesystem is isolated from the host). The pytest unit tests cover
the safety checks and the retention logic; the live backup is
exercised by ``scripts/validate_phase9.ps1`` which writes to a path
inside the container.
"""

from pathlib import Path
import time

import pyodbc
import pytest

from app.agents.backup import BackupAgent, BackupError


def _mssql_available() -> bool:
    try:
        c = pyodbc.connect(
            "DRIVER={ODBC Driver 18 for SQL Server};"
            "SERVER=127.0.0.1,1433;UID=sa;PWD=CHANGE_ME_SA_PASSWORD;"
            "Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=2;",
            timeout=2,
        )
        c.close()
        return True
    except pyodbc.Error:
        return False


def _mssql_in_docker() -> bool:
    """True when the reachable MSSQL is the Phase 9 validation container.

    When the container is up, the host's tmp_path is NOT writable
    from inside the container, so live ``BACKUP DATABASE TO DISK =
    '<host path>'`` silently writes nowhere. The Phase 9 validator
    handles this by bind-mounting a shared backup directory into
    the container, which pytest cannot easily reproduce.
    """
    import subprocess

    out = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "ai-anonymizer-mssql-validation" in out.stdout


# Live BACKUP DATABASE pytest tests are skipped by default because
# the host/container filesystem boundary makes them unreliable from
# pytest's tmp_path. The Phase 9 validator covers the live case.
_live = pytest.mark.skipif(
    not _mssql_available() or _mssql_in_docker(),
    reason="live BACKUP DATABASE requires a non-Docker MSSQL (Docker is covered by validate_phase9.ps1)",
)


def _conn(database: str = "anonymizer_app"):
    from app.schemas.discovery import ConnectionDetails

    return ConnectionDetails(
        host="127.0.0.1",
        port=1433,
        auth_type="sql",
        username="sa",
        password="CHANGE_ME_SA_PASSWORD",
        database=database,
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=30,
    )


@_live
def test_backup_creates_file_and_verifies(tmp_path):
    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    res = agent.backup(_conn())
    assert res.verified
    assert res.size_bytes > 0
    assert Path(res.backup_path).exists()
    assert res.kept_after == 1


@_live
def test_backup_retention_keeps_last_n(tmp_path):
    agent = BackupAgent(backup_dir=tmp_path, keep=2)
    paths = []
    for i in range(4):
        time.sleep(1.05)
        res = agent.backup(_conn())
        paths.append(Path(res.backup_path))
    survivors = sorted([p.name for p in tmp_path.glob("anonymizer_app_*.bak")])
    assert len(survivors) == 2


@_live
def test_list_backups(tmp_path):
    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    agent.backup(_conn())
    assert len(agent.list_backups("anonymizer_app")) == 1


def test_backup_rejects_unsafe_database_name(tmp_path):
    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    with pytest.raises(BackupError, match="safety check"):
        agent.backup(_conn(database="master; DROP DATABASE foo"))


def test_backup_requires_sql_auth(tmp_path):
    from app.schemas.discovery import ConnectionDetails

    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    bad = ConnectionDetails(
        host="127.0.0.1",
        port=1433,
        auth_type="windows",
        username=None,
        password=None,
        database="anonymizer_app",
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=30,
    )
    with pytest.raises(BackupError, match="SQL-auth"):
        agent.backup(bad)


def test_backup_requires_database(tmp_path):
    from app.schemas.discovery import ConnectionDetails

    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    bad = ConnectionDetails(
        host="127.0.0.1",
        port=1433,
        auth_type="sql",
        username="sa",
        password="CHANGE_ME_SA_PASSWORD",
        database=None,
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=30,
    )
    with pytest.raises(BackupError, match="No database"):
        agent.backup(bad, database=None)


def test_list_backups_returns_empty_when_no_files(tmp_path):
    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    assert agent.list_backups() == []
    assert agent.list_backups("anonymizer_app") == []


def test_retention_keeps_only_n_newest(tmp_path):
    """Pure-filesystem retention test: pre-create 4 fake .bak files,
    run one backup, observe that 4 are removed to 3 (default keep)."""
    from datetime import datetime, timezone

    agent = BackupAgent(backup_dir=tmp_path, keep=3)
    # Fabricate 4 old files with different mtimes.
    for i, age_minutes in enumerate([10, 8, 6, 4]):
        p = tmp_path / f"anonymizer_app_2026010{i}T000000Z.bak"
        p.write_bytes(b"x" * 100)
        mtime = (datetime.now(timezone.utc).timestamp()) - age_minutes * 60
        import os

        os.utime(p, (mtime, mtime))
    # Now manually trigger retention (without running a real backup).
    kept = agent._enforce_retention("anonymizer_app")
    survivors = sorted([p.name for p in tmp_path.glob("anonymizer_app_*.bak")])
    assert kept == 3
    assert len(survivors) == 3
    # The newest 3 should be the 4-, 6-, 8-minute-old files.
    assert "anonymizer_app_20260103T000000Z.bak" in survivors
    assert "anonymizer_app_20260100T000000Z.bak" not in survivors
