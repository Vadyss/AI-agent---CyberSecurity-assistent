<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Toolsmith frontend

A Next.js 16 / React 19 single-page workspace, exported as static files and served by nginx. It talks to the backend over HTTP and one WebSocket. The event model it follows is described in [Docs/EVENTS.md](../Docs/EVENTS.md).

**Status:** the UI works with the sign-in-free demo stack. It still uses the first, unversioned API paths (`/runs`, `/skills`, `/health`, `/ws`) and has no sign-in handling. The changes needed for the full authenticated mode are listed in [Docs/FRONTEND_CHANGES.md](../Docs/FRONTEND_CHANGES.md).

## Run it

From the repository root, start the backend and sandbox in demo mode (mock model, no sign-in, localhost only):

```bash
POSTGRES_PASSWORD=demo docker compose -f docker-compose.yml -f docker-compose.demo.yml up --build backend sandbox
```

The backend is then on `http://127.0.0.1:8000`. In a second terminal:

```bash
cd frontend
npm ci
npm run dev          # http://localhost:3000
```

To use the packaged frontend instead, run the whole demo stack as described in the [README](../README.md).

## Configuration

`NEXT_PUBLIC_API_BASE` is the backend origin as the *browser* sees it (default `http://127.0.0.1:8000`, no `/api` prefix, no trailing slash). It is embedded in the JavaScript at build time, so changing it needs a new build (`docker compose ... up --build`, or a restart of `npm run dev`). For development you can put it in `frontend/.env.development.local`. The address must be reachable from the browser; the Docker service name `backend` is not. The backend only accepts browser origins listed in `CORS_ORIGINS` (default `http://localhost:3000,http://127.0.0.1:3000`), for both HTTP and the WebSocket handshake.

## Checks

```bash
npm run lint
npm test             # Node's test runner, no backend needed
npm run build        # static export to out/
```

## Behaviour worth knowing

- **Plain text only.** Everything that comes from the API (model output, logs, analyst text) is rendered as text, never as HTML or Markdown. The tests include XSS probes.
- **Requests and context.** `Enter` inserts a new line and `Ctrl/Cmd+Enter` sends (switchable in Preferences). `Instructions` and up to three attached text files (`.txt`, `.log`, `.csv`, `.json`, `.md`, each at most 256 KB) are added as labelled sections to the single request text; there is no upload endpoint. The combined text must fit in 2,000 characters; the editor blocks sending instead of truncating.
- **Drafts and history.** Unsent drafts stay in memory for the page session. Renaming, pinning and archiving runs in `Search runs` (`Ctrl/Cmd+K`) is local to the page and does not change backend records.
- **Preferences** (theme, density, reading size, Enter behaviour, JSON wrapping) are stored in `localStorage` under `frankenstein-preferences`.
- **Voice input** uses the browser's SpeechRecognition in English (`en-US`) and needs HTTPS or localhost. Some browsers send audio to their own speech service. It is optional; typing always works.
- **Voice summaries** from the backend are optional and play only when a `voice_ready` event arrives.
- All interface text is in English.
