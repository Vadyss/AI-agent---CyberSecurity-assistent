# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Nonblocking broadcast; each subscriber has its own bounded send queue."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(eq=False)
class Subscriber:
    ws: WebSocket
    queue: asyncio.Queue
    tenant_id: object = None
    task: asyncio.Task | None = None
    disconnected: bool = False


class WebSocketHub:
    def __init__(self, queue_size: int = 1000, send_timeout_s: float = 5):
        self.queue_size = queue_size
        self.send_timeout_s = send_timeout_s
        self.clients: set[Subscriber] = set()
        self.cleanup_tasks: set[asyncio.Task] = set()

    async def connect(self, ws: WebSocket, tenant_id: object) -> Subscriber:
        await ws.accept()
        client = Subscriber(ws, asyncio.Queue(maxsize=self.queue_size), tenant_id)
        self.clients.add(client)
        client.task = asyncio.create_task(self._send(client))
        return client

    def broadcast(self, event: dict, tenant_id: object) -> None:
        """Deliver only to subscribers of the tenant that owns the event."""
        for client in tuple(self.clients):
            if client.tenant_id != tenant_id:
                continue
            try:
                client.queue.put_nowait(event)
            except asyncio.QueueFull:
                self.clients.discard(client)
                task = asyncio.create_task(self.disconnect(client))
                self.cleanup_tasks.add(task)
                task.add_done_callback(self.cleanup_tasks.discard)

    async def _send(self, client: Subscriber) -> None:
        try:
            while True:
                event = await client.queue.get()
                await asyncio.wait_for(client.ws.send_json(event), timeout=self.send_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            await self.disconnect(client)

    async def disconnect(self, client: Subscriber) -> None:
        if client.disconnected:
            return
        client.disconnected = True
        self.clients.discard(client)
        if client.task and client.task is not asyncio.current_task():
            client.task.cancel()
            await asyncio.gather(client.task, return_exceptions=True)
        try:
            await asyncio.wait_for(client.ws.close(), timeout=self.send_timeout_s)
        except Exception:
            pass

    async def close(self) -> None:
        await asyncio.gather(*(self.disconnect(c) for c in tuple(self.clients)), return_exceptions=True)
        if self.cleanup_tasks:
            await asyncio.gather(*tuple(self.cleanup_tasks), return_exceptions=True)
