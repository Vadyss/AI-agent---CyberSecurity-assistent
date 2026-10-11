# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""The backend reaches the network-less sandbox only through a Unix socket."""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from gatekeeper.sandbox_client import SandboxClient
from sandbox import server


@pytest.fixture
def socket_path():
    # AF_UNIX paths are limited to about 100 bytes, so avoid pytest's long tmp_path.
    folder = Path(tempfile.mkdtemp(prefix="sbx-", dir="/tmp"))
    path = folder / "sandbox.sock"
    instance = uvicorn.Server(uvicorn.Config(server.app, uds=str(path), log_level="error"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started:
        if time.monotonic() > deadline:
            raise RuntimeError("The test sandbox did not start.")
        time.sleep(0.02)
    yield str(path)
    instance.should_exit = True
    thread.join(timeout=10)
    for leftover in folder.iterdir():
        leftover.unlink()
    folder.rmdir()


async def test_client_runs_a_skill_over_the_unix_socket(socket_path):
    client = SandboxClient("http://sandbox", timeout_s=10, uds=socket_path)
    try:
        assert await client.health() is True
        answer = await client.run("def run(inputs, params):\n    return [len(inputs)]\n", ["a", "b"], {},
                                  allowed_imports=[], timeout_s=5)
        assert answer["status"] == "ok"
        assert answer["result"] == [2]
    finally:
        await client.close()


def test_health_probe_uses_the_socket(socket_path):
    assert server.healthy(socket_path) is True
    assert server.healthy(socket_path + ".missing") is False


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="chgrp to another group needs root.")
def test_socket_directory_is_limited_to_the_client_group():
    folder = Path(tempfile.mkdtemp(prefix="sbx-", dir="/tmp"))
    try:
        server.prepare_socket_dir(str(folder / "sandbox.sock"), 10001)
        info = folder.stat()
        assert (info.st_mode & 0o777, info.st_gid) == (0o750, 10001)
    finally:
        folder.rmdir()
