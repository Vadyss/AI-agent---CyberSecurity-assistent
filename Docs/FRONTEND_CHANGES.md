<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Changes the frontend needs

The backend became multi-tenant, authenticated and versioned on the `saas-hardening` branch. The frontend code was not changed. This list says what the frontend must change, in priority order. The full contract is `Docs/api/openapi.json` (served at `/api/v1/openapi.json`).

The current frontend keeps working **only in the demo** (`docker-compose.demo.yml`, sign-in disabled). With sign-in, items F-1 to F-4 are required.

## Must have (before a release with sign-in)

### F-1 Call the API through oauth2-proxy, on the same origin

- Build with `NEXT_PUBLIC_API_BASE=<PUBLIC_URL>/api`, for example `https://toolsmith.example.com/api`. Compose already passes this build argument.
- The browser then sends the oauth2-proxy session cookie automatically (`fetch` default `credentials: "same-origin"`). Do not add an `Authorization` header in the browser; oauth2-proxy injects the ID token.
- `wsUrl()` must keep producing an absolute `wss://<host>/api/v1/ws`. The backend only accepts WebSocket handshakes whose `Origin` is the public URL (protection against cross-site WebSocket hijacking).

### F-2 Use the versioned paths

Replace `/runs`, `/skills`, `/health`, `/ws` with `/v1/runs`, `/v1/skills`, `/v1/health`, `/v1/ws` (so `API_BASE + "/v1/..."`). The old paths still answer, with a `Deprecation: true` header, and will be removed in a later version.

### F-3 Handle sign-in and permission errors

Every error body is `{"error": {"code": "...", "message": "..."}}`. Show `message` to the user; branch on `code`.

| HTTP | Code | What the UI should do |
|---|---|---|
| 401 | `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN` | Session expired: reload the page, so oauth2-proxy signs the user in again. |
| 403 | `NO_TENANT` | Show onboarding (F-5). |
| 400 | `TENANT_REQUIRED` | The user belongs to several tenants: show the tenant switcher (F-6). |
| 403 | `FORBIDDEN` | The role does not allow this: hide or disable the action (use `permissions` from `GET /v1/me`). |
| 403 | `TENANT_FORBIDDEN`, `TENANT_SUSPENDED` | Show the message; offer the tenant switcher. |
| 429 | `RATE_LIMITED`, `QUEUE_FULL`, `QUOTA_EXCEEDED` | Show the message; if `Retry-After` is present, offer a retry after that many seconds. |
| 503 | `IDENTITY_PROVIDER_UNAVAILABLE` | "Sign-in service unavailable, try again shortly." |

Also include the `X-Request-ID` response header in error messages ("Reference: …"), so support can find the request in the logs.

### F-4 New run statuses, events and fields

- `RunInfo.status` can also be `queued` (waiting for a free worker or for the tenant's previous run) and `cancelled`. `POST /v1/runs` answers `{"run_id", "status": "running" | "queued"}`.
- New terminal event `run_cancelled` (`data: {"reason"}`).
- New `run_failed.reason_code`: `QUOTA_EXCEEDED`.
- `RunInfo` has new optional fields: `attempt`, `retry_of` (the failed run this one retries), `created_by` (`user:<id>`, `api_key:<id>` or `local:local`).
- Usage records (`llm_usage` events) have an optional `prompt_version`.
- `RUN_ALREADY_ACTIVE` (409) no longer exists: a second run is queued instead.

## Should have

### F-5 Onboarding

`GET /v1/onboarding` returns the signed-in user's memberships and `can_create_tenant`. When the user has no tenant: if `can_create_tenant`, offer "Create a workspace" (`POST /v1/onboarding/tenants {"name"}`); otherwise explain that an administrator must invite them (`EMAIL_NOT_VERIFIED` means the e-mail must be verified at the identity provider first).

### F-6 Tenant switcher

Users in several tenants send `X-Toolsmith-Tenant: <tenant id or slug>` on every request and `?tenant=<id or slug>` on the WebSocket URL. List tenants from `GET /v1/onboarding`.

### F-7 Review the code before approving

Approving a rule installs its new tools into the tenant's library. The approval screen should show each candidate's code and tests:

- `GET /v1/runs/{run_id}/candidates` lists candidate tools (any role).
- `GET /v1/runs/{run_id}/candidates/{name}` returns `manifest`, `code`, `tests`, `code_sha256` (analyst and admin). Render the code as text, never as HTML.
- `409 INTEGRITY_VIOLATION` means the files changed after verification: show it as a blocking warning and do not offer approval.
- `GET /v1/skills/{name}` shows the code of an installed tool.

### F-8 Run controls

- Cancel: `POST /v1/runs/{id}/cancel` for `queued` and `running` runs (`409 NOT_CANCELLABLE` otherwise; reject a run awaiting approval instead).
- Retry: `POST /v1/runs/{id}/retry` for `failed` and `cancelled` runs; returns the new run. Show the `retry_of` link in the history.

### F-9 User menu and sign-out

`GET /v1/me` returns `email`, `role`, `tenant` and `permissions`. Sign-out: link to `/oauth2/sign_out` (oauth2-proxy).

## Could have

### F-10 Administration screens (admin role)

- Members: `GET /v1/members`, `PATCH /v1/members/{user_id} {"role"}`, `DELETE /v1/members/{user_id}` (`409 LAST_ADMIN` protects the last admin).
- Invitations: `GET/POST /v1/invitations {"email", "role"}`, `DELETE /v1/invitations/{id}`. Invitations take effect at the invitee's next sign-in with a verified e-mail.
- API keys: `GET/POST /v1/api-keys {"name", "role": "analyst" | "viewer", "expires_in_days"}`, `DELETE /v1/api-keys/{id}`. The `key` in the creation response is shown once and never again; display it with a copy button and a warning.
- Limits and usage: `GET /v1/tenant`, `PATCH /v1/tenant/limits`, `GET /v1/usage` (monthly cost, budget, remaining, runs today, by model).
- Privacy: `GET /v1/me/export` (download as JSON), `DELETE /v1/me` (erase my data, confirm first), `DELETE /v1/tenant {"confirm": "<slug>"}` (admin, irreversible).

## Frontend image and dependencies

### F-11 Patch the nginx base image

The CI image scan found a fixable HIGH vulnerability in the frontend image (`tiff` CVE-2026-4775 in the Alpine base of `nginxinc/nginx-unprivileged:alpine`). In `frontend/Dockerfile`: pin the base image by digest and add `RUN apk upgrade --no-cache` (as root, before switching back to the unprivileged user). The CI scan of this image is informational until then.

### F-12 Development dependencies

`npm audit` reports 5 high-severity issues in development-only packages (`eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`: denial of service on deeply nested glob patterns). They are not part of the built site. `npm audit fix --force` would downgrade `eslint-config-next`; upgrade it within the current Next.js major instead when a fixed release is available.

## Unchanged

- Event envelope, sequence numbers, `after_seq` replay and the existing event payloads.
- The attribution footer stays as it is.
