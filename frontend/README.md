<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License. -->
# Frankenstein frontend

The Next.js frontend connects directly from the browser to the FastAPI backend. The default address is `http://127.0.0.1:8000`. HTTP requests use `/health`, `/runs`, and `/skills`; the WebSocket address is `ws://127.0.0.1:8000/ws`.

## Local development

You need Node.js with npm and Python (the Docker image uses version 3.12). In the first terminal, start the backend on port 8000 from the repository root:

```powershell
python -m pip install -r backend/requirements.txt
python -m uvicorn main:app --app-dir backend/orchestrator --host 127.0.0.1 --port 8000 --reload
```

In a second terminal, start the frontend from the repository root:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open [http://localhost:3000](http://localhost:3000). Check the backend at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

If the backend runs elsewhere, create `.env.development.local` in the `frontend` directory with its address, for example:

```dotenv
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
```

Set the backend origin without an `/api` prefix or a trailing slash. Restart `npm.cmd run dev` after changing it. On Linux and macOS, use `npm` instead of `npm.cmd`.

The backend allows frontend origins through `CORS_ORIGINS`, which defaults to `http://localhost:3000,http://127.0.0.1:3000`. For a different frontend address, set this variable in the backend environment before starting it, then restart the backend. HTTP CORS allows `GET`, `POST`, and the `Content-Type` header; the WebSocket checks `Origin` against the same list.

## Running with Docker

Run from the repository root:

```powershell
docker compose up --build
```

The application is available at [http://localhost:3000](http://localhost:3000). nginx serves static files, and the browser connects directly to the backend API and WebSocket. Compose publishes the backend as `127.0.0.1:8000:8000`.

Compose passes the build argument `NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE:-http://127.0.0.1:8000}` when building the frontend. To use a different address, set `NEXT_PUBLIC_API_BASE` in the Compose command environment or the root `.env`, then run `docker compose up --build` again. `frontend/.env.development.local` applies only to the development server.

The public `NEXT_PUBLIC_API_BASE` value is embedded in the browser JavaScript during `next build`; changing the production address requires a new build. The address must be reachable from the user's browser: `localhost` and `127.0.0.1` are for local use; do not use the Docker service name `backend` here. When the frontend uses HTTPS, the backend must use HTTPS and the WebSocket must use WSS.

For access from another device, also set `BACKEND_BIND_HOST=0.0.0.0`, a publicly reachable `NEXT_PUBLIC_API_BASE`, and the frontend origin in `CORS_ORIGINS` in the Compose environment or root `.env`. CI passes these values from GitHub repository variables with the same names; without them, it uses the local defaults.

## Voice input

Click the microphone next to the text field and allow microphone access. Voice dictation uses English (`en-US`) only. There is no language selector. Recognized speech is added to the text as you speak.

To finish, click `Stop dictation`. Once transcription finishes, you can edit the text and submit it with `Build detection`. Typing manually, selecting a template, or leaving the request editor stops dictation. Sending is disabled while transcription is in progress so the final result is preserved. Voice text shares the combined 2,000-character request limit described below.

This feature requires HTTPS or localhost and a browser that supports [SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition). If the API is unavailable or the browser denies microphone access, the interface displays a message and text input remains available. Some browsers, such as Chrome, may send audio to their online speech recognition service. Clicking `Build detection` submits the resulting text to the backend as a normal prompt.

## Requests and context

The production workspace uses the real backend for runs, events, approval, rules, and skills. The standalone `ui-concept` directory remains a separate design preview; its scripted examples are not used by this frontend.

By default, `Enter` inserts a new line and `Ctrl+Enter` or `Cmd+Enter` sends the request. In Preferences, you can instead make `Enter` send and use `Shift+Enter` for a new line. Sending is disabled while the backend is unavailable, a run is active, files are being read, or voice transcription is in progress. A failed submission keeps your draft so you can retry.

Use `Instructions` for constraints and `Attach files` for up to three `.txt`, `.log`, `.csv`, `.json`, or `.md` files, each at most 256 KB. Files can be previewed as plain text and removed before sending. The complete file contents and instructions are included as labeled sections in the existing `POST /runs` body: `{ "request": "..." }`. There is no separate upload endpoint.

The combined request, instructions, file contents, and section labels must fit within **2,000 Unicode code points**, matching the backend's character count. The editor shows the combined count and blocks submission when it exceeds the limit; it never silently truncates attached text. The file size limit does not increase the request limit. Shorten the text or remove a file if needed.

Unsent drafts, instructions, and attachments stay in memory for the current page session. They are not saved in browser storage and are cleared on reload. Submitted context becomes part of the backend run request. `Edit & rerun` opens the original request for editing before a new submission.

## Preferences and history

Preferences apply immediately and are saved in browser `localStorage` under `frankenstein-preferences`. They include:

- Light, dark, or system appearance.
- Reading size, interface density, and content width.
- Visibility of templates, recent work, context panels, and keyboard hints.
- Enter behavior, spellcheck, and JSON wrapping.

`Restore defaults` resets those preferences without discarding draft text. If browser storage is unavailable, settings apply for the current page session. Scrolling respects the system's reduced-motion preference. All interface copy and dictation controls are in English.

Open `Search runs` with `Ctrl+K` or `Cmd+K` to search backend runs, rename them in the interface, pin them, or archive them. Names, pins, and archive choices are kept in memory for the current page session; they reset on reload and do not alter backend records. Archiving hides a run from the main history; it does not delete or stop the run. Archived runs can be restored from the search dialog.

## Validation

From `frontend`, run:

```powershell
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

The tests use Node's built-in test runner and the existing TypeScript dependency. They check request assembly, Unicode boundaries, exact file content preservation, and combined context limits without contacting a backend.

## Current backend scope

The backend sends events for planning, tool building, rule evaluation, review, and approval. The timeline, results, tools, and comparison read those events and the API; this frontend has no scripted production run data. A second request during an active run returns `RUN_ALREADY_ACTIVE`. Voice summaries are optional.

The backend does not provide a stop or cancel endpoint, so the production frontend does not offer a control that pretends to cancel a running detection. `Stop dictation` only stops microphone dictation.

The [contract](../Docs/kontrakt.md) describes the API format and intended event behavior.
