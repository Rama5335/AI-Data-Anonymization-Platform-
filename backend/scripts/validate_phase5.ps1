# Phase 5 live validation for a disposable Docker MSSQL container.
#
# Usage (from backend/):
#   .\scripts\validate_phase5.ps1
#
# What it does (everything Phase 3 + 4 did, plus Phase 5):
#   1. Starts mcr.microsoft.com/mssql/server:2022-latest on 127.0.0.1:1433.
#   2. Waits for SQL Server to accept logins.
#   3. Creates the anonymizer_app database.
#   4. Runs `alembic upgrade head` (creates the discovered_* tables).
#   5. Seeds Customers / Orders / CustomerNotes (the last with a
#      nvarchar(MAX) and a varbinary(MAX) column) plus a few rows of
#      data so the sampler has something to return.
#   6. Boots the FastAPI app and hits /health, every /discovery/* route,
#      both /schema/* routes, and the new /sampling/* endpoint.
#   7. Asserts that the sampler truncates the LOB columns and that a
#      second call returns cached rows.
#   8. Tears the container down on exit.
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

function Seed-Fixtures {
    param([int]$Port, [string]$Password)
    Write-Host "Seeding Customers / Orders / CustomerNotes + sample data for sampling..."
    $scriptPath = Join-Path $workDir "seed.py"
    $scriptBody = @"
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$Port;DATABASE=anonymizer_app;UID=sa;PWD=$Password;Encrypt=yes;TrustServerCertificate=yes;'
c = pyodbc.connect(conn_str, autocommit=True)
cur = c.cursor()
# Idempotent: drop in FK-safe order, then recreate.
for t in ('dbo.CustomerNotes', 'dbo.Orders', 'dbo.Customers'):
    cur.execute('IF OBJECT_ID(?, ?) IS NOT NULL DROP TABLE ' + t, t, 'U')

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
cur.execute('''
CREATE TABLE dbo.CustomerNotes (
    Id          INT IDENTITY(1,1) NOT NULL,
    CustomerId  INT               NOT NULL,
    NoteText    NVARCHAR(MAX)     NULL,
    Attachment  VARBINARY(MAX)    NULL,
    CreatedAt   DATETIME2(0)      NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_CustomerNotes PRIMARY KEY (Id),
    CONSTRAINT FK_CustomerNotes_Customer FOREIGN KEY (CustomerId) REFERENCES dbo.Customers(Id)
)
''')
cur.execute("CREATE INDEX IX_Orders_CustomerId ON dbo.Orders (CustomerId)")

# Seed a handful of customers and orders so the sampler has rows.
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

# A note with a 2 KB nvarchar(MAX) to force truncation; an attachment with a 64-byte varbinary.
big_note = 'X' * 2048
attachment = bytes(range(64))
cur.execute(
    "INSERT INTO dbo.CustomerNotes (CustomerId, NoteText, Attachment) VALUES (?, ?, ?)",
    1, big_note, attachment,
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

    Seed-Fixtures -Port $mssqlPort -Password $saPassword

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

        Write-Host "Phase 4 endpoints..."
        $discoverRespJson = Probe -Path "/schema/discover" -Body (New-DiscoverBodyJson -Database "anonymizer_app")
        $discoverBody = $discoverRespJson | ConvertFrom-Json
        $jobId = $discoverBody.job_id
        Write-Host "  -> job_id=$jobId  tables=$($discoverBody.table_count) columns=$($discoverBody.column_count) fks=$($discoverBody.foreign_key_count) idx=$($discoverBody.index_count) cons=$($discoverBody.constraint_count)"

        $jobRespJson = Probe -Path "/schema/job/$jobId" -Method GET
        $jobBody = $jobRespJson | ConvertFrom-Json
        $customerTable = $jobBody.tables | Where-Object { $_.table_name -eq "Customers" } | Select-Object -First 1
        $orderTable    = $jobBody.tables | Where-Object { $_.table_name -eq "Orders" }    | Select-Object -First 1
        $notesTable    = $jobBody.tables | Where-Object { $_.table_name -eq "CustomerNotes" } | Select-Object -First 1
        if (-not $customerTable) { throw "Customers table not discovered" }
        if (-not $notesTable)    { throw "CustomerNotes table not discovered" }

        # Phase 5 sampling: Customers first (bounded types only).
        Write-Host "Phase 5 endpoints (sampling)..."
        $customersSampleJson = Probe -Path "/sampling/tables/$($customerTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 512 } | ConvertTo-Json -Compress
        )
        $customersSample = $customersSampleJson | ConvertFrom-Json
        if ($customersSample.row_count -ne 5) {
            throw "Expected 5 sampled rows from Customers, got $($customersSample.row_count)"
        }
        if ($customersSample.sampled_columns -notcontains 'Email') {
            throw "Customers sample should include the Email column"
        }
        $firstEmail = $customersSample.rows[0].Email
        if (-not $firstEmail.value -or $firstEmail.truncated) {
            throw "Customers.Email sample should be untruncated (got: $firstEmail)"
        }
        Write-Host "  -> Customers sample: $($customersSample.row_count) rows, cached=$($customersSample.cached)"

        # Cached re-read: should return cached=true.
        $customersSample2Json = Probe -Path "/sampling/tables/$($customerTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 512 } | ConvertTo-Json -Compress
        )
        $customersSample2 = $customersSample2Json | ConvertFrom-Json
        if (-not $customersSample2.cached) { throw "Second sample call should be cached" }

        # LOB truncation: CustomerNotes.NoteText is nvarchar(MAX) with a 2KB value.
        $notesSampleJson = Probe -Path "/sampling/tables/$($notesTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 256 } | ConvertTo-Json -Compress
        )
        $notesSample = $notesSampleJson | ConvertFrom-Json
        if ($notesSample.row_count -ne 2) {
            throw "Expected 2 sampled rows from CustomerNotes, got $($notesSample.row_count)"
        }
        $bigNote = $notesSample.rows[0].NoteText
        if (-not $bigNote.truncated) {
            throw "CustomerNotes.NoteText (2KB value, 256-byte cap) should be flagged truncated=true"
        }
        if ($bigNote.original_length -ne 2048) {
            throw "CustomerNotes.NoteText original_length should be 2048, got $($bigNote.original_length)"
        }
        if ($bigNote.value.Length -gt 256) {
            throw "CustomerNotes.NoteText value should be <= 256 chars after truncation, got $($bigNote.value.Length)"
        }
        $shortNote = $notesSample.rows[1].NoteText
        if ($shortNote.truncated) {
            throw "CustomerNotes.NoteText (short note) should not be truncated"
        }
        $attachment = $notesSample.rows[0].Attachment
        if ($attachment.value.Length -ne 128) {  # 64 bytes -> 128 hex chars
            throw "CustomerNotes.Attachment (64-byte value) hex length should be 128, got $($attachment.value.Length)"
        }
        Write-Host "  -> CustomerNotes: nvarchar(MAX) truncated to 256 chars, varbinary(MAX) hex=128 chars"

        # Excluded column test: sampling with a column excluded should omit it.
        $customersSample3Json = Probe -Path "/sampling/tables/$($customerTable.id)" -Body (
            @{ connection = (New-ConnectionHash -Database "anonymizer_app"); row_count = 5; max_bytes_per_value = 512; excluded_column_names = @("Email") } | ConvertTo-Json -Compress
        )
        $customersSample3 = $customersSample3Json | ConvertFrom-Json
        if ($customersSample3.sampled_columns -contains 'Email') {
            throw "Email should be excluded from the sample"
        }
        if ($customersSample3.sampled_columns -notcontains 'FullName') {
            throw "FullName should still be in the sample"
        }
        Write-Host "  -> excluded_column_names honored: sample omits Email"

        Write-Host "Phase 5 validation: PASS"
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
