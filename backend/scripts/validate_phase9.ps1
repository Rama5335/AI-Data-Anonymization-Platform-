# Phase 9 live validation: run a real BACKUP DATABASE + RESTORE VERIFYONLY
# against the disposable Docker MSSQL, with a bind-mounted shared backup dir.
#
# Usage (from backend/):
#   .\scripts\validate_phase9.ps1
#
# Container layout: /var/opt/mssql/backups is bind-mounted to the host's
# ./backups directory. MSSQL writes the .bak there, the API and the host
# can read it.

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $repoRoot ".cpython311-venv\Scripts\python.exe"
$backupDirHost = Join-Path $repoRoot "backups"
$backupDirContainer = "/var/opt/mssql/backups"

if (-not (Test-Path $venvPy)) {
    Write-Error "Python venv not found at $venvPy"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not on PATH. Install Docker Desktop and re-run."
}

$saPassword = $env:MSSQL_SA_PASSWORD
if (-not $saPassword) { throw 'MSSQL_SA_PASSWORD is not set. Export it before running this script.' }
$mssqlPort = 1433
$appPort = 8765
$containerName = "ai-anonymizer-mssql-validation"
$image = "mcr.microsoft.com/mssql/server:2022-latest"

New-Item -ItemType Directory -Force -Path $backupDirHost | Out-Null

try {
    Write-Host "Starting MSSQL container $containerName (with $backupDirContainer mounted)..."
    & docker rm -f $containerName 2>&1 | Out-Null
    & docker run -d --name $containerName -e "ACCEPT_EULA=Y" `
        -e "MSSQL_SA_PASSWORD=$saPassword" `
        -p "${mssqlPort}:1433" `
        -v "${backupDirHost}:${backupDirContainer}" `
        $image | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker run failed" }

    Write-Host "Waiting for MSSQL..." -NoNewline
    $waitScript = @"
import sys, time, pyodbc
for _ in range(60):
    try:
        c = pyodbc.connect(
            "DRIVER={ODBC Driver 18 for SQL Server};"
            "SERVER=127.0.0.1,$mssqlPort;UID=sa;PWD=$saPassword;"
            "Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=5;"
        )
        c.close()
        sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
"@
    $waitPath = Join-Path $repoRoot ".validation-work\wait.py"
    New-Item -ItemType Directory -Force -Path (Split-Path $waitPath) -ErrorAction SilentlyContinue | Out-Null
    [System.IO.File]::WriteAllText($waitPath, $waitScript, [System.Text.UTF8Encoding]::new($false))
    & $venvPy $waitPath
    if ($LASTEXITCODE -ne 0) { throw "MSSQL did not start" }
    Write-Host " ready."

    Write-Host "Running live BACKUP DATABASE via Python agent..."
    $env:DATABASE_URL = "mssql+pyodbc://sa:$saPassword@127.0.0.1:$mssqlPort/master?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
    $env:PYTHONPATH = $repoRoot
    $env:BACKUP_DIR = $backupDirContainer  # Path the *container* can write
    Push-Location $repoRoot
    & $venvPy -c @"
import os
os.environ['BACKUP_DIR'] = r'$backupDirContainer'
from app.agents.backup import BackupAgent
from app.schemas.discovery import ConnectionDetails
agent = BackupAgent(backup_dir=r'$backupDirContainer', keep=2)
conn = ConnectionDetails(
    host='127.0.0.1', port=$mssqlPort, auth_type='sql',
    username='sa', password='$saPassword', database='master',
    driver='ODBC Driver 18 for SQL Server',
    encrypt=True, trust_server_certificate=True, timeout_seconds=30,
)
res = agent.backup(conn, database='master')
print('PASS  database=', res.database)
print('      path=', res.backup_path)
print('      bytes=', res.size_bytes)
print('      verified=', res.verified)
print('      kept_after=', res.kept_after)
"@
    if ($LASTEXITCODE -ne 0) { throw "BACKUP DATABASE failed" }
    Pop-Location

    $bak = Get-ChildItem -Path $backupDirHost -Filter "master_*.bak" | Select-Object -First 1
    if (-not $bak) { throw "No .bak file appeared on the host" }
    Write-Host "PASS  .bak visible on host at $($bak.FullName) ($($bak.Length) bytes)"

    Write-Host "Phase 9 validation: PASS"
} finally {
    Write-Host "Stopping MSSQL container..."
    & docker rm -f $containerName 2>&1 | Out-Null
}
