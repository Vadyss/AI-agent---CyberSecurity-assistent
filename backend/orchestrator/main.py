# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Frontend contract v1 and dependency composition for the backend."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import APIRouter, Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from gatekeeper.api import AUDIT_ACTOR
from identity.principal import IdentityError, Permission, Principal
from orchestrator.api_errors import ApiError, register_handlers
from orchestrator.auth import Authenticator, requires
from orchestrator.config import Settings
from orchestrator.decisions import approve, reject
from orchestrator.events import EventEmitter
from orchestrator.models import ApproveBody, CreateRunBody, EventEnvelope, RejectBody, RunInfo
from orchestrator.run_store import RunStore
from orchestrator.tenancy import GatekeeperPool
from orchestrator.ws import WebSocketHub

CONTRACT_VERSION = 1
RUN_ID = re.compile(r"run_[a-z0-9]{4,32}")
RESTART_REASON = "The backend restarted during this run."
RETRY_BACKOFF_S = 30


def new_run_id() -> str:
    # 64 random bits: run IDs stay unique across the whole history in the database.
    return "run_" + secrets.token_hex(8)


async def recover_unfinished_runs(repository) -> list:
    """Close runs a previous process left open; returns those that were mid-flight.

    Their in-memory state (gatekeeper checks, candidates) is gone, so they
    cannot continue. Runs that were still running are retried as new runs;
    runs awaiting approval must be started again by the analyst.
    """
    from orchestrator.events import render_message
    from orchestrator.run_store import now_iso

    interrupted = []
    for record in await repository.unfinished():
        if record.status == "running":
            interrupted.append(record)
        events = await repository.events(record.id)
        if not events:
            await repository.set_status(record.id, "failed", reason_code="INTERNAL_ERROR", finished=True)
            continue
        data = {"reason_code": "INTERNAL_ERROR", "reason": RESTART_REASON}
        event = EventEnvelope(type="run_failed", run_id=record.id, seq=record.last_seq + 1, timestamp=now_iso(),
                              phase=events[-1]["phase"], message=render_message("run_failed", data),
                              data=data).model_dump(mode="json")
        from datetime import datetime, timezone
        await repository.record_event(record.id, event, status="failed", finished_at=datetime.now(timezone.utc))
        logging.getLogger(__name__).warning("Run %s was interrupted by a restart and marked failed.", record.id)
    return interrupted


def create_app(*, settings=None, gatekeeper=None, sandbox=None, roles=None, pipeline_runner=None, voice=None,
               oidc_transport=None) -> FastAPI:
    config = (settings or Settings.from_env()).validate()

    @asynccontextmanager
    async def lifespan(app):
        sandbox_client = sandbox
        app.state.roles = roles
        app.state.voice = voice
        app.state.examiner = None
        app.state.db = None
        app.state.oidc_http = None
        logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO))
        try:
            await open_identity(app)
            if sandbox_client is None:
                from gatekeeper.api import SandboxClient

                sandbox_client = SandboxClient(config.sandbox_url, timeout_s=config.sandbox_timeout_s,
                                               uds=config.sandbox_socket or None)

            async def examine(description, log_format):
                return await app.state.examiner.generate(description, log_format)

            app.state.gatekeepers = GatekeeperPool(config, sandbox_client, examine if config.examiner_enabled else None,
                                                   fixed=gatekeeper)
            # Development and tests: expose the local tenant's gatekeeper for diagnostics.
            local = app.state.auth.local
            app.state.gatekeeper = await app.state.gatekeepers.get(local.tenant_id) if local else gatekeeper
            from gatekeeper.api import load_policy

            policy_calls = (getattr(gatekeeper, "max_llm_calls", None)
                            or load_policy(config.policy_path).llm.max_calls_per_run)
            role_config = replace(config, llm_max_calls_per_run=min(config.llm_max_calls_per_run, policy_calls))
            if roles is None:
                from orchestrator.planner import make_roles

                app.state.roles = make_roles(role_config)
            if config.examiner_enabled:
                from examiner import Examiner

                app.state.examiner = Examiner(app.state.roles.client)
            if voice is None:
                from orchestrator.voice import VoiceService

                app.state.voice = VoiceService(config, app.state.emitter)
            try:
                available = await sandbox_client.health()
            except Exception:
                available = False
            if not available:
                logging.getLogger(__name__).warning("Sandbox is unavailable; the run will end with SANDBOX_ERROR.")
            from orchestrator.dispatcher import RunDispatcher

            app.state.dispatcher = RunDispatcher(app.state.run_repository, start_run,
                                                 concurrency=config.worker_concurrency)
            for record in app.state.interrupted:
                if record.attempt < config.run_max_attempts:
                    await schedule_retry(record.id, record.tenant_id, record.created_by, record.request,
                                         record.attempt, delay_s=0)
            app.state.dispatcher.start_background()
            yield
        finally:
            if app.state.dispatcher is not None:
                await app.state.dispatcher.close()
            for resource in (app.state.store, app.state.hub, sandbox_client, app.state.voice, app.state.roles):
                close = getattr(resource, "close", None)
                if close:
                    try:
                        await close()
                    except Exception as exc:
                        logging.getLogger(__name__).error("Failed to close a backend resource (%s).", type(exc).__name__)
            if app.state.oidc_http is not None:
                await app.state.oidc_http.aclose()
            if app.state.db is not None:
                await app.state.db.close()

    async def open_identity(app) -> None:
        """Database, identity service and authenticator; refuses an outdated schema."""
        import httpx

        from identity.oidc import JwksCache, OidcVerifier
        from identity.service import IdentityService
        from storage.db import Database

        # An empty URL is only valid in tests (see Settings.validate): in-memory SQLite.
        db = Database(config.database_url or "sqlite+aiosqlite://")
        app.state.db = db
        if db.is_sqlite:
            await db.create_schema()
        elif not await db.schema_is_current():
            raise RuntimeError("The database schema is not at the newest migration; run the migrate service.")
        identity = IdentityService(db, bootstrap_admin_email=config.bootstrap_admin_email,
                                   bootstrap_tenant_name=config.bootstrap_tenant_name, signup_mode=config.signup_mode)
        verifier, local = None, None
        if config.auth_mode == "oidc":
            app.state.oidc_http = httpx.AsyncClient(trust_env=False, follow_redirects=False, transport=oidc_transport)
            verifier = OidcVerifier(config.oidc_issuer, config.oidc_audience,
                                    JwksCache(config.oidc_jwks_url, app.state.oidc_http),
                                    algorithms=config.oidc_algorithms)
        else:
            local = await identity.ensure_local_tenant()
            logging.getLogger(__name__).warning(
                "AUTH_MODE=disabled: every request acts as the local admin. Development only.")
        app.state.identity = identity
        app.state.auth = Authenticator(identity, issuer=config.oidc_issuer, verifier=verifier, local=local)
        from storage.runs import RunRepository

        app.state.run_repository = RunRepository(db)
        app.state.emitter.repository = app.state.run_repository
        app.state.interrupted = await recover_unfinished_runs(app.state.run_repository)

    app = FastAPI(title="Frankenstein", lifespan=lifespan, docs_url=None, redoc_url=None)
    register_handlers(app)
    app.state.settings = config
    app.state.store = RunStore()
    app.state.hub = WebSocketHub()
    app.state.emitter = EventEmitter(app.state.hub)
    app.state.gatekeepers = None
    app.state.auth = None
    app.state.run_repository = None
    app.state.dispatcher = None
    app.state.interrupted = []
    from identity.ratelimit import RateLimiter

    app.state.rate_limits = RateLimiter(config.rate_limit_per_minute)
    app.state.run_limits = RateLimiter(config.run_rate_limit_per_minute)
    if config.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins),
                           allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Toolsmith-Tenant"])
    router = APIRouter()
    reader = Depends(requires(Permission.READ))
    runner = Depends(requires(Permission.RUN))
    decider = Depends(requires(Permission.DECIDE))

    @router.get("/health")
    async def health():
        return {"status": "ok", "contract_version": CONTRACT_VERSION}

    @router.get("/me")
    async def me(principal: Principal = reader):
        return {"kind": principal.kind, "id": principal.id, "email": principal.email, "role": principal.role,
                "tenant": {"id": str(principal.tenant_id), "slug": principal.tenant_slug},
                "permissions": sorted(p.value for p in Permission if principal.can(p))}

    async def start_run(record):
        """Called by the dispatcher for a claimed run: start its pipeline as the run's creator."""
        run = app.state.store.adopt(record.id, record.request, record.tenant_id, record.created_by, record.attempt)
        tenant_gatekeeper = await app.state.gatekeepers.get(record.tenant_id)
        run_settings = replace(config, data_dir=app.state.gatekeepers.data_dir(record.tenant_id))
        token = AUDIT_ACTOR.set(record.created_by)
        try:
            run.task = asyncio.create_task(execute(run, tenant_gatekeeper, run_settings))
        finally:
            AUDIT_ACTOR.reset(token)
        return run.task

    async def execute(run, tenant_gatekeeper, run_settings):
        pipeline = pipeline_runner
        if pipeline is None:
            from orchestrator.pipeline import run_pipeline

            pipeline = run_pipeline
        try:
            await pipeline(run, tenant_gatekeeper, app.state.roles, app.state.emitter, run_settings, app.state.voice)
        finally:
            if run.cancel_requested and run.status == "running":
                # Whatever the pipeline did, a cancelled run must end in a terminal state.
                try:
                    if not run.events:
                        await app.state.emitter.emit(run, "run_started", "intake", {"request": run.request})
                    await app.state.emitter.emit(run, "run_cancelled", run.phase, {"reason": "Cancelled on request."})
                except Exception as error:
                    logging.getLogger(__name__).error("Run %s could not be closed after cancellation (%s).",
                                                      run.run_id, type(error).__name__)
            if run.status == "failed" and run.retryable and not run.cancel_requested \
                    and run.attempt < config.run_max_attempts:
                await schedule_retry(run.run_id, run.tenant_id, run.created_by, run.request, run.attempt)

    async def schedule_retry(run_id, tenant_id, created_by, request, attempt, *, delay_s=None):
        """Queue a fresh attempt as a new run that points back to the failed one."""
        from datetime import datetime, timedelta, timezone

        delay = RETRY_BACKOFF_S * attempt if delay_s is None else delay_s
        retry = await app.state.run_repository.create(
            run_id=new_run_id(), tenant_id=tenant_id, created_by=created_by, request=request, status="queued",
            attempt=attempt + 1, retry_of=run_id, not_before=datetime.now(timezone.utc) + timedelta(seconds=delay))
        logging.getLogger(__name__).warning("Run %s failed transiently; retry %s queued.", run_id, retry.id)
        app.state.dispatcher.wake()
        return retry

    @router.post("/runs", status_code=202)
    async def create_run(body: CreateRunBody, principal: Principal = runner):
        text = body.request.strip()
        if not 1 <= len(text) <= 2000:
            raise ApiError(400, "INVALID_REQUEST", "The request must contain 1 to 2000 characters.")
        # A tenant-wide cap on new runs; each run can spend real model budget.
        app.state.run_limits.check(f"tenant:{principal.tenant_id}")
        queued = await app.state.run_repository.count(principal.tenant_id, statuses=("queued",))
        if queued >= config.max_queued_runs_per_tenant:
            raise ApiError(429, "QUEUE_FULL", "Too many runs are waiting; try again when one has finished.")
        record = await app.state.run_repository.create(run_id=new_run_id(), tenant_id=principal.tenant_id,
                                                       created_by=principal.actor, request=text)
        # Start right away when a worker is free; otherwise the run waits in the queue.
        started = await app.state.dispatcher.fill()
        return {"run_id": record.id, "status": "running" if record.id in started else "queued"}

    @router.post("/runs/{run_id:path}/cancel")
    async def cancel_run(run_id: str, principal: Principal = runner):
        record = await stored_run(run_id, principal)
        if record.status == "queued":
            await app.state.run_repository.cancel_queued(record.id)
            refreshed = await app.state.run_repository.get(record.id, principal.tenant_id)
            if refreshed.status == "cancelled":
                return {"status": "cancelled"}
            record = refreshed  # The dispatcher claimed it meanwhile.
        run = app.state.store.find(run_id, principal.tenant_id)
        if record.status != "running" or run is None or run.task is None:
            raise ApiError(409, "NOT_CANCELLABLE", "Only queued or running runs can be cancelled; "
                                                   "reject a run that awaits approval.")
        run.cancel_requested = True
        await app.state.run_repository.request_cancel(run.run_id)
        run.task.cancel()
        await asyncio.wait([run.task], timeout=10)
        return {"status": run.status}

    @router.post("/runs/{run_id:path}/retry", status_code=202)
    async def retry_run(run_id: str, principal: Principal = runner):
        record = await stored_run(run_id, principal)
        if record.status not in {"failed", "cancelled"}:
            raise ApiError(409, "NOT_RETRYABLE", "Only failed or cancelled runs can be retried.")
        app.state.run_limits.check(f"tenant:{principal.tenant_id}")
        retry = await schedule_retry(record.id, principal.tenant_id, principal.actor, record.request,
                                     record.attempt, delay_s=0)
        started = await app.state.dispatcher.fill()
        return {"run_id": retry.id, "status": "running" if retry.id in started else "queued"}

    async def stored_run(run_id: str, principal: Principal):
        """The tenant's run from the database; another tenant's run is reported as missing."""
        if not RUN_ID.fullmatch(run_id):
            raise ApiError(404, "RUN_NOT_FOUND", "Run not found.")
        record = await app.state.run_repository.get(run_id, principal.tenant_id)
        if record is None:
            raise ApiError(404, "RUN_NOT_FOUND", "Run not found.")
        return record

    async def live_run(run_id: str, principal: Principal):
        """A run of this process that can still take a decision."""
        run = app.state.store.find(run_id, principal.tenant_id) if RUN_ID.fullmatch(run_id) else None
        if run is None:
            await stored_run(run_id, principal)
            raise ApiError(409, "NOT_AWAITING_APPROVAL", "This run is not awaiting approval.")
        return run

    @router.get("/runs")
    async def list_runs(limit: int = 50, principal: Principal = reader):
        if not 1 <= limit <= 200:
            raise ApiError(400, "INVALID_REQUEST", "limit must be between 1 and 200.")
        records = await app.state.run_repository.list(principal.tenant_id, limit=limit)
        return {"runs": [RunInfo.model_validate(r.info()).model_dump(mode="json") for r in records]}

    @router.get("/runs/{run_id:path}/events")
    async def get_events(run_id: str, after_seq: str = "0", principal: Principal = reader):
        run = app.state.store.find(run_id, principal.tenant_id) if RUN_ID.fullmatch(run_id) else None
        if run is None:
            await stored_run(run_id, principal)
        if not after_seq.isascii() or not after_seq.isdigit():
            raise ApiError(400, "INVALID_REQUEST", "Invalid after_seq parameter.")
        try:
            number = int(after_seq)
        except ValueError:
            raise ApiError(400, "INVALID_REQUEST", "Invalid after_seq parameter.") from None
        if run is None:
            return {"events": await app.state.run_repository.events(run_id, number)}
        async with run.lock:
            return {"events": [e for e in run.events if e["seq"] > number]}

    @router.post("/runs/{run_id:path}/approve")
    async def approve_run(run_id: str, body: ApproveBody | None = None, principal: Principal = decider):
        run = await live_run(run_id, principal)
        return await approve(run, body.comment if body else None,
                             await app.state.gatekeepers.get(principal.tenant_id), app.state.emitter)

    @router.post("/runs/{run_id:path}/reject")
    async def reject_run(run_id: str, body: RejectBody, principal: Principal = decider):
        run = await live_run(run_id, principal)
        reason = body.reason.strip()
        if not 1 <= len(reason) <= 500:
            raise ApiError(400, "INVALID_REQUEST", "The reason must contain 1 to 500 characters.")
        return await reject(run, reason, await app.state.gatekeepers.get(principal.tenant_id), app.state.emitter)

    @router.get("/skills")
    async def list_skills(principal: Principal = reader):
        tenant_gatekeeper = await app.state.gatekeepers.get(principal.tenant_id)
        return {"skills": [s.model_dump(mode="json") for s in tenant_gatekeeper.installed_skills()]}

    @router.get("/runs/{run_id:path}/audio")
    async def get_audio(run_id: str, principal: Principal = reader):
        run = app.state.store.find(run_id, principal.tenant_id) if RUN_ID.fullmatch(run_id) else None
        if run is None:
            await stored_run(run_id, principal)
            raise ApiError(404, "AUDIO_NOT_FOUND", "This run has no audio summary.")
        if run.audio is None:
            raise ApiError(404, "AUDIO_NOT_FOUND", "This run has no audio summary.")
        return Response(run.audio, media_type="audio/mpeg")

    @router.websocket("/ws")
    async def websocket_events(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin is not None and origin not in config.cors_origins:
            await ws.close(code=1008)
            return
        try:
            principal = await app.state.auth.authenticate(ws.headers, ws.query_params.get("tenant"))
            principal.require(Permission.READ)
        except IdentityError:
            # Policy violation: the handshake is refused before any event is sent.
            await ws.close(code=1008)
            return
        subscriber = await app.state.hub.connect(ws, principal.tenant_id)
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await app.state.hub.disconnect(subscriber)

    from orchestrator.admin_api import build_admin_router

    router.include_router(build_admin_router(config))
    app.include_router(router)
    app.include_router(router, prefix="/api")
    return app

