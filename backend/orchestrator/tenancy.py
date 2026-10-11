# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""One gatekeeper per tenant: separate tool registry, candidates, lessons and audit chain."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path


class GatekeeperPool:
    def __init__(self, settings, sandbox, examine=None, *, fixed=None, observer=None) -> None:
        self.settings = settings
        self.sandbox = sandbox
        self.examine = examine
        self.observer = observer
        # Tests may inject one gatekeeper; it then serves every tenant.
        self.fixed = fixed
        self._items: dict[str, object] = {}
        self._lock = asyncio.Lock()

    def data_dir(self, tenant_id: uuid.UUID | str) -> Path:
        return Path(self.settings.data_dir) / "tenants" / str(uuid.UUID(str(tenant_id)))

    async def forget(self, tenant_id: uuid.UUID | str) -> None:
        async with self._lock:
            self._items.pop(str(uuid.UUID(str(tenant_id))), None)

    async def get(self, tenant_id: uuid.UUID | str):
        if self.fixed is not None:
            return self.fixed
        key = str(uuid.UUID(str(tenant_id)))
        async with self._lock:
            gatekeeper = self._items.get(key)
            if gatekeeper is None:
                from gatekeeper.api import Gatekeeper

                config = replace(self.settings.gatekeeper_config(), data_dir=self.data_dir(key))
                sandbox = self.sandbox.for_tenant(key) if hasattr(self.sandbox, "for_tenant") else self.sandbox
                # Registry start-up verifies and seeds files; keep it off the event loop.
                gatekeeper = await asyncio.to_thread(Gatekeeper, config, sandbox, self.examine, self.observer)
                self._items[key] = gatekeeper
            return gatekeeper
