<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Language model providers

Toolsmith talks to a language model through one OpenAI-compatible `POST {LLM_BASE_URL}/chat/completions` call (no streaming, no vendor SDK). Which provider it uses is set by `LLM_PROVIDER`. Only the backend holds model credentials; the sandbox and the browser never see them.

| `LLM_PROVIDER` | Needs | Use it for |
|---|---|---|
| `mock` | nothing | The demo, tests and CI. Scripted answers for the sample scenarios; no network. Refused in production. |
| `openai_compatible` | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Any provider with an OpenAI-style chat completions API, for example OpenRouter. `LLM_BASE_URL` ends at the API prefix (usually `/v1`), not at `/chat/completions`. |
| `apify` | `APIFY_TOKEN` (or `LLM_API_KEY`) and a reachable relay | The original setup: an [Apify](https://apify.com) Standby Actor (`apify/llm-relay`) that forwards requests to Apify's OpenRouter endpoint. |

## Using a real model

The model names in the defaults use OpenRouter's `vendor/model` form. A minimal `.env` for OpenRouter:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=<your key>
LLM_MODEL=anthropic/claude-sonnet-5.5
LLM_MODEL_FALLBACK=<a cheaper model used when the primary one keeps failing>
```

The demo overlay (`docker-compose.demo.yml`) always forces the mock model and blanks all keys, so a real model needs the development stack (`sh scripts/dev-up.sh`, see the [README](../README.md)) or a production deployment ([OPERATIONS.md](OPERATIONS.md)). Check the connection without running the whole pipeline:

```bash
set -a; . ./.env; set +a                    # llm_smoke.py reads the shell environment, not .env
python backend/scripts/llm_smoke.py         # one planner call; prints the model and token count (needs backend/requirements.txt)
```

`LLM_BASE_URL` defaults to the author's own relay Actor. **Replace it** with your relay (below) or another provider; the default is not a public service.

## Settings that matter

| Variable | Default | Meaning |
|---|---|---|
| `LLM_MODEL` | `anthropic/claude-sonnet-5.5` | Model for the planner, forge, rule author and summarizer. Each role can be overridden with `LLM_MODEL_PLANNER`, `_FORGE`, `_RULE`, `_SUMMARY`. |
| `LLM_MODEL_FALLBACK`, `LLM_MODEL_EXAMINER` | `deepseek/deepseek-v4.1-flash` | Fallback model after repeated failures; model of the optional examiner. |
| `LLM_REASONING_EFFORT` | `low` in Compose | `minimal` to `max`. In live tests, reasoning models with a small token budget often returned empty or truncated JSON; `low` with `LLM_MAX_TOKENS=16000` worked reliably. |
| `LLM_MAX_TOKENS` / `LLM_MAX_TOKENS_CAP` | `16000` / `32000` | Output budget per call, and the ceiling used when a call is cut off and retried. |
| `LLM_TIMEOUT_S` | `180` | Per request. |
| `LLM_MAX_CALLS_PER_RUN` | `25` | Hard cap; retries and JSON repairs count. |

Costs and budgets are described in [COST_TRACKING.md](COST_TRACKING.md).

## The Apify relay

`apify/llm-relay/` is a small Python HTTP Actor. It exposes `POST /v1/chat/completions` and forwards the body to `https://openrouter.apify.actor/api/v1/chat/completions` with the Actor's own `APIFY_TOKEN` as the bearer token. The upstream status and body are returned unchanged (hop-by-hop headers removed); the whole upstream call has a 180 s deadline (timeout → `504`, connection error → `502`). SSE responses are buffered, not streamed. Tokens, prompts and bodies are never logged. `GET /` and `GET /health` return `{"status":"ok"}`.

Callers authenticate to the Standby URL with `Authorization: Bearer <Apify API token>`; the platform checks it. Apify Standby allows 5 minutes until the first response, so the 180 s deadline fits inside the platform limit, but a cold start adds latency.

Deploy it with Node.js 22+ and the Apify CLI:

```bash
npm install -g apify-cli
apify login
cd apify/llm-relay
apify push                 # builds the Actor; usesStandbyMode is set in .actor/actor.json
```

Copy the Standby URL from the Actor's **Endpoints** tab into `LLM_BASE_URL` (ending in `/v1`). Test it with one completion:

```bash
curl --fail-with-body --max-time 300 \
  -H "Authorization: Bearer ${APIFY_TOKEN:?export APIFY_TOKEN first}" \
  -H 'Content-Type: application/json' \
  --data '{"model":"openrouter/auto","messages":[{"role":"user","content":"Reply with OK only."}],"max_tokens":16,"stream":false}' \
  "${RELAY_URL%/}/v1/chat/completions"
```

To run it locally (Docker, `APIFY_TOKEN` exported): `docker build -t llm-relay-local apify/llm-relay && docker run --rm -p 127.0.0.1:4321:4321 -e APIFY_TOKEN -e ACTOR_WEB_SERVER_PORT=4321 llm-relay-local`, then open `http://127.0.0.1:4321/health`. Its tests (`cd apify/llm-relay && python -m pytest tests`) run offline.

## Recorded live runs (2026-10-09)

The pipeline was exercised against `anthropic/claude-sonnet-5.5` through the relay: 16 real runs over four scenarios (password spraying, a second run reusing the skill it had just built, brute force with prompt injection in the logs, and a web directory scan that needs a new parser). 10 runs were approved with precision and recall of 1.0 on both datasets, and the follow-up run reused the approved `distinct_count_window` skill instead of building it again. The 6 failures all came from the first, untuned configuration (small token budget, no reasoning effort): the model returned empty or truncated JSON, and the runs ended safely as `FORGE_FAILED` or `LLM_ERROR`. After the prompt and token-budget fixes, every scenario passed twice in a row. These are synthetic datasets, so the numbers show that the loop works, not how well it detects attacks in real traffic. See [EVALS.md](EVALS.md).
