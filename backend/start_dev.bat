@echo off
setlocal
:: Dev launcher for the backend.
:: Requires JWT_SECRET_KEY and CREDENTIAL_ENCRYPTION_KEY to be set in
:: the environment beforehand (see ../.env.example for how to generate).
if "%JWT_SECRET_KEY%"=="" (
  echo [ERROR] JWT_SECRET_KEY is not set.
  echo         Generate one:  python -c "import secrets; print(secrets.token_urlsafe(48))"
  goto :fail
)
if "%CREDENTIAL_ENCRYPTION_KEY%"=="" (
  echo [ERROR] CREDENTIAL_ENCRYPTION_KEY is not set.
  echo         Generate one:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  goto :fail
)

set "BASE=%~dp0"
set "BASE=%BASE:\=/%"
set DATABASE_URL=sqlite:///%BASE%app.db
set LLM_PROVIDER=ollama
set OLLAMA_BASE_URL=http://127.0.0.1:11434
set OLLAMA_MODEL=llama3.1:8b
set BACKUP_DIR=%BASE%../backups
set BACKUP_KEEP=5

:: Optional first-run bootstrap admin. Uncomment and set your OWN values.
:: set BOOTSTRAP_ADMIN_USERNAME=admin
:: set BOOTSTRAP_ADMIN_PASSWORD=change-me

cd /d "%~dp0"
.\.cpython311-venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:fail
echo.
echo Startup aborted. See the errors above.
exit /b 1
