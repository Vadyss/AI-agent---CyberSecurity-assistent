# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Starts queued runs from the database as worker capacity frees up."""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class RunDispatcher:
    def __init__(self, repository, start, *, concurrency: int, poll_s: float = 1.0) -> None:
        self.repository = repository
        self.start = start            # async (RunRecord) -> asyncio.Task
        self.concurrency = concurrency
        self.poll_s = poll_s
        self.active: set[str] = set()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def wake(self) -> None:
        self._wake.set()

    async def fill(self) -> list[str]:
        """Claim and start runs while capacity remains; returns the started run IDs."""
        started = []
        async with self._lock:
            while len(self.active) < self.concurrency:
                record = await self.repository.claim_next()
                if record is None:
                    break
                self.active.add(record.id)
                try:
                    task = await self.start(record)
                except Exception:
                    self.active.discard(record.id)
                    logger.error("Run %s could not be started.", record.id)
                    await self.repository.set_status(record.id, "failed", reason_code="INTERNAL_ERROR", finished=True)
                    continue
                task.add_done_callback(lambda _task, run_id=record.id: self._finished(run_id))
                started.append(record.id)
        return started

    def _finished(self, run_id: str) -> None:
        self.active.discard(run_id)
        self.wake()

    async def _loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.fill()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("The run dispatcher failed (%s); retrying.", type(error).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_s)
            except asyncio.TimeoutError:
                pass

    def start_background(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
