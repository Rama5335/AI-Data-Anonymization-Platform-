# syntax=docker/dockerfile:1.6
# Backend image: offline-first FastAPI service.
# Built on a slim Python 3.11 base. The MSSQL driver (msodbcsql18)
# and unixODBC are installed so the platform can talk to live
# MSSQL instances out of the box.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# OS-level dependencies: unixODBC + msodbcsql18 (the official
# Microsoft driver) + curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gnupg2 \
        apt-transport-https \
        ca-certificates \
        unixodbc \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies in their own layer so source changes
# don't bust the cache.
COPY backend/pyproject.toml /app/pyproject.toml
RUN pip install --upgrade pip \
    && pip install .

# Copy the rest of the source.
COPY backend /app

# The application reads CREDENTIAL_ENCRYPTION_KEY, JWT_SECRET_KEY,
# DATABASE_URL, LLM_PROVIDER, etc. from env. A sane default is
# provided for the LLM provider; the rest are required.
ENV LLM_PROVIDER=ollama \
    OLLAMA_BASE_URL=http://ollama:11434 \
    OLLAMA_MODEL=qwen2.5:7b-instruct \
    BACKUP_DIR=/backups \
    BACKUP_KEEP=5

# Docker secrets support: when secrets are mounted at /run/secrets/,
# the entrypoint reads them into environment variables. This avoids
# putting secrets in docker-compose environment: blocks or .env files.
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
