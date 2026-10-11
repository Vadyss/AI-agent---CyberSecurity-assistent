<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Token and cost tracking

Every model call is recorded with its tenant, run, role, model, tokens, cost and prompt version. The browser shows the records in the **Model usage** panel of a run, and the run comparison uses the totals. The payload is the `llm_usage` event described in [EVENTS.md](EVENTS.md).

## Provider and call sites

The default path is an Apify Standby relay → Apify OpenRouter proxy → model provider (see [LLM_PROVIDERS.md](LLM_PROVIDERS.md)). The application uses **HTTPX**, not an OpenAI or Anthropic SDK; the legacy synchronous compatibility function uses **requests**. Requests use the existing `/chat/completions` path and bearer header. There is no streaming. [OpenRouter usage documentation](https://openrouter.ai/docs/cookbook/administration/usage-accounting) confirms that non-streaming completions include native token usage, cost and cache information automatically; no additional request option is needed.

| Call site | Step | Configured model | Additional calls |
|---|---|---|---|
| `backend/orchestrator/planner.py` → `Planner.propose` | planner | anthropic/claude-sonnet-5.5 | Existing proposal retries and JSON repair |
| `backend/orchestrator/forge.py` → `Forge.build` | forge | anthropic/claude-sonnet-5.5 | Existing build retries and JSON repair |
| `backend/orchestrator/rule_author.py` → `RuleAuthor.draft` | rule_author | anthropic/claude-sonnet-5.5 | Existing proposal retries and JSON repair |
| `backend/orchestrator/summarizer.py` → `Summarizer.summarize` | summary | anthropic/claude-sonnet-5.5 | Existing JSON repair; template fallback is not an LLM call |
| `backend/examiner/examiner.py` → `Examiner.generate` | examiner | deepseek/deepseek-v4.1-flash | Optional independent examiner; second generation attempt |
| `backend/orchestrator/llm.py` → `json_chat` | calling role | role's model | Initial request and optional JSON repair |
| `backend/orchestrator/llm.py` → `HttpLlmClient.chat` | calling role | role's model, or fallback | Every HTTP attempt, including timeout/429/5xx and empty-output length retries |
| `backend/orchestrator/llm.py` → `ask` | legacy | main/explicit model, or fallback | Synchronous compatibility requests and retries |
| `backend/scripts/llm_smoke.py` | planner | configured planner model | Standalone diagnostic using the same client |
| `backend/orchestrator/llm_mock.py` → `MockLlm.chat` | calling role | mock | No network requests; tokens explicitly estimated and inference charge zero |

Existing fallback behavior remains: two consecutive provider failures switch later calls to `deepseek/deepseek-v4.1-flash`. Each record uses the actual response model when supplied, otherwise the requested model. Individual role overrides remain supported; unconfigured prices produce warnings rather than guessed rates.

## Verified prices

Prices are decimal strings in `backend/orchestrator/pricing.json`. Update rates, official sources and verification dates manually, then restart the backend to reload its cached pricing configuration; no price lookup requests are made during a run. USD per one million tokens:

| Model | Input | Cached input | Output | Official source | Verification |
|---|---:|---:|---:|---|---|
| anthropic/claude-sonnet-5.5 | $2.00 | $0.20 | $10.00 | [Anthropic official pricing](https://www.anthropic.com/claude-sonnet-5-5); [Apify billing rules](https://apify.com/apify/openrouter) | 2026-10-09 |
| deepseek/deepseek-v4.1-flash | provider-reported only | provider-reported only | provider-reported only | [DeepSeek official pricing](https://api-docs.deepseek.com/quick_start/pricing/); [Apify billing rules](https://apify.com/apify/openrouter) | Provider-reported cost only (`usage.cost` from OpenRouter); no fallback rates are calculated |

The configured routing references are retained separately for traceability; prices are sourced from the model providers' official pages, not third-party comparison sites. Claude's documented five-minute cache-write reference rate is also stored ($2.50/M). DeepSeek's direct peak/off-peak rates are not substituted for OpenRouter routing: direct rates and routed endpoint listings differ, and the actual selected endpoint is not fixed in this application. No DeepSeek fallback rate was guessed. Missing/unverified pricing generates an English warning in logs and in the usage panel, including when a valid provider cost is available.

**Reported `usage.cost` always takes precedence.** Published-rate calculations are visibly labelled estimates and are only enabled for the verified configured Apify relay path. For another compatible provider/base URL, missing reported cost remains unknown. Calculations require the applicable token/cache counts and prices. Cached input is removed from regular input before applying its rate, and output/reasoning is not double-counted. All monetary arithmetic uses `Decimal`; the new event and usage file serialize exact decimal strings. Existing `summary.stats.cost_usd` remains a JSON number for compatibility.

The reported amount is **model inference cost**, not an exact Apify invoice. [Apify's official proxy documentation](https://apify.com/apify/openrouter) describes per-call paid-plan rounding up to $0.00001 and a free-plan multiplier. Account-plan markup and relay hosting cannot be inferred from completion usage, and are explicitly excluded in the UI. This application does not guess the account plan or infrastructure charges.

## Records, events and files

Tracking lives in `llm.py`, around the actual HTTP request rather than around only successful parsing. Successful responses, provider error responses with usage, transport failures, length retries, JSON repairs and mock calls each get a separate record. Missing API credentials or an already exhausted call budget do not create phantom provider attempts. Existing retry schedules, model fallback and call budgets are unchanged.

Each content-free record includes run ID, call ID, step, role iteration, request attempt, actual model, input/output/cached/cache-write/total tokens, decimal cost, currency, cost source, duration in milliseconds, UTC timestamp, token-estimation flag, retry flag, HTTP outcome and a safe warning. JSON repair is counted as a retry even if its request attempt is 1. Proposal attempts greater than 1 also count as retries; a record is counted only once. Examiner repair iterations are explicitly attributed without changing examiner prompts or inputs.

Real provider token counts are never replaced by estimates. If only a total is reported, it is retained and unavailable splits remain null. If no usable token counts exist, an offline UTF-8 byte-level tokenizer counts content bytes and marks the record estimated. This is deliberately conservative and **not the model's native tokenizer**; chat framing, unknown output on transport failures and unknown cache savings cannot be reconstructed. Reasoning text can contribute to the fallback token count but is never used as an answer or retained in the ledger.

`llm_usage` carries the records (see [EVENTS.md](EVENTS.md), section 6.9):

- `kind: call`: `record`, running `totals`, and `summary: null` after each request attempt.
- `kind: summary`: `record: null`, final `totals`, and a structured cost summary after LLM work finishes, before approval or run failure.

Existing event sequencing, persistence-before-broadcast and terminal-event ordering are preserved.

The public gatekeeper storage boundary provides a new isolated metadata store in `registry.py`; agent code still cannot write runtime files directly. Atomic snapshots are saved to `runs/<run_id>/usage.json` in the tenant's data directory after every call and at completion, with mode 0600 and validated run IDs. Symlink storage paths are refused. This store does not modify candidates, skills, recipes, policy, audit-chain records or datasets. Storage failures are logged using the run ID only. Normal API runs bind their ledger once and reset it at completion. Standalone smoke/legacy diagnostics without a run binding retain usage only in memory; they are not API runs.

## Reading the UI and final summary

Open a run and look at **Model usage**. Each table row is one request attempt. Step and iteration identify the agent task, model identifies the actual provider response, and input/output columns show native counts when available. Cached counts, duration, timestamp and cost source are under **Call N details**. Retries, failed requests and estimated values are labelled. The server supplies the running total; the browser does not sum money with floating-point arithmetic.

After LLM work completes, **Cost by step** shows each step's cost, one-decimal percentage share and retry count. The summary identifies the most expensive step, total retries, average cost and one to three observations. Averages use persisted completed usage snapshots with available total costs, including calculated estimates, and display the denominator. They can therefore span backend restarts. Zero-call runs have exact zero totals and participate in the average. No most-expensive step is named when all costs are zero or any cost is unknown.

A missing value appears as **—**. If a call's cost is unknown, the total cost stays unknown and **Known subtotal** is shown separately. Partial token totals behave similarly. Unknown-cost runs are excluded from the cost average rather than treated as zero. A valid provider cost remains usable even when individual token splits are unavailable. Old runs without usage events keep their existing summary/statistics and receive a one-line explanation in the new panel.

## Limitations

- DeepSeek uses provider-reported cost only (`usage.cost`); with no reported cost the call's cost stays unknown and no rate is guessed.
- Inference usage does not establish the final Apify invoice, free-plan multiplier or relay hosting cost.
- Missing provider usage cannot establish exact native tokens or unknown output/billing; estimates and partial totals are explicitly marked.
- Usage records survive restarts; API run history remains process-local as before. Standalone diagnostics are not persisted API runs.
- Accounting is verified with the real client code and scripted provider responses; the test suite makes no paid model calls.

