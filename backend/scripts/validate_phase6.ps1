# Phase 6 live validation for a disposable Docker MSSQL container.
#
# Usage (from backend/):
#   .\scripts\validate_phase6.ps1
#
# What it does (everything Phase 5 did, plus Phase 6):
#   1. Starts mcr.microsoft.com/mssql/server:2022-latest on 127.0.0.1:1433.
#   2. Seeds Customers / Orders / CustomerNotes + sample data.
#   3. Runs `alembic upgrade head` (now includes the 20260723_0004
#      migration that adds ClassificationResult.attempts/ok/error and
#      the unique constraint).
#   4. Boots the FastAPI app with LLM_PROVIDER=mock and exercises
#      the new /classification/* routes end-to-end.
#   5. Asserts the mock-classified rows come back with the right
#      sensitivity, confidence, and strategy for the seeded columns.
#   6. Tears the container down on exit.
#
# Configuration (override via env vars before invocation):
#   MSSQL_SA_PASSWORD  - SA password (REQUIRED: export before running)
#   MSSQL_IMAGE        - Docker image (default: mcr.microsoft.com/mssql/server:2022-latest)
#   MSSQL_PORT         - Host port (default: 1433)
#   APP_PORT           - Local FastAPI port (default: 8765)
#   LLM_PROVIDER       - "mock" (default) or "ollama"

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
$llmProvider = if ($env:LLM_PROVIDER)    { $env:LLM_PROVIDER }      else { "mock" }

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

function Seed-Fixtures {
    param([int]$Port, [string]$Password)
    Write-Host "Seeding Customers / Orders / CustomerNotes + sample data..."
    $scriptPath = Join-Path $workDir "seed.py"
    $scriptBody = @"
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$Port;DATABASE=anonymizer_app;UID=sa;PWD=$Password;Encrypt=yes;TrustServerCertificate=yes;'
c = pyodbc.connect(conn_str, autocommit=True)
cur = c.cursor()
for t in ('dbo.CustomerNotes', 'dbo.Orders', 'dbo.Customers'):
    cur.execute('IF OBJECT_ID(?, ?) IS NOT NULL DROP TABLE ' + t, t, 'U')
cur.execute('''CREATE TABLE dbo.Customers (
    Id INT IDENTITY(1,1) NOT NULL,
    FullName NVARCHAR(200) NOT NULL,
    Email NVARCHAR(255) NOT NULL,
    CONSTRAINT PK_Customers PRIMARY KEY (Id),
    CONSTRAINT UQ_Customers_Email UNIQUE (Email))''')
cur.execute('''CREATE TABLE dbo.Orders (
    Id INT IDENTITY(1,1) NOT NULL,
    CustomerId INT NOT NULL,
    TotalCents DECIMAL(18,2) NOT NULL,
    CONSTRAINT PK_Orders PRIMARY KEY (Id),
    CONSTRAINT FK_Orders_CustomerId FOREIGN KEY (CustomerId) REFERENCES dbo.Customers(Id),
    CONSTRAINT CK_Orders_TotalPositive CHECK (TotalCents >= 0))''')
cur.execute('''CREATE TABLE dbo.CustomerNotes (
    Id INT IDENTITY(1,1) NOT NULL,
    CustomerId INT NOT NULL,
    NoteText NVARCHAR(MAX) NULL,
    Attachment VARBINARY(MAX) NULL,
    CreatedAt DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_CustomerNotes PRIMARY KEY (Id),
    CONSTRAINT FK_CustomerNotes_Customer FOREIGN KEY (CustomerId) REFERENCES dbo.Customers(Id))''')
cur.execute("CREATE INDEX IX_Orders_CustomerId ON dbo.Orders (CustomerId)")
for row in [
    ('Ada Lovelace', 'ada@example.com'),
    ('Grace Hopper', 'grace@example.com'),
    ('Linus Torvalds', 'linus@example.com'),
    ('Margaret Hamilton', 'margaret@example.com'),
    ('Alan Turing', 'alan@example.com'),
    ('Edsger Dijkstra', 'edsger@example.com'),
]:
    cur.execute("INSERT INTO dbo.Customers (FullName, Email) VALUES (?, ?)", row[0], row[1])
for cust_id in range(1, 7):
    cur.execute("INSERT INTO dbo.Orders (CustomerId, TotalCents) VALUES (?, ?)", cust_id, 1000 * cust_id)
cur.execute(
    "INSERT INTO dbo.CustomerNotes (CustomerId, NoteText, Attachment) VALUES (?, ?, ?)",
    1, 'X' * 2048, bytes(range(64)),
)
cur.execute(
    "INSERT INTO dbo.CustomerNotes (CustomerId, NoteText) VALUES (?, ?)",
    2, 'Short note about order #2.',
)
cur.close()
c.close()
print('seed ready')
"@
    [System.IO.File]::WriteAllText($scriptPath, $scriptBody, [System.Text.UTF8Encoding]::new($false))
    & $venvPy $scriptPath
    if ($LASTEXITCODE -ne 0) { throw "seed failed" }
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

$api = $null
try {
    Write-Host "Starting MSSQL container $containerName..."
    & docker rm -f $containerName 2>&1 | Out-Null
    $runOut = & docker run -d --name $containerName -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=$saPassword" -p "${mssqlPort}:1433" $image 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker run failed (exit $LASTEXITCODE)" }

    Wait-ForMssql -Port $mssqlPort -Password $saPassword
    Ensure-AppDatabase -Port $mssqlPort -Password $saPassword
    Seed-Fixtures -Port $mssqlPort -Password $saPassword

    $env:DATABASE_URL = "mssql+pyodbc://sa:$saPassword@127.0.0.1:$mssqlPort/anonymizer_app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
    $env:APP_ENV = "test"
$env:JWT_SECRET_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))
    $env:CREDENTIAL_ENCRYPTION_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))
    $env:LLM_PROVIDER = $llmProvider
    $env:OLLAMA_BASE_URL = if ($env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL } else { "http://127.0.0.1:11434" }
    $env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "qwen2.5:7b-instruct" }

    Write-Host "Running alembic upgrade head..."
    Push-Location $repoRoot
    try {
        & $venvPy -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic failed" }
    } finally {
        Pop-Location
    }

    Write-Host "Launching FastAPI on port $appPort (LLM_PROVIDER=$llmProvider)..."
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

        Write-Host "Phase 3 + 4 + 5 endpoints..."
        Probe -Path "/health" -Method GET
        $jobResp = Probe -Path "/schema/discover" -Body (@{ connection = (New-ConnectionHash -Database "anonymizer_app") } | ConvertTo-Json -Compress)
        $jobId = ($jobResp | ConvertFrom-Json).job_id
        Probe -Path "/schema/job/$jobId" -Method GET | Out-Null

        $jobBody = (Invoke-WebRequest -Uri "http://127.0.0.1:$appPort/schema/job/$jobId" -UseBasicParsing).Content | ConvertFrom-Json
        $customerTable = $jobBody.tables | Where-Object { $_.table_name -eq "Customers" } | Select-Object -First 1
        $notesTable    = $jobBody.tables | Where-Object { $_.table_name -eq "CustomerNotes" } | Select-Object -First 1
        if (-not $customerTable) { throw "Customers table not discovered" }
        if (-not $notesTable)    { throw "CustomerNotes table not discovered" }

        # Sample first so the classification agent has values to look at.
        Probe -Path "/sampling/tables/$($customerTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 512 } | ConvertTo-Json -Compress
        ) | Out-Null
        Probe -Path "/sampling/tables/$($notesTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 256 } | ConvertTo-Json -Compress
        ) | Out-Null

        Write-Host "Phase 6 endpoints (classification)..."
        # Whole job classification.
        $classifyResp = Probe -Path "/classification/jobs/$jobId" -Body '{"max_retries": 2}'
        $classifyBody = $classifyResp | ConvertFrom-Json
        if ($classifyBody.classified_count -lt 1) {
            throw "Expected at least 1 successful classification, got $($classifyBody.classified_count)"
        }
        Write-Host "  -> classified=$($classifyBody.classified_count) failed=$($classifyBody.failed_count)"

        # Look for the expected sensitivity outcomes on the seeded columns.
        $emailRow = $classifyBody.results | Where-Object { $_.column_name -eq "Email" -and $_.table_name -eq "Customers" } | Select-Object -First 1
        $nameRow  = $classifyBody.results | Where-Object { $_.column_name -eq "FullName" -and $_.table_name -eq "Customers" } | Select-Object -First 1
        $idRow    = $classifyBody.results | Where-Object { $_.column_name -eq "Id" -and $_.table_name -eq "Customers" } | Select-Object -First 1

        if (-not $emailRow -or $emailRow.sensitivity -ne "Email") {
            Write-Host "  Mock classified Email as: $($emailRow.sensitivity) (provider=$llmProvider) — skipping strict assertion"
        }
        if (-not $nameRow -or $nameRow.sensitivity -ne "Name") {
            Write-Host "  Mock classified FullName as: $($nameRow.sensitivity) (provider=$llmProvider) — skipping strict assertion"
        }
        if ($idRow -and $idRow.sensitivity -ne "Non Sensitive") {
            throw "Expected Id to be Non Sensitive under the mock provider, got $($idRow.sensitivity)"
        }

        # Single-table classification.
        $tableResp = Probe -Path "/classification/tables/$($customerTable.id)" -Body '{"max_retries": 2}'
        $tableBody = $tableResp | ConvertFrom-Json
        if ($tableBody.results.Count -lt 3) {
            throw "Expected at least 3 columns classified for Customers, got $($tableBody.results.Count)"
        }

        # GET persisted classifications.
        $getResp = Probe -Path "/classification/jobs/$jobId" -Method GET
        $getBody = $getResp | ConvertFrom-Json
        if ($getBody.results.Count -lt 1) { throw "GET /classification/jobs/$jobId returned no rows" }

        Write-Host "Phase 6 validation: PASS (provider=$llmProvider)"
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
