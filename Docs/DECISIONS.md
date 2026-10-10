<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Architecture decisions

Short records of decisions taken while turning the prototype into a multi-tenant SaaS.
Each entry states the decision, the reason, and the trade-off we accept. Newest last.

## D-001 Work branch and repository rules

- **Decision:** All hardening work happens on `saas-hardening`, branched from `cost-tracking` (not yet merged into `main`). The old "commit only on `frontend`" rule is retired.
- **Why:** `cost-tracking` contains the latest backend work. A dedicated branch keeps `main` deployable.
- **Trade-off:** `saas-hardening` must later be merged through a reviewed pull request.

## D-002 Uncommitted skeleton changes were stashed, not deleted

- **Decision:** The uncommitted single-file skeleton in `backend/orchestrator/main.py` and `backend/tests/test_health.py` was moved to `git stash` as `sev1-skeleton`. The modular `create_app` version is the baseline.
- **Why:** The skeleton removed the gatekeeper, pipeline and sandbox wiring. Stashing keeps it recoverable (`git stash list`).

## D-003 Documentation lives in `Docs/`

- **Decision:** New documents go to `Docs/` (existing folder), not `docs/`.
- **Why:** macOS uses a case-insensitive file system by default, so `docs/` and `Docs/` would be the same folder locally but two folders in Git on Linux.

## D-004 Tests run in the production base image

- **Decision:** Backend tests run with `sh backend/scripts/test.sh`, inside `python:3.12-slim` as root, the same base image as production.
- **Why:** The local interpreter is Python 3.9, and some sandbox isolation tests need root inside a container to switch user IDs. CI uses the same script so local and CI results match.

## D-005 Sandbox isolation: hardened process per job with a private UID

- **Decision:** Generated code runs in a fresh Python process per job inside one long-lived, hardened sandbox container. Each job gets its own unprivileged UID from a small slot pool (`SANDBOX_UID_BASE` + slot). After a job ends, the supervisor kills every process of that UID (found through `/proc`) before the slot is reused. If that ever fails, the supervisor exits, so a dirty UID is never reused and the restart policy brings up a clean container.
- **Why not a container or microVM per run:** Starting containers per job would need the Docker socket in the backend, and that socket is equivalent to root on the host. gVisor or Firecracker add a host runtime that a team of two would have to operate. A private UID per job gives kernel-enforced separation between concurrent jobs (and so between tenants), on top of the existing container boundary and Python-level guards.
- **What enforces it:** read-only root file system; no network (D-006); all capabilities dropped except `SETUID`, `SETGID` and `KILL` for the supervisor; `no-new-privileges`; job processes have zero capabilities; per-job `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NOFILE`, `RLIMIT_NPROC=16` and `RLIMIT_FSIZE=0` (no file writes at all); container `pids_limit`, memory and CPU limits; `/tmp` readable only by root.
- **Fairness:** At most `SANDBOX_MAX_JOBS` jobs run at once, and at most `SANDBOX_MAX_JOBS_PER_TENANT` of them for one tenant.
- **Trade-off:** The supervisor process starts as UID 0 (with the three capabilities above) because it must switch UIDs. Untrusted code never runs as UID 0. The kernel is still shared with the host, so a kernel exploit would cross the boundary; gVisor (`runtime: runsc`) can be added later without code changes.

## D-006 The sandbox has no network; the backend uses a Unix socket

- **Decision:** The sandbox container runs with `network_mode: none`. The supervisor listens on `/run/toolsmith/sandbox.sock` on a volume shared only with the backend. The socket directory is `root:10001`, mode `0750`, so the backend (GID 10001) can connect and job UIDs cannot even reach the path.
- **Why:** Previously the sandbox shared an internal Docker network with the backend. Code that escaped the Python guards could then send requests to the backend API. With no network interface there is nothing to reach, not even other containers.
- **Trade-off:** Local development without Docker still uses TCP (`SANDBOX_URL`, no `SANDBOX_SOCKET`). That mode is for development only and is logged as such.

## D-007 Storage: PostgreSQL for metadata, files for artifacts and audit

- **Decision:** PostgreSQL (SQLAlchemy 2 async with psycopg 3, Alembic migrations) stores tenants, users, memberships, invitations, API keys, runs, run events and LLM usage. Skill code, tests, approved rules and the hash-chained audit log stay on the `/data` volume, under one directory per tenant.
- **Why:** Relational data needs constraints, transactions and queries (quotas, queue, RBAC). Skill artifacts are already content-addressed and integrity-checked by the gatekeeper registry. Moving them into the database would rewrite proven code without a security gain.
- **Order:** The database foundation (a P2 item) is built before P1, because identities, tenants and API keys need it.
- **Testing:** One SQL implementation. Fast tests use in-memory SQLite. `backend/scripts/test.sh` also starts a throwaway PostgreSQL server, and the storage tests run against both. A test checks that the migrations match the models and downgrade cleanly.
- **Trade-off:** Backups must cover both PostgreSQL and the `/data` volume (see the operations guide).

## D-008 Secure defaults and three environments

- **Decision:** `ENVIRONMENT` is `production` unless set otherwise, and `AUTH_MODE` is `oidc`. In production the backend refuses to start without a PostgreSQL `DATABASE_URL`, complete OIDC settings, an HTTPS issuer and a real model provider. `AUTH_MODE=disabled` (one local tenant, local admin) is allowed only in `development` and `test`. The demo overlay uses it, and it must never be exposed beyond localhost.
- **Why:** A forgotten variable must produce a server that does not start, not one that runs without authentication.
- **Implementation:** The app is created by `uvicorn orchestrator.main:create_app --factory`, so configuration is validated at startup instead of at import. Migrations run in a one-shot `migrate` service before the backend starts. `POSTGRES_PASSWORD` has no default.
- **Trade-off:** The `deploy` job on `main` now needs `POSTGRES_PASSWORD` (secret) and the `OIDC_*` variables configured in GitHub before it can start the stack.
