# llm-relay

Python HTTP Actor for Apify Standby. It listens on the platform's
`ACTOR_WEB_SERVER_PORT`; `APIFY_TOKEN` comes from the Actor environment.

- `GET /health`: `{"status":"ok"}`.
- `GET /`: the same response, including the Standby readiness probe.
- `POST /v1/chat/completions`: forwards the request bytes to
  `https://openrouter.apify.actor/api/v1/chat/completions`, using the Actor's
  `APIFY_TOKEN` as a Bearer token. The upstream body, content headers and HTTP
  status are preserved; hop-by-hop headers are removed. Responses are buffered,
  including SSE requests, and the entire upstream operation has a 180 s deadline.

Call the actual Standby URL shown in the Actor's **Endpoints** tab using
`Authorization: Bearer <your Apify API token>`. The platform authenticates the
caller. The relay replaces this header with its environment token upstream.
Tokens, prompts and request/response contents are never logged. Transport errors
return generic HTTP 502; timeouts return HTTP 504. There are no automatic retries.

Deploy from this directory with `apify login` and `apify push`.
`usesStandbyMode: true` in `.actor/actor.json` enables Standby on push.
Detailed commands and a curl test are in `Docs/LLM_PROVIDERS.md` in the repository.
