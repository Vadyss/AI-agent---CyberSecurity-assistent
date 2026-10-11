# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Privacy endpoints: personal-data export and erasure, tenant deletion, retention."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from identity.principal import IdentityError, Permission, Principal
from orchestrator.auth import requires

logger = logging.getLogger(__name__)


class DeleteTenantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


async def signed_in_user(request: Request):
    auth = request.app.state.auth
    if auth.local is not None:
        raise IdentityError(403, "NOT_AVAILABLE", "Personal data endpoints need sign-in; authentication is disabled.")
    return await auth.user(request.headers)


def build_privacy_router() -> APIRouter:
    router = APIRouter()
    manager = Depends(requires(Permission.MANAGE))

    @router.get("/me/export")
    async def export_my_data(request: Request):
        """Everything stored about the signed-in person (GDPR access and portability)."""
        user = await signed_in_user(request)
        state = request.app.state
        data = await state.identity.export_user(user.id)
        tenant_ids = [uuid.UUID(m["tenant_id"]) for m in data["memberships"]]
        runs = await state.run_repository.runs_created_by(f"user:{user.id}", tenant_ids)
        data["runs"] = [{**run.info(), "tenant_id": str(run.tenant_id)} for run in runs]
        return data

    @router.delete("/me", status_code=204)
    async def erase_me(request: Request):
        """Erase the signed-in person's personal data and memberships (GDPR erasure)."""
        user = await signed_in_user(request)
        await request.app.state.identity.erase_user(user.id)
        request.app.state.auth.forget()
        return Response(status_code=204)

    @router.delete("/tenant", status_code=204)
    async def delete_tenant(body: DeleteTenantBody, request: Request, principal: Principal = manager):
        """Delete the whole tenant: runs, events, usage, members, keys, tools, rules and audit chain."""
        if body.confirm != principal.tenant_slug:
            raise IdentityError(400, "CONFIRMATION_REQUIRED", "Send the tenant slug in confirm to delete the tenant.")
        state = request.app.state
        live = [run for run in list(state.store.runs.values()) if run.tenant_id == principal.tenant_id]
        for run in live:
            run.cancel_requested = True
            if run.task is not None and not run.task.done():
                run.task.cancel()
        tasks = [run.task for run in live if run.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=15)
        for run in live:
            state.store.runs.pop(run.run_id, None)
        await state.identity.delete_tenant(principal.tenant_id)
        from gatekeeper.api import erase_tenant_data

        await state.gatekeepers.forget(principal.tenant_id)
        await asyncio.to_thread(erase_tenant_data, state.gatekeepers.data_dir(principal.tenant_id))
        state.auth.forget()
        logger.warning("Tenant %s was deleted by %s.", principal.tenant_id, principal.actor)
        return Response(status_code=204)

    return router


async def purge_expired_runs(app, days: int, now: datetime | None = None) -> int:
    """Retention: delete finished runs older than ``days`` with their events, usage rows and files."""
    if days <= 0:
        return 0
    from gatekeeper.api import remove_run_files

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    purged = await app.state.run_repository.purge_finished_before(cutoff)
    by_tenant: dict[uuid.UUID, list[str]] = defaultdict(list)
    for tenant_id, run_id in purged:
        by_tenant[tenant_id].append(run_id)
        app.state.store.runs.pop(run_id, None)
    for tenant_id, run_ids in by_tenant.items():
        await asyncio.to_thread(remove_run_files, app.state.gatekeepers.data_dir(tenant_id), run_ids)
    if purged:
        logger.info("Retention removed %d finished runs older than %d days.", len(purged), days)
    return len(purged)


async def retention_loop(app, days: int, interval_s: float = 86400, first_delay_s: float = 300) -> None:
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            await purge_expired_runs(app, days)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("Retention run failed (%s).", type(error).__name__)
        await asyncio.sleep(interval_s)
