#!/usr/bin/env python3
# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""End-to-end check of the sign-in chain of the development stack.

Browser flow through oauth2-proxy and the mock provider, API calls with the
session cookie, an API key on /m2m/, and refusal of forged identity headers.
Requires: scripts/dev-up.sh running, Python 3.9+ with httpx.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from html import unescape
from urllib.parse import urljoin

import httpx

SMOKE_REQUEST = "detect ssh brute force [smoke test]"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sign_in(base: str, username: "str | None" = None, claims: "str | None" = None) -> httpx.Client:
    """Sign in like a person would; None keeps the login page's pre-filled value."""
    client = httpx.Client(follow_redirects=False, timeout=15)
    response = client.get(base + "/api/me")
    hops = 0
    # Follow redirects by hand until the provider's login form appears.
    while response.status_code in (301, 302, 303, 307) and hops < 10:
        response = client.get(urljoin(str(response.url), response.headers["location"]))
        hops += 1
    if response.status_code != 200 or 'name="username"' not in response.text:
        fail(f"expected the provider login form, got HTTP {response.status_code}")
    form = re.search(r'<form[^>]*action="([^"]*)"', response.text)
    action = urljoin(str(response.url), unescape(form.group(1))) if form else str(response.url)
    if username is None:
        username = unescape(re.search(r'name="username"[^>]*value="([^"]*)"', response.text).group(1))
    if claims is None:
        claims = unescape(re.search(r'<textarea[^>]*name="claims"[^>]*>(.*?)</textarea>', response.text, re.S).group(1))
    response = client.post(action, data={"username": username, "claims": claims})
    hops = 0
    while response.status_code in (301, 302, 303, 307) and hops < 10:
        response = client.get(urljoin(str(response.url), response.headers["location"]))
        hops += 1
    return client


def check_websocket(base: str, admin: httpx.Client) -> None:
    """Events reach the signed-in browser; other origins are refused even with the cookie."""
    import asyncio

    try:
        import websockets
    except ImportError:
        print("websockets is not installed; WebSocket checks skipped.")
        return
    url = base.replace("http", "ws", 1) + "/api/ws"
    cookie = "; ".join(f"{c.name}={c.value}" for c in admin.cookies.jar)

    async def run() -> None:
        async with websockets.connect(url, additional_headers={"Cookie": cookie, "Origin": base}) as ws:
            created = admin.post(base + "/api/runs", json={"request": SMOKE_REQUEST}).json()
            while True:
                message = await asyncio.wait_for(ws.recv(), 60)
                if '"type":"awaiting_approval"' in message:
                    break
            admin.post(f"{base}/api/runs/{created['run_id']}/reject", json={"reason": "Smoke test cleanup."})
        try:
            async with websockets.connect(url, additional_headers={"Cookie": cookie, "Origin": "https://evil.example"}) as ws:
                await ws.recv()
        except websockets.exceptions.InvalidStatus:
            return
        fail("a cross-site WebSocket with a valid session cookie was accepted")

    asyncio.run(run())
    print("WebSocket events stream through the proxy; cross-site handshakes are refused.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:4180")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    anonymous = httpx.get(base + "/api/me", follow_redirects=False, timeout=15)
    if anonymous.status_code not in (302, 401, 403):
        fail(f"an anonymous request reached the API (HTTP {anonymous.status_code})")
    forged = httpx.get(base + "/api/me", follow_redirects=False, timeout=15,
                       headers={"X-Forwarded-Email": "dev@example.test", "X-Forwarded-User": "dev"})
    if forged.status_code == 200:
        fail("forged identity headers were accepted")
    print("Anonymous and forged-header requests are refused.")

    admin = sign_in(base)  # The pre-filled bootstrap administrator.
    me = admin.get(base + "/api/me")
    if me.status_code != 200 or me.json()["role"] != "admin":
        fail(f"the bootstrap admin could not sign in: {me.status_code} {me.text[:200]}")
    print(f"Signed in through oauth2-proxy as {me.json()['email']} ({me.json()['role']}, tenant {me.json()['tenant']['slug']}).")

    created = admin.post(base + "/api/api-keys", json={"name": "smoke", "role": "analyst", "expires_in_days": 1})
    if created.status_code != 201:
        fail(f"API key creation failed: {created.status_code} {created.text[:200]}")
    key = created.json()["key"]
    machine = httpx.get(base + "/m2m/me", headers={"X-API-Key": key}, timeout=15)
    if machine.status_code != 200 or machine.json()["kind"] != "api_key":
        fail(f"the API key did not work on /m2m/: {machine.status_code} {machine.text[:200]}")
    if httpx.get(base + "/m2m/me", headers={"X-API-Key": key[:-1] + "x"}, timeout=15).status_code != 401:
        fail("a wrong API key was accepted")
    if httpx.get(base + "/m2m/me", timeout=15).status_code != 401:
        fail("/m2m/ answered without a key")
    print("API key works on /m2m/; wrong or missing keys are refused.")

    # Leftovers of an earlier smoke run would keep the tenant's queue waiting.
    for stale in admin.get(base + "/api/runs").json()["runs"]:
        if stale["request"] == SMOKE_REQUEST and stale["status"] == "queued":
            admin.post(f"{base}/api/runs/{stale['run_id']}/cancel")
        if stale["request"] == SMOKE_REQUEST and stale["status"] == "awaiting_approval":
            admin.post(f"{base}/api/runs/{stale['run_id']}/reject", json={"reason": "Smoke test cleanup."})
    run = admin.post(base + "/api/runs", json={"request": SMOKE_REQUEST})
    if run.status_code != 202:
        fail(f"creating a run failed: {run.status_code} {run.text[:200]}")
    run_id = run.json()["run_id"]
    deadline = time.monotonic() + 60
    status = None
    while time.monotonic() < deadline:
        runs = {r["run_id"]: r for r in admin.get(base + "/api/runs").json()["runs"]}
        status = runs[run_id]["status"]
        if status not in ("queued", "running"):
            break
        time.sleep(0.5)
    if status != "awaiting_approval":
        fail(f"the run ended as {status}")
    print(f"Run {run_id} completed through the proxy and awaits approval.")
    if admin.post(f"{base}/api/runs/{run_id}/reject", json={"reason": "Smoke test cleanup."}).status_code != 200:
        fail("rejecting the smoke run failed")
    check_websocket(base, admin)

    stranger = sign_in(base, "stranger", '{"email": "stranger@example.test", "email_verified": true}')
    answer = stranger.get(base + "/api/runs")
    if answer.status_code != 403 or answer.json()["error"]["code"] != "NO_TENANT":
        fail(f"a user without a tenant got {answer.status_code}")
    print("A signed-in user without a tenant is refused (NO_TENANT).")
    print("Auth chain: OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
