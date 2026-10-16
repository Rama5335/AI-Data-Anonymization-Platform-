#!/bin/sh
set -e

# Docker secrets → environment variables.
# If a secret file exists at /run/secrets/<name>, export it as
# <ENV_VAR> so the Python app can read it via os.environ.
#
# Mapping:
#   /run/secrets/jwt_secret_key              → JWT_SECRET_KEY
#   /run/secrets/credential_encryption_key   → CREDENTIAL_ENCRYPTION_KEY
#   /run/secrets/mssql_sa_password           → (used in DATABASE_URL)

for secret_file in /run/secrets/*; do
    [ -f "$secret_file" ] || continue
    secret_name=$(basename "$secret_file")
    env_var=$(echo "$secret_name" | tr '[:lower:]' '[:upper:]')
    export "$env_var"=$(cat "$secret_file")
done

# Apply pending metadata-DB migrations before the app starts. This is
# idempotent (``alembic upgrade head`` only applies missing revisions) and
# makes a fresh database usable without a separate manual step. The
# compose stack brings the database up first via ``depends_on``.
if [ -f /app/alembic.ini ]; then
    echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
    alembic upgrade head
fi

exec "$@"
