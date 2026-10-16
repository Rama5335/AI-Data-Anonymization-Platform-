# Docker assets

This directory contains the production-ish Docker assets for the
AI Data Anonymization Platform.

## Services

* **backend** — FastAPI app, built from `docker/backend.Dockerfile`.
  Listens on `127.0.0.1:8765`. The image installs `msodbcsql18`
  so the platform can connect to a real MSSQL out of the box.
* **frontend** — Vite build served by nginx (`docker/frontend.Dockerfile`).
  Listens on `127.0.0.1:5173` and proxies `/api/*` to the backend.
* **ollama** — `ollama/ollama:0.3.12`. Pulls `qwen2.5:7b-instruct`
  on first start. Override with `OLLAMA_MODEL` in compose.
* **mssql** *(optional)* — disposable `mssql/server:2022-latest` for
  the live validators. Bind-mounts `./backups` to
  `/var/opt/mssql/backups` so the backup agent has a path the
  container can write to.

## Quick start

```bash
# Local dev (one-shot, on 127.0.0.1 only)
docker compose -f docker/docker-compose.yml up -d --build

# Pull the LLM (one-time, ~5 GB)
docker exec -it ai-anonymizer-ollama ollama pull qwen2.5:7b-instruct

# Open the UI
open http://127.0.0.1:5173

# Run the live validator end-to-end
pwsh backend/scripts/validate_phase*.ps1
```

## Port policy (per `implement_plan.md` §15)

All four services are bound to `127.0.0.1` by default. The platform
is meant to be reachable only on `localhost`; flip to `0.0.0.0` only
on a trusted network and behind a reverse proxy that adds TLS +
authentication.

## Production stack (`docker-compose.prod.yml`)

The production compose adds three layers that the dev stack omits:

1. **Caddy reverse proxy** — auto-TLS via Let's Encrypt. All traffic
   goes through Caddy; the backend and frontend are not exposed to the
   host network.
2. **Docker secrets** — `JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`,
   and database passwords are stored as Docker secrets, not in compose
   files or `.env` files.
3. **Ollama API key gate** (optional) — an nginx sidecar that sits in
   front of Ollama and requires an API key, preventing other services
   on the Docker network from accessing inference directly.

### Quick start (production)

```bash
# 1. Create the .env file
cp docker/.env.prod.example docker/.env.prod
# Edit .env.prod with your values

# 2. Generate secrets
JWT_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
ENC_KEY=$(python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())")

# 3. Create Docker secrets
echo "$JWT_KEY" | docker secret create jwt_secret_key -
echo "$ENC_KEY" | docker secret create credential_encryption_key -
echo "your-db-password" | docker secret create mssql_sa_password -

# 4. Deploy
docker stack deploy -c docker/docker-compose.prod.yml anonymizer

# Or with docker compose:
docker compose -f docker/docker-compose.prod.yml --env-file docker/.env.prod up -d

# 5. Pull the LLM model (first time)
docker exec -it ai-anonymizer-ollama ollama pull llama3.1:8b
```

### Secrets management

| Secret | Env var (after entrypoint) | Description |
| --- | --- | --- |
| `jwt_secret_key` | `JWT_SECRET_KEY` | 32-byte hex string for JWT signing |
| `credential_encryption_key` | `CREDENTIAL_ENCRYPTION_KEY` | 32-byte base64 string for encrypting DB credentials at rest |
| `mssql_sa_password` | `MSSQL_SA_PASSWORD` | Optional: SA password for the platform's metadata DB |

The `entrypoint.sh` script reads files from `/run/secrets/` and exports
them as environment variables before starting uvicorn. This keeps
secrets out of compose files, `.env` files, and Docker image layers.

### TLS

The default Caddyfile uses `:443` (self-signed / internal). For
production with a public domain:

1. Edit `docker/Caddyfile` — replace `:443` with your domain:
   ```
   yourdomain.com {
       reverse_proxy backend:8765
   }
   ```
2. Caddy automatically obtains a Let's Encrypt certificate.
3. Ensure ports 80 and 443 are open for the ACME challenge.

### Ollama API key gate (optional)

To require an API key for Ollama inference:

1. Create the secret: `echo "my-ollama-key" | docker secret create ollama_api_key -`
2. Uncomment the `ollama-api-gate` service in `docker-compose.prod.yml`
3. Uncomment the `ollama_api_key` secret
4. Change the backend's `OLLAMA_BASE_URL` to `http://ollama-api-gate:8080`
5. Create `docker/ollama-api-gate.conf` (see example below)

**`ollama-api-gate.conf`:**
```nginx
server {
    listen 8080;
    location / {
        # Check for API key in Authorization header
        if ($http_authorization != "Bearer ${OLLAMA_API_KEY}") {
            return 401;
        }
        proxy_pass http://ollama:11434;
    }
}
```

In practice, the backend container is the only service that talks to
Ollama, so the API key gate is defense-in-depth for multi-tenant Docker
networks.
