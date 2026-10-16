# Bring up the live dev stack (Docker MSSQL + API + Vite) and keep it running.
#
# Usage: .\scripts\dev_up.ps1
# Stop:  .\scripts\dev_down.ps1 (or just Ctrl+C each process)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = $repoRoot
$frontend = Join-Path $repoRoot "..\frontend"
$venvPy = Join-Path $repoRoot ".cpython311-venv\Scripts\python.exe"
$containerName = "ai-anonymizer-mssql-validation"
$saPassword = $env:MSSQL_SA_PASSWORD
if (-not $saPassword) { throw 'MSSQL_SA_PASSWORD is not set. Export it before running this script.' }
$mssqlPort = 1433
$appPort = 8765
$workDir = Join-Path $repoRoot ".validation-work"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

# 1. Start Docker.
& docker rm -f $containerName 2>&1 | Out-Null
& docker run -d --name $containerName -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=$saPassword" -p "${mssqlPort}:1433" mcr.microsoft.com/mssql/server:2022-latest
if ($LASTEXITCODE -ne 0) { throw "docker run failed" }

# 2. Wait for MSSQL.
$waitScript = @"
import sys, time, pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$mssqlPort;UID=sa;PWD=$saPassword;Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=5;'
for _ in range(60):
    try:
        c = pyodbc.connect(conn_str, timeout=5)
        c.close()
        sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
"@
$waitPath = Join-Path $workDir "wait.py"
[System.IO.File]::WriteAllText($waitPath, $waitScript, [System.Text.UTF8Encoding]::new($false))
Write-Host "Waiting for MSSQL..." -NoNewline
& $venvPy $waitPath
if ($LASTEXITCODE -ne 0) { throw "MSSQL did not start" }
Write-Host " ready."

# 3. Create DB + seed.
$seedScript = @"
import pyodbc
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$mssqlPort;DATABASE=master;UID=sa;PWD=$saPassword;Encrypt=yes;TrustServerCertificate=yes;'
m = pyodbc.connect(conn_str, autocommit=True)
c = m.cursor()
c.execute("IF DB_ID('anonymizer_app') IS NULL CREATE DATABASE anonymizer_app")
c.close(); m.close()

conn_str2 = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=127.0.0.1,$mssqlPort;DATABASE=anonymizer_app;UID=sa;PWD=$saPassword;Encrypt=yes;TrustServerCertificate=yes;'
c = pyodbc.connect(conn_str2, autocommit=True)
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
for cid in range(1, 7):
    cur.execute("INSERT INTO dbo.Orders (CustomerId, TotalCents) VALUES (?, ?)", cid, 1000*cid)
cur.execute("INSERT INTO dbo.CustomerNotes (CustomerId, NoteText, Attachment) VALUES (?, ?, ?)",
            1, 'X' * 2048, bytes(range(64)))
cur.execute("INSERT INTO dbo.CustomerNotes (CustomerId, NoteText) VALUES (?, ?)",
            2, 'Short note about order #2.')
cur.close(); c.close()
print('seeded')
"@
$seedPath = Join-Path $workDir "seed.py"
[System.IO.File]::WriteAllText($seedPath, $seedScript, [System.Text.UTF8Encoding]::new($false))
& $venvPy $seedPath
if ($LASTEXITCODE -ne 0) { throw "seed failed" }

# 4. Migrate.
$env:DATABASE_URL = "mssql+pyodbc://sa:$saPassword@127.0.0.1:$mssqlPort/anonymizer_app?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=yes"
$env:JWT_SECRET_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))
$env:CREDENTIAL_ENCRYPTION_KEY = ([System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 })))
Push-Location $backend
& $venvPy -m alembic upgrade head
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "alembic failed" }

# 5. Start API + Vite.
Write-Host "Starting API on 127.0.0.1:$appPort..."
# Forward LLM env vars so the API can talk to Ollama (or fall back to mock).
if (-not $env:LLM_PROVIDER) { $env:LLM_PROVIDER = "ollama" }
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434" }
if (-not $env:OLLAMA_MODEL) { $env:OLLAMA_MODEL = "llama3.1:8b" }
if (-not $env:OLLAMA_REQUEST_TIMEOUT_SECONDS) { $env:OLLAMA_REQUEST_TIMEOUT_SECONDS = "180" }
Write-Host "  LLM_PROVIDER=$($env:LLM_PROVIDER)  OLLAMA_MODEL=$($env:OLLAMA_MODEL)  OLLAMA_BASE_URL=$($env:OLLAMA_BASE_URL)"
$api = Start-Process -FilePath $venvPy -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$appPort"
) -PassThru -NoNewWindow -RedirectStandardOutput "uvicorn.out.log" -RedirectStandardError "uvicorn.err.log" -WorkingDirectory $backend

$npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npmCmd) { $npmCmd = "npm.cmd" }
Write-Host "Starting Vite on 127.0.0.1:5174..."
$vite = Start-Process -FilePath $npmCmd -ArgumentList @("run", "dev") `
    -PassThru -NoNewWindow -RedirectStandardOutput "vite.out.log" -RedirectStandardError "vite.err.log" `
    -WorkingDirectory $frontend

Write-Host ""
Write-Host "Stack is up:"
Write-Host "  - MSSQL container: $containerName"
Write-Host "  - API pid:         $($api.Id)"
Write-Host "  - Vite pid:        $($vite.Id)"
Write-Host "  - API logs:        $backend\uvicorn.err.log"
Write-Host "  - Vite logs:       $frontend\vite.err.log"
Write-Host ""
Write-Host "Open http://127.0.0.1:5174"
Write-Host "Stop with: Stop-Process -Id $($api.Id),$($vite.Id); docker rm -f $containerName"
