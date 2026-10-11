# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Structured JSON logs, request IDs and an access log without secrets.

Every log line is one JSON object with the request ID and, when known, the
tenant, run and actor. Headers, query strings and bodies are never logged.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

from gatekeeper.api import AUDIT_ACTOR

REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
LOG_FIELDS: ContextVar[dict | None] = ContextVar("log_fields", default=None)
INCOMING_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
access_logger = logging.getLogger("toolsmith.access")


def bind_log(**fields) -> object:
    """Add fields (tenant_id, run_id) to every log line in the current context."""
    return LOG_FIELDS.set({**(LOG_FIELDS.get() or {}), **{k: str(v) for k, v in fields.items() if v is not None}})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
                   .replace("+00:00", "Z"),
                   "level": record.levelname.lower(), "logger": record.name, "message": record.getMessage()}
        request_id = REQUEST_ID.get()
        if request_id:
            payload["request_id"] = request_id
        actor = AUDIT_ACTOR.get()
        if actor:
            payload["actor"] = actor
        payload.update(LOG_FIELDS.get() or {})
        payload.update(getattr(record, "fields", {}))
        span = _current_span_ids()
        if span:
            payload.update(span)
        if record.exc_info and record.exc_info[0] is not None:
            # The type only: exception messages can contain request data or secrets.
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def _current_span_ids() -> dict | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}


def configure_logging(level: str, environment: str, log_format: str) -> None:
    """Production processes log JSON to stdout. Tests keep pytest's handlers untouched."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if environment == "test":
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if log_format == "json"
                         else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers = [handler]
    # Uvicorn's own loggers go through the same handler; its access log is
    # replaced by RequestContext, which logs without query strings.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RequestContext:
    """Assigns a request ID, returns it as X-Request-ID and writes one access line per request."""

    def __init__(self, app, metrics=None, mount_prefix: str = "") -> None:
        self.app = app
        self.metrics = metrics
        # FastAPI >= 0.140 keeps included routers whole, so route paths are relative to the mount.
        self.mount_prefix = mount_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if INCOMING_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = REQUEST_ID.set(request_id)
        started = time.monotonic()
        status = {"code": 500}

        async def tagged(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"x-request-id", request_id.encode("ascii"))]}
            elif message["type"] == "websocket.close":
                status["code"] = 101
            await send(message)

        try:
            await self.app(scope, receive, tagged)
        finally:
            duration = time.monotonic() - started
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            if (template != "unmatched" and self.mount_prefix and not template.startswith(self.mount_prefix + "/")
                    and scope.get("path", "").startswith(self.mount_prefix + "/")):
                template = self.mount_prefix + template
            method = scope.get("method", "WS")
            if self.metrics is not None:
                self.metrics.observe_request(method, template, status["code"], duration)
            if not (template.endswith("/health") or template.endswith("/ready")):
                access_logger.info("request", extra={"fields": {
                    "method": method, "route": template, "status": status["code"],
                    "duration_ms": round(duration * 1000, 1)}})
            REQUEST_ID.reset(token)
