# Repository workflow

- Work on a feature branch (currently `saas-hardening`); never commit or push directly to `main`.
- Before pushing, verify the current branch and push it explicitly, for example `git push origin saas-hardening`.
- One logical change per commit. The backend test suite (`sh backend/scripts/test.sh`) must stay green on every commit.
- Never commit secrets. `.env` is git-ignored; `.env.example` lists variable names only.
- Record architectural decisions in `Docs/DECISIONS.md` and API changes the frontend must follow in `Docs/FRONTEND_CHANGES.md`.
