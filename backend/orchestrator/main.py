# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Frontend contract v1 and dependency composition for the backend."""
from __future__ import annotations

import asyncio
import logging
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
from orchestrator.models import ApproveBody, CreateRunBody, RejectBody
from orchestrator.run_store import RunStore
from orchestrator.tenancy import GatekeeperPool
from orchestrator.ws import WebSocketHub

CONTRACT_VERSION = 1


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
            yield
        finally:
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

    app = FastAPI(title="Frankenstein", lifespan=lifespan, docs_url=None, redoc_url=None)
    register_handlers(app)
    app.state.settings = config
    app.state.store = RunStore()
    app.state.hub = WebSocketHub()
    app.state.emitter = EventEmitter(app.state.hub)
    app.state.gatekeepers = None
    app.state.auth = None
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

    @router.post("/runs", status_code=202)
    async def create_run(body: CreateRunBody, principal: Principal = runner):
        text = body.request.strip()
        if not 1 <= len(text) <= 2000:
            raise ApiError(400, "INVALID_REQUEST", "The request must contain 1 to 2000 characters.")
        tenant_gatekeeper = await app.state.gatekeepers.get(principal.tenant_id)
        run = await app.state.store.create(text, principal.tenant_id, principal.actor)
        pipeline = pipeline_runner
        if pipeline is None:
            from orchestrator.pipeline import run_pipeline

            pipeline = run_pipeline
        run_settings = replace(config, data_dir=app.state.gatekeepers.data_dir(principal.tenant_id))
        # The task copies this context, so every audit record of the run names its actor.
        token = AUDIT_ACTOR.set(principal.actor)
        try:
            run.task = asyncio.create_task(pipeline(run, tenant_gatekeeper, app.state.roles,
                                                    app.state.emitter, run_settings, app.state.voice))
        finally:
            AUDIT_ACTOR.reset(token)
        return {"run_id": run.run_id, "status": "running"}

    @router.get("/runs")
    async def list_runs(principal: Principal = reader):
        return {"runs": app.state.store.list(principal.tenant_id)}

    @router.get("/runs/{run_id:path}/events")
    async def get_events(run_id: str, after_seq: str = "0", principal: Principal = reader):
        run = app.state.store.get(run_id, principal.tenant_id)
        if not after_seq.isascii() or not after_seq.isdigit():
            raise ApiError(400, "INVALID_REQUEST", "Invalid after_seq parameter.")
        try:
            number = int(after_seq)
        except ValueError:
            raise ApiError(400, "INVALID_REQUEST", "Invalid after_seq parameter.") from None
        async with run.lock:
            return {"events": [e for e in run.events if e["seq"] > number]}

    @router.post("/runs/{run_id:path}/approve")
    async def approve_run(run_id: str, body: ApproveBody | None = None, principal: Principal = decider):
        run = app.state.store.get(run_id, principal.tenant_id)
        return await approve(run, body.comment if body else None,
                             await app.state.gatekeepers.get(principal.tenant_id), app.state.emitter)

    @router.post("/runs/{run_id:path}/reject")
    async def reject_run(run_id: str, body: RejectBody, principal: Principal = decider):
        run = app.state.store.get(run_id, principal.tenant_id)
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
        run = app.state.store.get(run_id, principal.tenant_id)
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

    app.include_router(router)
    app.include_router(router, prefix="/api")
    return app

