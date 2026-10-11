<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Toolsmith

Toolsmith is an agent for security analysts that builds its own tools. An analyst describes an attack in plain language, for example "detect SSH password spraying". The agent plans the work and checks which tools its library already has. If one is missing, such as a log parser or a sliding-window aggregation, it writes the tool, tests it in an isolated sandbox, drafts a detection rule, tunes the rule on one dataset and validates it on a second, independent one. A human reviews the code and the evidence and approves; only then do the new tools join the library and get reused by later runs. The point of the design is that a language model is allowed to *propose* code but never to *decide* anything: a deterministic gatekeeper checks, measures and records, and a human approves.

Toolsmith started as a hackathon project on the brief "Frankenstein" (a self-building agent), which is why the web UI is still titled *Frankenstein*. It was then hardened into a multi-tenant backend with sign-in, quotas, auditing and an operations guide.

> **Warning: demo mode has no sign-in and must never be exposed to the internet.**
> The demo stack (`docker-compose.demo.yml`) disables authentication: anyone who can reach it can start runs, approve code and read everything. It binds to localhost, uses a mock model and a throwaway database. Do not publish its ports, put it behind a tunnel or run it on a shared network. For any shared or public use, run the full mode with sign-in (see [Known limitations](#known-limitations)).

![The request editor](Docs/screenshots/home.png)

## Features

- **Plain-language requests.** Four attack types ship with datasets: SSH brute force, distributed brute force, password spraying and web directory scanning. Requests the policy forbids (such as disabling checks) are refused.
- **Self-extending tool library.** Parsers, aggregations and enrichments are generated on demand, versioned, fingerprinted with SHA-256 and reused by later runs.
- **Evidence before approval.** Every tool must pass its own tests and independent hidden tests. A rule is tuned on one dataset and measured once on a held-out one, against precision and recall thresholds set by policy, not by the model.
- **Human in the loop.** The analyst reads the generated code and the metrics, then approves or rejects. Nothing is installed before that.
- **Live activity stream.** Every step is an event stored before it is sent, so the browser can replay a run after a reconnect or a restart.
- **Cost tracking.** Every model call is recorded with tokens, cost and prompt version; each tenant has a monthly budget and a daily run quota ([COST_TRACKING.md](Docs/COST_TRACKING.md)).
- **Multi-tenant platform.** OIDC sign-in through oauth2-proxy, roles (viewer, analyst, admin), invitations, API keys, a durable run queue in PostgreSQL, rate limits, metrics, tracing, backups and GDPR tooling.

| Run with an approval request | Skill library |
|---|---|
| ![Approval](Docs/screenshots/approval.png) | ![Skills](Docs/screenshots/skills.png) |

## Architecture

```
Browser / API client ── oauth2-proxy (OIDC sign-in) ── frontend + API (/v1)
                                                         │  proposals only
                        Model provider ◄── planner, forge, rule author, summarizer
                                                         │
                                         gatekeeper (per tenant) ── sandbox (no network, private UID per job)
                                                         │
                                  PostgreSQL + per-tenant tool library and audit chain
```

The full component and run-lifecycle diagrams are in [Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md); the reasons behind each choice are in [Docs/DECISIONS.md](Docs/DECISIONS.md).

## Security properties

- **Model output is a proposal.** A deterministic gatekeeper enforces [the policy](backend/policy/policy.yaml), tests every tool and measures every rule; the orchestrator cannot import the gatekeeper's internals (enforced by tests).
- **Untrusted data stays data.** Logs and analyst text reach the model only inside escaped blocks; the UI renders everything as plain text.
- **Sandboxed execution.** Generated code is statically checked, then runs in a container with no network, a read-only filesystem, no capabilities, and a private unprivileged UID per job with CPU, memory, process and file limits.
- **Identity and tenancy.** Identity comes only from tokens signed by your identity provider (or hashed API keys). Tenants are isolated in every query, file path and event stream.
- **Audit.** Every approval and sandbox execution lands in a per-tenant SHA-256 hash chain that `scripts/verify_audit.py` can verify.
- **Supply chain.** Hash-pinned dependencies, digest-pinned base images, and CI jobs for secret scanning of the whole history, dependency and image vulnerability scans and SBOMs.

Details: [SECURITY.md](SECURITY.md), [Docs/THREAT_MODEL.md](Docs/THREAT_MODEL.md), [Docs/PRIVACY.md](Docs/PRIVACY.md).

## Quick start: demo mode

You need Docker with Compose v2 and about 2 GB of free disk space for the images. No model account or API key is needed: the demo uses a scripted mock model.

1. Clone the repository and enter it.

   ```bash
   git clone https://github.com/Vadyss/Hackathon---From-Dusk-Till-Dawn.git toolsmith
   cd toolsmith
   ```

2. Start the demo stack. The first build takes a few minutes.

   ```bash
   POSTGRES_PASSWORD=demo docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
   ```

3. Open <http://localhost:3000>. The status pill at the top right should read **Connected**.
4. Click the example **Password spraying** and then **Build detection**. The mock model needs well under a minute; the activity list shows the plan, the tool the agent builds and its tests, the drafted rule and the tuning and validation results.
5. When the run reaches **Needs review**, open the code and the metrics, then choose **Approve and install**. The new tool appears in the **Skill library**.
6. Start **Distributed brute force**. This time the agent reuses the tool you approved instead of building it.
7. Stop with `Ctrl+C`; remove the demo data with `docker compose -f docker-compose.yml -f docker-compose.demo.yml down -v`.

The backend is also published on `http://127.0.0.1:8000` (localhost only).

### Which model provider and API key?

| Mode | Model | Key needed |
|---|---|---|
| Demo | Mock (scripted, offline) | None |
| Development stack with sign-in | Mock unless you set `LLM_PROVIDER` | None for the mock |
| Real model | Any OpenAI-compatible provider (for example OpenRouter), or an Apify relay you deploy from `apify/llm-relay` | `LLM_API_KEY` (with `LLM_BASE_URL` and `LLM_MODEL`) or `APIFY_TOKEN` |

The demo always forces the mock model. To try a real model, start the development stack with your provider settings in `.env`, for example:

```bash
cp .env.example .env     # then set LLM_PROVIDER=openai_compatible, LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
sh scripts/dev-up.sh
```

Open <http://localhost:4180> and sign in as `dev@example.test` (a throwaway identity provider accepts any user, so keep this stack on localhost as well). Provider details, settings and the relay are in [Docs/LLM_PROVIDERS.md](Docs/LLM_PROVIDERS.md). Real runs spend money: roughly 3 to 9 model calls per run, bounded by `LLM_MAX_CALLS_PER_RUN` and the tenant budget.

## Using the API

The API is versioned under `/v1`; the contract is [Docs/api/openapi.json](Docs/api/openapi.json) and the event stream is described in [Docs/EVENTS.md](Docs/EVENTS.md).

```bash
# Programmatic access with a tenant API key (an admin creates keys: POST /api/v1/api-keys)
curl -H "X-API-Key: tsk_..." https://toolsmith.example.com/m2m/v1/runs
curl -H "X-API-Key: tsk_..." -H "Content-Type: application/json" \
     -d '{"request": "Detect SSH password spraying"}' https://toolsmith.example.com/m2m/v1/runs
```

Roles: `viewer` reads; `analyst` also starts, cancels and retries runs, reviews candidate code and approves or rejects; `admin` also manages members, invitations, API keys, quotas and data deletion.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy and Alembic, PostgreSQL 18 (SQLite in unit tests), Pydantic, Prometheus client and OpenTelemetry.
- **Sandbox:** a separate Python service on an internal Unix socket, no network.
- **Frontend:** Next.js 16 and React 19, exported as static files behind nginx.
- **Sign-in:** oauth2-proxy with any OIDC provider (a mock provider in the development stack).
- **Infrastructure:** Docker Compose, GitHub Actions (ruff, mypy, gitleaks, pip-audit, npm audit, trivy, CycloneDX SBOM, integration tests).
- **Models:** provider-agnostic through an OpenAI-compatible API; tested with Claude Sonnet 5.5.

## Repository layout

| Path | Contents |
|---|---|
| `backend/orchestrator/` | HTTP and WebSocket API, queue, pipeline, model roles and prompts, cost tracking |
| `backend/gatekeeper/` | Policy, static analysis, hidden tests, evaluation, tool registry, audit chain |
| `backend/identity/`, `backend/storage/` | OIDC verification, tenants, API keys, rate limits; SQLAlchemy models and migrations |
| `backend/examiner/` | Optional independent test-data generator |
| `backend/datasets/`, `backend/seed_skills/`, `backend/policy/` | Synthetic tuning and validation data, seed tools, the immutable policy |
| `backend/evals/` | Evaluation harness for the model-backed roles ([EVALS.md](Docs/EVALS.md)) |
| `backend/tests/` | Unit, integration, architecture and end-to-end tests |
| `sandbox/` | Isolated execution service |
| `frontend/` | Web UI and its tests |
| `apify/llm-relay/` | Optional model relay for Apify Standby |
| `scripts/` | Development stack, sign-in smoke test, backup and restore |
| `deploy/dev-idp/` | Login page of the development identity provider |
| `Docs/` | Architecture, decisions, threat model, privacy, operations, API, screenshots |

## Development

```bash
sh backend/scripts/test.sh            # backend and sandbox tests (Docker, SQLite and PostgreSQL)
cd frontend && npm ci && npm test     # frontend tests
ruff check backend sandbox apify scripts && (cd backend && mypy)   # versions are pinned in CI
```

The evaluation harness is described in [Docs/EVALS.md](Docs/EVALS.md). CI runs lint, secret scanning, dependency and image scans, SBOM generation, all test suites, an isolated Docker integration test and a sign-in chain test. Its `deploy` job targets the authors' own self-hosted runner; adapt or remove it for a fork.

**Production:** follow the [operations guide](Docs/OPERATIONS.md): register an OIDC client, fill `.env`, then `docker compose up --build -d --wait` behind a TLS reverse proxy. The backend refuses to start with an incomplete or unsafe configuration.

## Known limitations

- **The web UI does not yet support the full authenticated mode.** It was written against the first, unversioned API and works only in the demo. Using it with sign-in needs frontend changes F-1 to F-4 from [Docs/FRONTEND_CHANGES.md](Docs/FRONTEND_CHANGES.md): same-origin calls through oauth2-proxy, the `/v1` paths, handling of sign-in and permission errors, and the new run statuses and events. Until then, the authenticated backend is usable through its API and API keys. The remaining items (F-5 to F-12: onboarding, tenant switcher, administration screens and so on) are missing features rather than blockers.
- **Synthetic data.** The tuning and validation datasets are generated. They make the demo reproducible but say nothing about detection quality on real traffic.
- **One host.** The design targets a single machine with Docker Compose. Rate limits are per process, and the queue workers run inside the backend process.
- **Shared kernel.** Generated code runs as a private, unprivileged UID without network or capabilities, but shares the host kernel. gVisor is the next step if your risk profile demands it ([D-005](Docs/DECISIONS.md)).
- **Approval does not survive a restart.** A run awaiting approval when the backend restarts is closed as failed and must be started again; runs that were executing are retried automatically.
- **A run awaiting approval blocks its tenant's queue**, because its candidate tools could conflict with a new run's.
- **Thin evaluation.** The suite has five cases and no recorded baseline from the harness itself ([EVALS.md](Docs/EVALS.md)); a manual series of live runs is summarised in [LLM_PROVIDERS.md](Docs/LLM_PROVIDERS.md).
- **No default model endpoint.** `LLM_BASE_URL` must be set for any provider except the mock.

## Authors

Adam Krúpa & Ondra Csajka.

## License

[MIT](LICENSE) © 2026 Adam Krúpa & Ondra Csajka.

## Documentation

| Document | Contents |
|---|---|
| [Docs/ARCHITECTURE.md](Docs/ARCHITECTURE.md) | Components, deployment and run lifecycle diagrams |
| [Docs/DECISIONS.md](Docs/DECISIONS.md) | Architecture decisions with reasons and trade-offs |
| [SECURITY.md](SECURITY.md) | Reporting vulnerabilities, security design summary |
| [Docs/THREAT_MODEL.md](Docs/THREAT_MODEL.md) | STRIDE threat model of the generated-code path |
| [Docs/PRIVACY.md](Docs/PRIVACY.md) | GDPR notes: stored data, retention, data-subject rights |
| [Docs/OPERATIONS.md](Docs/OPERATIONS.md) | Deployment, configuration, observability, backups, updates |
| [Docs/LLM_PROVIDERS.md](Docs/LLM_PROVIDERS.md) | Model providers, settings, the Apify relay, recorded live runs |
| [Docs/COST_TRACKING.md](Docs/COST_TRACKING.md) | Token and cost accounting |
| [Docs/EVALS.md](Docs/EVALS.md) | Measuring the model-backed roles |
| [Docs/EVENTS.md](Docs/EVENTS.md) | The run event stream |
| [Docs/FRONTEND_CHANGES.md](Docs/FRONTEND_CHANGES.md) | What the frontend must change for the new API |
| [Docs/api/openapi.json](Docs/api/openapi.json) | The API contract |
