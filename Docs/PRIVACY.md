<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Privacy notes (GDPR)

Technical notes for the operator of a Toolsmith deployment and for customers' data protection officers. They describe what the software stores and how to honour data-subject rights. They are not legal advice; the operator remains responsible for its own records of processing, contracts and notices.

## Roles

For tenant data, the customer (tenant) is normally the **controller** and the operator of the deployment the **processor**. For sign-in data of its users, the operator may be a controller in its own right. Model providers (for example OpenRouter via Apify) and the identity provider are **sub-processors** and must be named in the data processing agreement.

## What is stored

| Data | Where | Personal data? | Retention |
|---|---|---|---|
| User profile: provider issuer and subject, e-mail, e-mail verified flag, display name, last sign-in | PostgreSQL `users` | Yes | Until erased (`DELETE /v1/me`) or the user is removed |
| Memberships and roles, invitations (e-mail, role) | PostgreSQL | Yes (e-mail) | Until removed. Invitations stop working after 14 days but stay listed until revoked or the tenant is deleted. |
| API keys (name, role, SHA-256 of the secret, timestamps) | PostgreSQL | No (linked to the creating user ID) | Until revoked and the tenant is deleted |
| Runs: analyst request text, status, creator ID | PostgreSQL `runs` | Possibly: free text written by analysts | `RUN_RETENTION_DAYS` (default 365) after creation, for finished runs |
| Run events: plans, rule drafts, metrics, summaries, rejection reasons and approval comments | PostgreSQL `run_events` | Possibly (free text) | Deleted with their run |
| Model usage: model, tokens, cost, prompt version | PostgreSQL `llm_usage`, `/data/tenants/<id>/runs/` | No | Deleted with their run |
| Tool code, tests, approved rules, lessons | `/data/tenants/<id>/` | Normally no | Until the tenant is deleted |
| Audit chain: actor IDs, run IDs, digests, short reasons and comments | `/data/tenants/<id>/audit.jsonl` | Pseudonymous IDs, possibly free text | Lifetime of the tenant (the hash chain cannot drop single records) |
| Logs: request ID, route, status, tenant ID, run ID, actor ID | stdout of the containers | Pseudonymous IDs | As configured in your log collector |

**Log samples** sent to the model come from the datasets shipped with the product (`backend/datasets`). If a deployment adds customer logs, these must be treated as personal data (IP addresses, user names).

Not stored: passwords (the identity provider handles sign-in), API key secrets in clear text, request headers or bodies in logs, prompts in usage records (only token counts and a prompt version hash).

## Data-subject rights

| Right | How |
|---|---|
| Access, portability (Art. 15, 20) | The person calls `GET /v1/me/export` (JSON: profile, memberships, invitations, runs they started). |
| Erasure (Art. 17) | The person calls `DELETE /v1/me`: memberships and invitations are deleted, e-mail, name and the provider link are blanked. History keeps only the pseudonymous `user:<id>`, which no longer resolves to a person. The last admin of a tenant must hand over or delete the tenant first. |
| Erasure of a whole customer | A tenant admin calls `DELETE /v1/tenant` with the tenant slug: all database rows and files of the tenant, including the audit chain, are deleted. |
| Rectification | Profile data comes from the identity provider and is refreshed at each sign-in. |
| Restriction | An operator can suspend a tenant (`tenants.status = 'suspended'`). |

Backups contain the same data and age out with your backup rotation (see `Docs/OPERATIONS.md`). An erasure is complete once the last backup made before it has expired.

## Security of processing (Art. 32)

Encryption in transit at the TLS reverse proxy; tenant isolation, roles and audit as described in `SECURITY.md` and `Docs/THREAT_MODEL.md`. Encryption at rest is the responsibility of the host (encrypted volumes), and backups must be encrypted before they leave the host.

## International transfers

Model calls send the analyst request, plans and short log samples to the configured model provider. Choose a provider and region that match your transfer requirements, and record them as sub-processors.
