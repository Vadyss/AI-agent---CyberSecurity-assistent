<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Operations guide

How to deploy, configure, observe, back up and update a Toolsmith installation. The target is one Linux host with Docker Compose (see `Docs/DECISIONS.md`, D-013 and D-015).

## Topology

```
Internet ── TLS reverse proxy (yours) ── oauth2-proxy :4180 ──┬── frontend (nginx) ── /api/* ──┐
                                                             └── /m2m/* (API keys) ───────────┤
                                                                                              ▼
                       PostgreSQL ◄── backend (API, queue workers, gatekeeper) ── Unix socket ── sandbox (no network)
                                            │
                                         /data volume (per-tenant tool registry, rules, audit chains)
```

Only oauth2-proxy is published. Backend metrics (port 9100) and the sandbox socket stay inside Docker.

## First deployment

1. Register an OIDC client with your identity provider (for example Microsoft Entra ID, Google Workspace, Okta, Keycloak or Auth0). Redirect URI: `https://<your host>/oauth2/callback`. Note the issuer URL, the client ID and the client secret.
2. Create `.env` next to `docker-compose.yml`:

   ```bash
   sh scripts/bootstrap-env.sh            # generates POSTGRES_PASSWORD and OAUTH2_PROXY_COOKIE_SECRET
   ```

   Then set at least:

   | Variable | Value |
   |---|---|
   | `PUBLIC_URL` | `https://toolsmith.example.com` (no trailing slash) |
   | `OIDC_ISSUER` | Provider issuer URL (HTTPS) |
   | `OIDC_AUDIENCE` | The client ID (oauth2-proxy uses it as `client-id`; the backend checks it as token audience) |
   | `OIDC_CLIENT_SECRET` | The client secret |
   | `OIDC_JWKS_URL` | The provider's JWKS URL (from `<issuer>/.well-known/openid-configuration`, field `jwks_uri`) |
   | `BOOTSTRAP_ADMIN_EMAIL` | E-mail of the first administrator (must be verified at the provider) |
   | `APIFY_TOKEN` or `LLM_API_KEY` | Model provider credentials |

3. Start the stack:

   ```bash
   APP_VERSION=$(git rev-parse --short HEAD) docker compose up --build -d --wait
   ```

4. Put a TLS-terminating reverse proxy (Caddy, nginx, Traefik or a cloud load balancer) in front of `127.0.0.1:4180`. oauth2-proxy sets secure cookies and expects HTTPS.
5. Sign in as the bootstrap administrator, then invite colleagues (`POST /api/v1/invitations`) and create API keys as needed.

The backend refuses to start with incomplete production settings (missing database URL, OIDC settings, HTTP issuer, mock model). Read the error in `docker compose logs backend`.

## Configuration reference

All settings are environment variables; `.env.example` lists every name. Highlights:

| Setting | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | `production` | `development` allows `AUTH_MODE=disabled`; never use it on a reachable host. |
| `SIGNUP_MODE` | `invite_only` | `self_service` lets any verified user create up to three tenants. |
| `DEFAULT_MONTHLY_BUDGET_USD` | `50` | Model budget per tenant per month; admins can set per-tenant values. |
| `DEFAULT_MAX_RUNS_PER_DAY` | `200` | Run quota per tenant per day. |
| `RATE_LIMIT_PER_MINUTE` / `RUN_RATE_LIMIT_PER_MINUTE` | `300` / `10` | Per actor / new runs per tenant. |
| `WORKER_CONCURRENCY` | `2` | Pipelines executing at once. |
| `SANDBOX_MAX_JOBS` / `SANDBOX_MAX_JOBS_PER_TENANT` | `2` / `1` | Concurrent sandbox jobs, globally and per tenant. |
| `LOG_FORMAT` | `json` | `text` for local reading. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Set to export traces over OTLP/HTTP. |

## Observability

- **Logs:** JSON lines on stdout (`docker compose logs -f backend sandbox`). Every line has `request_id`; tenant, run and actor when known. Clients see the same `X-Request-ID`, so a support request can be matched to the logs.
- **Metrics:** Prometheus format at `http://backend:9100/metrics` inside the Docker network. Add a Prometheus container on the same network to scrape it. Useful alerts: `toolsmith_runs_finished_total{status="failed"}` rising, `toolsmith_queue_depth` above the worker count for long, `toolsmith_sandbox_executions_total{status="timeout"}` rising, `toolsmith_llm_unknown_cost_calls_total` above zero.
- **Readiness:** `GET /ready` (inside the network) returns `503` and the failing component: `database`, `schema`, `sandbox` or `identity_provider`.
- **Traces:** set `OTEL_EXPORTER_OTLP_ENDPOINT` (for example a Jaeger or Grafana Tempo collector) to see one span per request and per run, with model and sandbox calls as children.
- **Audit:** `docker compose exec backend python scripts/verify_audit.py /data` verifies every tenant's hash chain.

## Backups and restore

`scripts/backup.sh` writes a PostgreSQL dump, an archive of the `/data` volume, the audit verification result, SHA-256 checksums and a manifest (schema revision, app version). Files are created with mode 600. They contain tenant data: encrypt them before they leave the host (for example `age -r <recipient> -o backup.tar.age`).

```bash
# Back up (default output: backups/<UTC timestamp>/)
COMPOSE="docker compose" sh scripts/backup.sh

# Restore: checks checksums, asks for confirmation, stops the backend, restores the
# database and the volume, applies newer migrations, verifies audit chains, starts the backend.
COMPOSE="docker compose" sh scripts/restore.sh backups/20261011T100307Z
```

Schedule a daily backup with cron or a systemd timer, keep at least 7 daily and 4 weekly copies off the host, and run a restore drill every quarter.

**Drill performed on 2026-10-11:** on the demo stack, a run forged and installed `nginx_access_parser`, a backup was taken, all volumes were destroyed (`docker compose down -v`), a fresh stack was started (no runs, seed tools only), and the backup was restored. Afterwards the run (status `approved`), the installed tool, a valid audit chain and `/ready` = 200 were back.

Recovery point: the time of the last backup. Runs that were active at backup time are closed as failed after a restore and retried automatically (D-013).

## Updates

- **Application:** `git pull`, then `docker compose up --build -d --wait`. The `migrate` service applies database migrations before the backend starts; the backend refuses to start on an outdated schema. Take a backup first.
- **Python dependencies:** edit `backend/requirements.txt` (or `sandbox/requirements.txt`), then regenerate the lock in the pinned base image:

  ```bash
  docker run --rm -v "$PWD":/w -w /w/backend python:3.12.15-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 \
    sh -c "pip install -q pip-tools==7.6.2 && pip-compile --generate-hashes --strip-extras --allow-unsafe --no-header --output-file requirements.lock requirements.txt"
  ```

- **Base images:** update the digest in `backend/Dockerfile` and `sandbox/Dockerfile` deliberately (`docker buildx imagetools inspect python:3.12-slim`), then let CI rebuild and scan.
- **Prompts and models:** run the live evaluation before and after (see `Docs/EVALS.md`).

## Secret rotation

| Secret | Rotation |
|---|---|
| `OIDC_CLIENT_SECRET` | Create a new secret at the provider, update `.env`, `docker compose up -d oauth2-proxy`, then delete the old one. |
| `OAUTH2_PROXY_COOKIE_SECRET` | Replace and restart oauth2-proxy; everyone signs in again. |
| `POSTGRES_PASSWORD` | `ALTER USER toolsmith PASSWORD '...'` in PostgreSQL, update `.env`, restart backend and migrate. |
| Model API keys | Rotate at the provider, update `.env`, restart the backend. |
| Tenant API keys | Admins revoke (`DELETE /api/v1/api-keys/{id}`) and create new ones; keys can carry an expiry. |

If a secret leaks, rotate it first, then check the audit chains and the access logs for its use.

## Incident checklist

1. Contain: suspend the tenant (set `status = 'suspended'` in `tenants`) or revoke the API key; stop the backend if the sandbox boundary is in doubt.
2. Preserve: take a backup and copy the logs before changing anything else.
3. Investigate: audit chains (`verify_audit.py`), access logs by `request_id`, `sandbox_execution` audit records (code and input digests).
4. Recover: rotate secrets, restore from a backup if data was altered, and record what happened.
