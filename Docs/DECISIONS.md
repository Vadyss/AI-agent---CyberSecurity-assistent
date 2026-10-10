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
