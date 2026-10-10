<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Frankenstein

Frankenstein is an agent that helps a security analyst write detection rules. The analyst describes an attack in plain language (for example, "detect SSH brute force"). The agent plans the work and checks which skills it already has. If a skill is missing, such as a log parser or a sliding-window aggregation, the agent writes it, tests it in an isolated sandbox, and adds it to a growing skill library. It then drafts a rule, tunes it on one dataset, validates it on a second, independent dataset, and asks the analyst to approve or reject it. Nothing is installed until a human approves.

## Authors

Adam Krúpa and Ondra Csajka. All rights reserved.

This project is proprietary. Use, copying, modification, and distribution require written consent from both authors. See [LICENSE](LICENSE).

## Architecture

```
Browser (Next.js, static export served by nginx)
   │  HTTP + WebSocket
   ▼
Orchestrator (FastAPI, backend/orchestrator)  ──►  LLM (Apify relay → OpenRouter)
   │  proposals only
   ▼
Gatekeeper (backend/gatekeeper)  ──►  Sandbox (separate container, no network)
```

Each LLM-backed role only proposes. The gatekeeper decides and is the only component that writes skills, rules, and audit records.

| Component | Where | Job |
|---|---|---|
| Planner | `backend/orchestrator/planner.py` | Breaks the request into steps and decides which skills are needed. |
| Forge | `backend/orchestrator/forge.py` | Writes missing skills (manifest, code, tests), with up to 3 attempts per skill. |
| Rule author | `backend/orchestrator/rule_author.py` | Drafts the detection rule (a recipe) from the available skills. |
| Summarizer | `backend/orchestrator/summarizer.py` | Writes the short run summary. Falls back to a template if the model fails. |
| Gatekeeper | `backend/gatekeeper/` | Deterministic checks: policy, static code analysis, plan and recipe validation, metrics, skill registry, audit log. |
| Examiner (optional) | `backend/examiner/` | Generates independent test data for custom attack types. Off by default (`EXAMINER_ENABLED=false`). |
| Sandbox | `sandbox/` | Runs untrusted skill code in a restricted subprocess inside a container with no internet access. |
| Policy | `backend/policy/policy.yaml` | Immutable rules: allowed imports, forbidden calls, size and time limits, precision and recall thresholds. |

Untrusted text (logs, requests, test output) is wrapped as data before it reaches a model, and every proposal goes through the gatekeeper before anything runs.

## Setup

Requirements: Docker with Compose. For local development without Docker you also need Python 3.12 and Node.js 22.

1. Create your local configuration:

   ```bash
   cp .env.example .env
   ```

   Fill in `APIFY_TOKEN` (or `LLM_API_KEY`). `.env` is git-ignored. `.env.example` only lists variable names, so never commit real values.

2. Start the stack from the project root:

   ```bash
   docker compose up --build
   ```

3. Open `http://localhost:3000`. Check the backend at `http://127.0.0.1:8000/health`.

**Demo without any LLM calls** (mock model):

```bash
docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build
```

### Useful settings

- `NEXT_PUBLIC_API_BASE`: backend address used by the browser (default `http://127.0.0.1:8000`). It is baked in at build time, so rebuild the frontend after changing it.
- `CORS_ORIGINS`: allowed frontend origins (default `http://localhost:3000,http://127.0.0.1:3000`). The WebSocket checks the `Origin` header against the same list.
- `BACKEND_BIND_HOST`: interface the backend port is published on (default `127.0.0.1`).
- `LLM_MODEL*`, `LLM_MAX_CALLS_PER_RUN`, `RUN_TIMEOUT_S`: model choice and per-run limits. See `.env.example` for the full list.

### Local development

```bash
python -m pip install -r backend/requirements.txt
python -m uvicorn orchestrator.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
```

A full run needs the sandbox, so use the Docker stack, or point `SANDBOX_URL` at a running sandbox and set a writable `DATA_DIR`. Then, in a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

More frontend details are in [frontend/README.md](frontend/README.md).

### Tests

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt -r ../sandbox/requirements.txt
python -m pytest
```

Frontend tests: `cd frontend && npm test`. CI (GitHub Actions) runs the backend, frontend, and an isolated Docker integration job, and deploys from `main` only.

## Cost tracking

Every LLM call goes through the client in `backend/orchestrator/llm.py` and is recorded in a per-run ledger.

- The cost reported by the provider (`usage.cost` from OpenRouter) is always used when present.
- `backend/orchestrator/pricing.json` holds published per-million-token rates and is only a fallback when the provider does not report a cost. Models marked `provider_reported_only` have no fallback rates. Update the file by hand and restart the backend.
- If any call's cost is unknown, the run total is shown as unknown (with a known subtotal), never as a partial sum.
- All money is handled as `Decimal` and sent to the UI as exact decimal strings.
- Each run writes `<DATA_DIR>/runs/<run_id>/usage.json` with per-call records and a final summary: cost by step, retries, and an average over recent real runs (mock runs are excluded).
- The UI shows live usage in the "Model usage" panel of each run.

Costs cover model inference only. They exclude Apify plan markup and relay hosting. Details: [COST_TRACKING.md](Docs/backend/COST_TRACKING.md).

## Known limitations

- **No authentication.** Anyone who can reach the backend can start runs, approve or reject rules, and read events over HTTP and WebSocket. CORS is not access control.
- **The backend binds to 127.0.0.1 by default.** Only change `BACKEND_BIND_HOST` on a trusted network, or put an authenticating reverse proxy in front.
- Run history and events are kept in memory and are lost on restart. Approved skills, rules, the audit log, and usage files are stored on the `/data` volume.
- Only one run can be active at a time. A run waiting for approval blocks new runs until it is approved or rejected.
- The sandbox's Python-level guards are defense in depth. The container boundary is what actually isolates untrusted code.

## More documentation

[ARCHITECTURE.md](Docs/backend/ARCHITECTURE.md), [PROGRESS.md](Docs/backend/PROGRESS.md), [REPORT.md](Docs/backend/REPORT.md), [SECURITY_REVIEW.md](Docs/backend/SECURITY_REVIEW.md), [LLM_RELAY.md](Docs/backend/LLM_RELAY.md), [LLM_LIVE_TEST.md](Docs/backend/LLM_LIVE_TEST.md).
