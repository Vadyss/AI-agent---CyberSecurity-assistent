# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Small Standby HTTP relay; request and upstream response bodies stay opaque."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

UPSTREAM_URL = "https://openrouter.apify.actor/api/v1/chat/completions"
TIMEOUT_S = 180.0
HOP_HEADERS = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
               b"te", b"trailer", b"transfer-encoding", b"upgrade"}


def create_app(*, transport=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        token = os.environ.get("APIFY_TOKEN", "").strip()
        if not token:
            raise RuntimeError("APIFY_TOKEN is required in the Actor environment.")
        app.state.authorization = "Bearer " + token
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport,
                                     trust_env=False, follow_redirects=False) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    @app.get("/")  # Apify sends its Standby readiness probe to GET /.
    async def health():
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.body()
        headers = {"Authorization": app.state.authorization,
                   "Content-Type": request.headers.get("content-type", "application/json")}
        for name in ("accept", "content-encoding"):
            if name in request.headers:
                headers[name] = request.headers[name]
        try:
            # HTTPX also limits each I/O operation. The outer deadline limits the
            # entire upstream response, including a slow series of chunks.
            async with asyncio.timeout(TIMEOUT_S):
                async with app.state.client.stream("POST", UPSTREAM_URL, content=body, headers=headers) as upstream:
                    raw = b"".join([chunk async for chunk in upstream.aiter_raw()])
                    blocked = HOP_HEADERS | {name.strip().lower().encode("ascii")
                                             for name in upstream.headers.get("connection", "").split(",") if name.strip()}
                    response = Response(raw, status_code=upstream.status_code)
                    response.raw_headers = [(name, value) for name, value in upstream.headers.raw
                                            if name.lower() not in blocked]
                    return response
        except (TimeoutError, httpx.TimeoutException):
            return JSONResponse({"error": {"message": "Upstream request timed out."}}, status_code=504)
        except httpx.HTTPError:
            return JSONResponse({"error": {"message": "Upstream request failed."}}, status_code=502)

    return app


def main() -> None:
    # Suppress access, transport and error logs, including exception details.
    logging.disable(logging.CRITICAL)
    try:
        port = int(os.environ["ACTOR_WEB_SERVER_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError
    except (KeyError, ValueError):
        raise SystemExit("ACTOR_WEB_SERVER_PORT must be a valid TCP port.") from None
    uvicorn.run(create_app(), host="0.0.0.0", port=port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
