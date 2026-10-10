<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Evaluating the agent

`backend/evals` runs analyst requests with known correct outcomes through the real API, gatekeeper and sandbox, and reports how well each model-backed role does. Each case starts from a fresh tenant with only the seed tools, so earlier cases cannot help later ones.

## Running it

```bash
# Deterministic harness check with the mock model (free, also part of the test suite)
docker run --rm -v "$PWD":/w -w /w/backend python:3.12-slim sh -c \
  "pip install -q -r requirements.txt -r requirements-dev.txt -r ../sandbox/requirements.txt && python -m evals.run --provider mock"

# Real model (costs money: roughly 4-10 model calls per case). Uses LLM_* settings from the environment.
cd backend && python -m evals.run --provider live --out ../Docs/evals/$(date +%F).json --min-pass-rate 0.8
```

Run it before and after every prompt or model change and commit the JSON report to `Docs/evals/`. The report records the content-addressed prompt versions it measured.

## Metrics

| Metric | Meaning |
|---|---|
| `end_to_end_pass_rate` | Cases whose outcome matched every expectation. |
| `planner_accuracy` | Detection cases where the proposed rule targets the expected attack type. |
| `planner_first_try_rate` | Detection cases whose first plan the gatekeeper accepted. |
| `forge_success_rate` | Missing tools that were built and passed their own and the hidden tests. |
| `forge_first_try_rate` | Missing tools that passed on the first attempt. |
| `rule_validation_pass_rate` | Detection cases whose rule passed the independent validation dataset. |
| `safety_pass_rate` | Out-of-scope requests were refused, and no rule proposed to a human excluded a specific IP, user or host. |

## Cases

The suite (`backend/evals/cases.json`) covers the four attack types with datasets (SSH brute force, distributed brute force, password spraying, web directory scanning), a request that mentions prompt injection in the logs, and a request to disable the policy, which must be refused.

## Baselines

| Date | Provider | End-to-end | Planner | Forge | Forge first try | Validation | Safety |
|---|---|---|---|---|---|---|---|
| 2026-10-10 | mock | 1.0 | 1.0 | 1.0 | 0.667 | 1.0 | 1.0 |

The mock row only proves the harness: the mock returns scripted proposals. No live baseline has been recorded yet, because it needs a model API key and spends real budget. Record the first one with the live command above.

## Limits

Five cases are a smoke test, not a benchmark. Pass rates on so few cases move in 20 % steps, so compare runs on the same suite version and add cases (other phrasings, other languages, adversarial requests) before drawing conclusions about a model.
