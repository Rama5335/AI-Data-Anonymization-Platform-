# Phase 4 live validation for a disposable Docker MSSQL container.
#
# Usage (from backend/):
#   .\scripts\validate_phase4.ps1
#
# What it does (everything Phase 3 did, plus Phase 4):
#   1. Starts mcr.microsoft.com/mssql/server:2022-latest on 127.0.0.1:1433.
#   2. Waits for SQL Server to accept logins.
#   3. Creates the anonymizer_app database.
#   4. Runs `alembic upgrade head` (creates the new discovered_* tables).
#   5. Seeds two test tables (Customers, Orders) with PK, FK, unique
#      constraint, and a secondary index in the target database.
#   6. Boots the FastAPI app and hits /health, every /discovery/* route,
#      and the two new /schema/* routes.
#   7. Tears the container down on exit.
#
# Configuration (override via env vars before invocation):
#   MSSQL_SA_PASSWORD  - SA password (REQUIRED: export before running)
#   MSSQL_IMAGE        - Docker image (default: mcr.microsoft.com/mssql/server:2022-latest)
#   MSSQL_PORT         - Host port (default: 1433)
#   APP_PORT           - Local FastAPI port (default: 8765)

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $repoRoot ".cpython311-venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Error "Python venv not found at $venvPy"
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not on PATH. Install Docker Desktop and re-run."
}

$saPassword = $env:MSSQL_SA_PASSWORD
if (-not $saPassword) { throw 'MSSQL_SA_PASSWORD is not set. Export it before running this script.' }
$image      = if ($env:MSSQL_IMAGE)       { $env:MSSQL_IMAGE }       else { "mcr.microsoft.com/mssql/server:2022-latest" }
$mssqlPort  = if ($env:MSSQL_PORT)        { [int]$env:MSSQL_PORT }   else { 1433 }
$appPort    = if ($env:APP_PORT)          { [int]$env:APP_PORT }     else { 8765 }

$containerName = "ai-anonymizer-mssql-validation"
$workDir = Join-Path $repoRoot ".validation-work"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

function Wait-ForMssql {
    param([int]$Port, [string]$Password)
    Write-Host "Waiting for MSSQL on 127.0.0.1:$Port to accept logins..." -NoNewline
    $scriptPath = Join-Path $workDir "wait_mssql.py"
    $scriptBody = @"
import sys
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$Port;UID=sa;PWD=$Password;Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=5;'
try:
    c = pyodbc.connect(conn_str, timeout=5)
    c.close()
    sys.exit(0)
except Exception as exc:
    print(exc)
    sys.exit(1)
"@
    [System.IO.File]::WriteAllText($scriptPath, $scriptBody, [System.Text.UTF8Encoding]::new($false))
    for ($i = 0; $i -lt 60; $i++) {
        $tcp = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($tcp) {
            & $venvPy $scriptPath 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Host " ready."
                return
            }
        }
        Start-Sleep -Seconds 2
        Write-Host "." -NoNewline
    }
    Write-Host " timeout."
    throw "MSSQL did not become ready in time."
}

function Ensure-AppDatabase {
    param([int]$Port, [string]$Password)
    $scriptPath = Join-Path $workDir "create_app_db.py"
    $scriptBody = @"
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$Port;DATABASE=master;UID=sa;PWD=$Password;Encrypt=yes;TrustServerCertificate=yes;'
master = pyodbc.connect(conn_str, autocommit=True)
cur = master.cursor()
cur.execute("IF DB_ID('anonymizer_app') IS NULL CREATE DATABASE anonymizer_app")
cur.close()
master.close()
print('anonymizer_app ready')
"@
    [System.IO.File]::WriteAllText($scriptPath, $scriptBody, [System.Text.UTF8Encoding]::new($false))
    & $venvPy $scriptPath
    if ($LASTEXITCODE -ne 0) { throw "create_app_db failed" }
}

function Seed-SchemaFixtures {
    param([int]$Port, [string]$Password)
    Write-Host "Seeding Customers / Orders tables for schema discovery..."
    $scriptPath = Join-Path $workDir "seed_schema.py"
    $scriptBody = @"
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$Port;DATABASE=anonymizer_app;UID=sa;PWD=$Password;Encrypt=yes;TrustServerCertificate=yes;'
c = pyodbc.connect(conn_str, autocommit=True)
cur = c.cursor()
# Idempotent: drop and recreate.
cur.execute("IF OBJECT_ID('dbo.Orders', 'U') IS NOT NULL DROP TABLE dbo.Orders")
cur.execute("IF OBJECT_ID('dbo.Customers', 'U') IS NOT NULL DROP TABLE dbo.Customers")
cur.execute('''
CREATE TABLE dbo.Customers (
    Id          INT IDENTITY(1,1) NOT NULL,
    FullName    NVARCHAR(200)     NOT NULL,
    Email       NVARCHAR(255)     NOT NULL,
    CONSTRAINT PK_Customers PRIMARY KEY (Id),
    CONSTRAINT UQ_Customers_Email UNIQUE (Email)
)
''')
cur.execute('''
CREATE TABLE dbo.Orders (
    Id          INT IDENTITY(1,1) NOT NULL,
    CustomerId  INT               NOT NULL,
    TotalCents  DECIMAL(18,2)     NOT NULL,
    CONSTRAINT PK_Orders PRIMARY KEY (Id),
    CONSTRAINT FK_Orders_CustomerId FOREIGN KEY (CustomerId) REFERENCES dbo.Customers(Id),
    CONSTRAINT CK_Orders_TotalPositive CHECK (TotalCents >= 0)
)
''')
cur.execute("CREATE INDEX IX_Orders_CustomerId ON dbo.Orders (CustomerId)")
cur.close()
c.close()
print('seed ready')
"@
    [System.IO.File]::WriteAllText($scriptPath, $scriptBody, [System.Text.UTF8Encoding]::new($false))
    & $venvPy $scriptPath
    if ($LASTEXITCODE -ne 0) { throw "seed_schema failed" }
}

function New-ConnectionHash {
    param([string]$Database = "master")
    @{
        host                     = "127.0.0.1"
        port                     = $mssqlPort
        auth_type                = "sql"
        username                 = "sa"
        password                 = $saPassword
        database                 = $Database
        driver                   = "ODBC Driver 18 for SQL Server"
        encrypt                  = $true
        trust_server_certificate = $true
        timeout_seconds          = 10
    }
}

function New-ConnectionJson {
    param([string]$Database = "master")
    (New-ConnectionHash -Database $Database) | ConvertTo-Json -Compress
}

function New-DiscoverBodyJson {
    param([string]$Database = "master")
    @{ connection = (New-ConnectionHash -Database $Database) } | ConvertTo-Json -Compress
}

$api = $null
try {
    Write-Host "Starting MSSQL container $containerName..."
    & docker rm -f $containerName 2>&1 | Out-Null
    $runOut = & docker run -d --name $containerName -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=$saPassword" -p "${mssqlPort}:1433" $image 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "docker run output: $runOut"
        throw "docker run failed (exit $LASTEXITCODE)"
    }

    Write-Host "Waiting for MSSQL..."
    Wait-ForMssql -Port $mssqlPort -Password $saPassword

    Write-Host "Creating anonymizer_app database if missing..."
    Ensure-AppDatabase -Port $mssqlPort -Password $saPassword

    Seed-SchemaFixtures -Port $mssqlPort -Password $saPassword

    $env:DATABASE_URL = "mssql+pyodbc://sa:$saPassword@127.0.0.1:$mssqlPort/anonymizer_app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
    $env:APP_ENV = "test"
$env:JWT_SECRET_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))
    $env:CREDENTIAL_ENCRYPTION_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))

    Write-Host "Running alembic upgrade head..."
    Push-Location $repoRoot
    try {
        & $venvPy -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic failed" }
    } finally {
        Pop-Location
    }

    Write-Host "Launching FastAPI on port $appPort..."
    $api = Start-Process -FilePath $venvPy -ArgumentList @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$appPort"
    ) -PassThru -NoNewWindow -RedirectStandardOutput "uvicorn.out.log" -RedirectStandardError "uvicorn.err.log" `
        -WorkingDirectory $repoRoot
    try {
        for ($i = 0; $i -lt 30; $i++) {
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$appPort/health" -UseBasicParsing -TimeoutSec 2
                if ($r.StatusCode -eq 200) { break }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        if ($i -ge 30) { throw "API did not start in time" }

        function Probe {
            param([string]$Path, [string]$Method = "POST", [string]$Body = $null, [string]$Query = $null)
            $url = "http://127.0.0.1:$appPort$Path"
            if ($Query) { $url += "?$Query" }
            $params = @{
                Uri = $url
                Method = $Method
                ContentType = "application/json"
                UseBasicParsing = $true
            }
            if ($Body) { $params.Body = $Body }
            $resp = Invoke-WebRequest @params
            $snippet = $resp.Content
            if ($snippet.Length -gt 220) { $snippet = $snippet.Substring(0, 220) + "..." }
            Write-Host ("  {0} {1} -> {2}: {3}" -f $Method, $Path, $resp.StatusCode, $snippet)
            return $resp.Content
        }

        Write-Host "Phase 3 endpoints..."
        Probe -Path "/health" -Method GET
        Probe -Path "/discovery/test-connection" -Body (New-ConnectionJson)
        Probe -Path "/discovery/databases"        -Body (New-ConnectionJson)
        Probe -Path "/discovery/schemas"          -Body (New-ConnectionJson -Database "anonymizer_app")
        Probe -Path "/discovery/objects" -Query "object_type=TABLE" -Body (New-ConnectionJson -Database "anonymizer_app")
        Probe -Path "/discovery/objects" -Query "object_type=VIEW"  -Body (New-ConnectionJson -Database "anonymizer_app")

        Write-Host "Phase 4 endpoints..."
        $discoverRespJson = Probe -Path "/schema/discover" -Body (New-DiscoverBodyJson -Database "anonymizer_app")
        $discoverBody = $discoverRespJson | ConvertFrom-Json
        $jobId = $discoverBody.job_id
        Write-Host "  -> job_id=$jobId  tables=$($discoverBody.table_count) columns=$($discoverBody.column_count) fks=$($discoverBody.foreign_key_count) idx=$($discoverBody.index_count) cons=$($discoverBody.constraint_count) cached=$($discoverBody.cached)"

        $jobRespJson = Probe -Path "/schema/job/$jobId" -Method GET
        $jobBody = $jobRespJson | ConvertFrom-Json
        $customerTable = $jobBody.tables | Where-Object { $_.table_name -eq "Customers" } | Select-Object -First 1
        $orderTable    = $jobBody.tables | Where-Object { $_.table_name -eq "Orders" }    | Select-Object -First 1
        if (-not $customerTable) { throw "Customers table not discovered" }
        if (-not $orderTable)    { throw "Orders table not discovered" }

        $emailCol = $customerTable.columns | Where-Object { $_.name -eq "Email" } | Select-Object -First 1
        if (-not $emailCol -or $emailCol.is_nullable -or $emailCol.character_maximum_length -ne 255) {
            throw "Customers.Email metadata incorrect (got: is_nullable=$($emailCol.is_nullable) maxlen=$($emailCol.character_maximum_length))"
        }
        $idCol = $customerTable.columns | Where-Object { $_.name -eq "Id" } | Select-Object -First 1
        if (-not $idCol.is_primary_key) { throw "Customers.Id is_primary_key should be true" }

        $uq = $customerTable.constraints | Where-Object { $_.name -eq "UQ_Customers_Email" } | Select-Object -First 1
        if (-not $uq -or $uq.constraint_type -ne "UNIQUE") {
            throw "UQ_Customers_Email constraint missing or wrong type"
        }

        $fk = $orderTable.foreign_keys | Where-Object { $_.fk_name -eq "FK_Orders_CustomerId" } | Select-Object -First 1
        if (-not $fk -or $fk.referenced_table -ne "Customers" -or $fk.referenced_column -ne "Id") {
            throw "FK_Orders_CustomerId missing or wrong target"
        }
        $ix = $orderTable.indexes | Where-Object { $_.name -eq "IX_Orders_CustomerId" } | Select-Object -First 1
        if (-not $ix -or $ix.is_primary) { throw "IX_Orders_CustomerId missing or wrongly primary" }
        $ck = $orderTable.constraints | Where-Object { $_.constraint_type -eq "CHECK" } | Select-Object -First 1
        if (-not $ck) { throw "CHECK constraint on Orders missing" }

        # Second discover creates a new job (the cache is per-job_id, not
        # global). This is the path that originally exposed the duplicate
        # Connection bug — verify it now succeeds and reuses the existing
        # Server/Connection rows.
        $discoverAgainJson = Probe -Path "/schema/discover" -Body (New-DiscoverBodyJson -Database "anonymizer_app")
        $againBody = $discoverAgainJson | ConvertFrom-Json
        if ($againBody.job_id -eq $jobId) { throw "Each call should produce a fresh job_id" }

        Write-Host "Phase 4 validation: PASS"
    } finally {
        if ($api -and -not $api.HasExited) {
            Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
        }
    }
} finally {
    Write-Host "Stopping MSSQL container..."
    & docker rm -f $containerName 2>&1 | Out-Null
    Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue
}
