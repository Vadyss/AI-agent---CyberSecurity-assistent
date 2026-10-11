<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Toolsmith

Toolsmith is an agent for security analysts that builds its own tools. An analyst describes an attack in plain language, for example "detect SSH password spraying". The agent plans the work and checks which tools it already has. If one is missing, such as a log parser or a sliding-window aggregation, it writes the tool, tests it in an isolated sandbox, drafts a detection rule, tunes it on one dataset and validates it on a second, independent one. A human reviews the code and approves; only then do the new tools join the team's library and get reused by later runs.

Built for the hackathon brief "Frankenstein" (a self-building agent) and hardened into a multi-tenant service.

## Authors

Adam Krúpa and Ondra Csajka. All rights reserved.

This project is proprietary. Use, copying, modification, and distribution require written consent from both authors. See [LICENSE](LICENSE).

## How it works

```
Browser / API client ── oauth2-proxy (OIDC sign-in) ── frontend + API (/v1)
                                                         │  proposals only
                        Model provider ◄── planner, forge, rule author, summarizer
                                                         │
                                         gatekeeper (per tenant) ── sandbox (no network, private UID per job)
                                                         │
                                  PostgreSQL + per-tenant tool library and audit chain
```

Language models only propose. A deterministic gatekeeper enforces [the policy](backend/policy/policy.yaml), tests every tool and measures every rule. A human approves. Every approval and every sandbox execution is recorded in the tenant's hash-chained audit log. Details: [architecture](Docs/ARCHITECTURE.md), [security](SECURITY.md), [threat model](Docs/THREAT_MODEL.md).

## Quick start

Requirements: Docker with Compose. For the scripts: a POSIX shell.

**Demo (no sign-in, mock model, localhost only):**

```bash
POSTGRES_PASSWORD=demo docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

Open `http://localhost:3000`.

**Development with real sign-in** (oauth2-proxy and a throwaway identity provider; mock model unless you set `LLM_PROVIDER`):

```bash
sh scripts/dev-up.sh
```

Open `http://localhost:4180` and sign in with the pre-filled development account (the first administrator). `python scripts/auth_smoke.py` checks the whole sign-in chain.

**Production:** follow the [operations guide](Docs/OPERATIONS.md): register an OIDC client, fill `.env`, then `docker compose up --build -d --wait` behind a TLS reverse proxy. The backend refuses to start with an incomplete or unsafe configuration.

## Using the API

The API is versioned under `/v1`; the contract is [Docs/api/openapi.json](Docs/api/openapi.json).

```bash
# Programmatic access with a tenant API key (created by an admin: POST /api/v1/api-keys)
curl -H "X-API-Key: tsk_..." https://toolsmith.example.com/m2m/v1/runs
curl -H "X-API-Key: tsk_..." -H "Content-Type: application/json" \
     -d '{"request": "Detect SSH password spraying"}' https://toolsmith.example.com/m2m/v1/runs
```

Roles: `viewer` reads; `analyst` also starts, cancels and retries runs, reviews candidate code and approves or rejects; `admin` also manages members, invitations, API keys, quotas and data deletion.

## Cost tracking and quotas

Every model call is recorded with its tenant, run, model, tokens, cost and prompt version.

- The cost reported by the provider (`usage.cost`) is used when present; `backend/orchestrator/pricing.json` holds published rates as a fallback. If any call's cost is unknown, totals say so instead of showing a partial sum. Money is handled as `Decimal`.
- Each tenant has a monthly model budget and a daily run quota (`DEFAULT_MONTHLY_BUDGET_USD`, `DEFAULT_MAX_RUNS_PER_DAY`, adjustable per tenant). The budget is checked before every model call. `GET /v1/usage` shows the month so far.
- Costs cover model inference only; they exclude Apify plan markup and hosting. Details: [COST_TRACKING.md](Docs/backend/COST_TRACKING.md).

## Development

```bash
sh backend/scripts/test.sh            # backend and sandbox tests (Docker, SQLite and PostgreSQL)
cd frontend && npm ci && npm test     # frontend tests
python -m evals.run --provider mock   # from backend/: evaluation harness, see Docs/EVALS.md
```

CI runs lint (ruff, mypy), secret scanning, dependency and image vulnerability scans, SBOM generation, all test suites, an isolated Docker integration test and a sign-in chain test. Deployment from `main` needs all of them.

## Known limitations

- **One host.** The design targets a single machine with Docker Compose. Rate limits are per process; the queue workers run inside the backend process.
- **Shared kernel.** Generated code runs as a private, unprivileged UID without network or capabilities, but shares the host kernel. gVisor is the next step if your risk profile demands it ([D-005](Docs/DECISIONS.md)).
- **Approval does not survive a restart.** A run awaiting approval when the backend restarts is closed as failed and must be started again; runs that were executing are retried automatically.
- **A run awaiting approval blocks its tenant's queue**, because its candidate tools could conflict with a new run's.
- **Evaluation:** the evaluation suite has five cases and no recorded live-model baseline yet ([EVALS.md](Docs/EVALS.md)).
- **Frontend:** the web UI predates sign-in; the changes it needs are listed in [FRONTEND_CHANGES.md](Docs/FRONTEND_CHANGES.md). Until then it works only in the demo.

## Documentation

| Document | Contents |
|---|---|
| [Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md) | Components, deployment and run lifecycle diagrams |
| [Docs/DECISIONS.md](Docs/DECISIONS.md) | Architecture decisions with reasons and trade-offs |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, security design summary |
| [Docs/THREAT_MODEL.md](Docs/THREAT_MODEL.md) | STRIDE threat model of the generated-code path |
| [Docs/PRIVACY.md](Docs/PRIVACY.md) | GDPR notes: stored data, retention, data-subject rights |
| [Docs/OPERATIONS.md](Docs/OPERATIONS.md) | Deployment, configuration, observability, backups, updates |
| [Docs/EVALS.md](Docs/EVALS.md) | Measuring the model-backed roles |
| [Docs/FRONTEND_CHANGES.md](Docs/FRONTEND_CHANGES.md) | What the frontend must change for the new API |
| [Docs/api/openapi.json](Docs/api/openapi.json) | The API contract |
| [Docs/backend/ARCHITECTURE.md](Docs/backend/ARCHITECTURE.md) | Detailed design of the original agent and gatekeeper |
