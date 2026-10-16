@echo off
setlocal
title AI Data Anonymization Platform
echo.
echo ================================================
echo   AI Data Anonymization Platform - Starting...
echo ================================================
echo.

:: ---- Required secrets: set these in the environment first ----
::   powershell:  $env:JWT_SECRET_KEY = "..."
::   cmd:         set JWT_SECRET_KEY=...
if "%JWT_SECRET_KEY%"=="" (
  echo [ERROR] JWT_SECRET_KEY is not set.
  echo         Generate one:  python -c "import secrets; print(secrets.token_urlsafe(48))"
  echo         Then set:      set JWT_SECRET_KEY=your-value
  goto :fail
)
if "%CREDENTIAL_ENCRYPTION_KEY%"=="" (
  echo [ERROR] CREDENTIAL_ENCRYPTION_KEY is not set.
  echo         Generate one:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  echo         Then set:      set CREDENTIAL_ENCRYPTION_KEY=your-value
  goto :fail
)

:: ---- Environment Variables (paths relative to this script) ----
set "BASE=%~dp0"
set "BASE=%BASE:\=/%"
set DATABASE_URL=sqlite:///%BASE%backend/app.db
set LLM_PROVIDER=ollama
set OLLAMA_BASE_URL=http://127.0.0.1:11434
set OLLAMA_MODEL=llama3.1:8b
set BACKUP_DIR=%BASE%backups
set BACKUP_KEEP=5

:: Optional first-run bootstrap admin. Uncomment and set your OWN values,
:: then remove them after the first successful login.
:: set BOOTSTRAP_ADMIN_USERNAME=admin
:: set BOOTSTRAP_ADMIN_PASSWORD=change-me

:: ---- Start Backend (API server) ----
echo [1/2] Starting Backend API on http://127.0.0.1:8765 ...
start "Backend API" /min /d "%~dp0backend" cmd /c ".\.cpython311-venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765"

:: ---- Wait for backend to be ready ----
echo       Waiting for backend to start...
timeout /t 4 /nobreak >nul

:: ---- Start Frontend (Vite dev server) ----
echo [2/2] Starting Frontend UI on http://127.0.0.1:5174 ...
start "Frontend Vite" /min /d "%~dp0frontend" cmd /c ".\node_modules\.bin\vite --port 5174"

:: ---- Done ----
echo.
echo ================================================
echo   Both servers are running!
echo.
echo   Frontend:  http://127.0.0.1:5174
echo   Backend:   http://127.0.0.1:8765
echo.
echo   Login:     use your own BOOTSTRAP_ADMIN_* credentials
echo ================================================
echo.
echo   Press any key to STOP both servers...
echo.
pause >nul

:: ---- Shutdown ----
echo Stopping servers...
taskkill /FI "WINDOWTITLE eq Backend API*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Frontend Vite*" /T /F >nul 2>&1
echo Done.
timeout /t 2 /nobreak >nul
goto :eof

:fail
echo.
echo Startup aborted. See the errors above.
exit /b 1
