# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""HTTP boundary and per-job process isolation for untrusted Python skills."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Iterator

from fastapi import FastAPI, Request

MAX_BODY_BYTES = 8_000_000
MAX_INPUT_BYTES = 5_000_000
MAX_OUTPUT_BYTES = 10_000_000
ALLOWED_MODULES = frozenset({
    "re", "datetime", "collections", "itertools", "functools", "math",
    "statistics", "ipaddress", "json", "typing", "dataclasses", "bisect",
    "heapq", "string", "operator", "enum", "decimal", "fractions",
    "hashlib", "base64", "binascii",
})
RUNNER_PATH = Path(__file__).with_name("runner.py").resolve()
TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxConfig:
    # "uid": every job runs as a private, unprivileged UID (production image).
    # "none": jobs share the supervisor's UID (local development without root).
    isolation: str = "none"
    uid_base: int = 20000
    max_jobs: int = 2
    max_jobs_per_tenant: int = 1

    @classmethod
    def from_env(cls) -> SandboxConfig:
        config = cls(isolation=os.environ.get("SANDBOX_ISOLATION", "none"),
                     uid_base=int(os.environ.get("SANDBOX_UID_BASE", "20000")),
                     max_jobs=int(os.environ.get("SANDBOX_MAX_JOBS", "2")),
                     max_jobs_per_tenant=int(os.environ.get("SANDBOX_MAX_JOBS_PER_TENANT", "1")))
        if config.isolation not in {"uid", "none"}:
            raise ValueError("SANDBOX_ISOLATION must be uid or none.")
        if not 1000 <= config.uid_base <= 2**31 - 1000 or not 1 <= config.max_jobs <= 64:
            raise ValueError("Invalid sandbox UID range or job limit.")
        if not 1 <= config.max_jobs_per_tenant <= config.max_jobs:
            raise ValueError("Invalid per-tenant job limit.")
        return config


class Scheduler:
    """Global job slots plus a per-tenant cap, so one tenant cannot hold every slot."""

    def __init__(self, slots: int, per_tenant: int) -> None:
        self.per_tenant = per_tenant
        self._free: queue.SimpleQueue[int] = queue.SimpleQueue()
        for index in range(slots):
            self._free.put(index)
        self._lock = threading.Lock()
        self._tenants: dict[str, list[Any]] = {}

    @contextmanager
    def slot(self, tenant: str, release: Any) -> Iterator[int]:
        with self._lock:
            entry = self._tenants.setdefault(tenant, [0, threading.BoundedSemaphore(self.per_tenant)])
            entry[0] += 1
        try:
            with entry[1]:
                index = self._free.get()
                try:
                    yield index
                finally:
                    # The slot returns to the pool only after its UID is clean.
                    if release(index):
                        self._free.put(index)
        finally:
            with self._lock:
                entry[0] -= 1
                if entry[0] == 0:
                    del self._tenants[tenant]


CONFIG = SandboxConfig.from_env()
SCHEDULER = Scheduler(CONFIG.max_jobs, CONFIG.max_jobs_per_tenant)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def response(status: str, *, error: str | None = None, result: Any = None,
             tests: dict[str, Any] | None = None, duration_ms: int = 0) -> dict[str, Any]:
    return {"status": status, "result": result,
            "tests": tests or {"total": 0, "failed": 0, "failures": []},
            "error": error[:2000] if error else None, "duration_ms": duration_ms}


def validate_job(job: Any) -> str | None:
    if not isinstance(job, dict):
        return "Úloha musí být JSON objekt."
    if not isinstance(job.get("job_id"), str) or not 1 <= len(job["job_id"]) <= 128:
        return "Neplatný identifikátor úlohy."
    tenant = job.get("tenant_id", "default")
    if not isinstance(tenant, str) or not TENANT_RE.fullmatch(tenant):
        return "Neplatný identifikátor tenanta."
    if job.get("mode") not in ("run", "test") or job.get("entrypoint", "run") != "run":
        return "Neplatný režim nebo vstupní bod úlohy."
    files = job.get("files")
    if not isinstance(files, dict) or not files or set(files) - {"skill.py", "test_skill.py"}:
        return "Neplatné názvy souborů úlohy."
    if "skill.py" not in files or (job["mode"] == "test" and "test_skill.py" not in files):
        return "Chybí soubor dovednosti nebo testů."
    if any(not isinstance(source, str) for source in files.values()):
        return "Obsah souborů musí být text."
    if not isinstance(job.get("inputs", []), list) or not isinstance(job.get("params", {}), dict):
        return "Neplatný vstup nebo parametry."
    imports = job.get("allowed_imports", [])
    if not isinstance(imports, list) or any(not isinstance(name, str) or name not in ALLOWED_MODULES
                                            for name in imports):
        return "Import není v bezpečném seznamu modulů."
    timeout = job.get("timeout_s", 10)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30 or not math.isfinite(timeout):
        return "Časový limit musí být větší než 0 a nejvýš 30 sekund."
    try:
        body = json.dumps(job, ensure_ascii=False, allow_nan=False).encode("utf-8")
        inputs = json.dumps(job.get("inputs", []), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return "Úlohu nelze serializovat do JSON."
    if len(body) > MAX_BODY_BYTES or len(inputs) > MAX_INPUT_BYTES:
        return "Úloha překračuje limit velikosti vstupu."
    return None


def _spawn(timeout_s: float, uid: int | None = None) -> subprocess.Popen:
    cpu_seconds = math.ceil(timeout_s) + 1
    # No preexec_fn: it is unsafe in a threaded server. The runner lowers its
    # own limits before it reads the untrusted job from stdin. With a private
    # UID, the kernel drops every capability when the child leaves UID 0.
    identity: dict[str, Any] = {} if uid is None else {"user": uid, "group": uid, "extra_groups": []}
    return subprocess.Popen(
        [sys.executable, "-I", "-B", "-S", str(RUNNER_PATH), str(cpu_seconds), "0" if uid is None else "1"],
        cwd="/", env={"PATH": os.defpath}, start_new_session=True, close_fds=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **identity,
    )


def _processes_of(uid: int) -> list[int]:
    """Live processes whose real, effective, saved or filesystem UID is ``uid``."""
    found = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/status", "rb") as handle:
                status = handle.read()
        except OSError:  # The process exited during the scan.
            continue
        fields = dict(line.split(b":", 1) for line in status.splitlines() if b":" in line)
        if fields.get(b"State", b"").split()[:1] == [b"Z"]:
            continue  # Zombies hold no resources and ignore signals.
        if uid in (int(value) for value in fields.get(b"Uid", b"").split()):
            found.append(int(entry.name))
    return found


def reap_uid(uid: int) -> bool:
    """Kill every process of a job UID, including ones that left the process group."""
    for _ in range(200):
        pids = _processes_of(uid)
        if not pids:
            return True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.005)
    return False


def _fatal(message: str) -> None:
    # A UID that cannot be cleaned must never be reused. Exit so the container
    # restart policy brings up a clean supervisor.
    logger.critical(message)
    os._exit(70)


def _release(index: int) -> bool:
    if CONFIG.isolation != "uid":
        return True
    if reap_uid(CONFIG.uid_base + index):
        return True
    _fatal("A sandbox job UID could not be cleaned up; refusing to reuse it.")
    return False


def preflight() -> None:
    """Fail closed when UID isolation is requested but cannot be enforced."""
    if CONFIG.isolation != "uid":
        logger.warning("Sandbox UID isolation is disabled; use only for local development.")
        return
    if os.geteuid() != 0:
        raise SystemExit("SANDBOX_ISOLATION=uid needs the supervisor to start as UID 0.")
    for index in range(CONFIG.max_jobs):
        if not reap_uid(CONFIG.uid_base + index):
            raise SystemExit("Leftover processes in the sandbox UID range could not be stopped.")


def _exchange(process: subprocess.Popen, payload: bytes, deadline: float) -> bytes | str:
    """Feed the job and collect the bounded result without blocking on either pipe.

    The exchange ends when the runner itself exits. A forked descendant that
    still holds the pipe open therefore cannot stall the server until timeout.
    """
    output = bytearray()
    view = memoryview(payload)
    stdin, stdout = process.stdin, process.stdout
    assert stdin is not None and stdout is not None
    os.set_blocking(stdin.fileno(), False)
    os.set_blocking(stdout.fileno(), False)

    def drain() -> bool:
        while True:
            try:
                chunk = os.read(stdout.fileno(), 65536)
            except BlockingIOError:
                return True
            if not chunk:
                return False
            output.extend(chunk)
            if len(output) > MAX_OUTPUT_BYTES + 4096:
                raise OverflowError

    try:
        with selectors.DefaultSelector() as selector:
            selector.register(stdin, selectors.EVENT_WRITE)
            selector.register(stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout"
                for key, _ in selector.select(min(remaining, 0.05)):
                    if key.fileobj is stdin:
                        try:
                            view = view[os.write(stdin.fileno(), view[:65536]):]
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            view = view[:0]
                        if not view:
                            selector.unregister(stdin)
                            stdin.close()
                    elif not drain():
                        # EOF: every writer is gone, so only the exit status remains.
                        try:
                            process.wait(timeout=max(0.0, deadline - time.monotonic()))
                        except subprocess.TimeoutExpired:
                            return "timeout"
                        return bytes(output)
                if process.poll() is not None:
                    drain()
                    return bytes(output)
    except OverflowError:
        return "too_large"


def _terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # The group already exited.
        pass
    for pipe in (process.stdin, process.stdout):
        if pipe is not None and not pipe.closed:
            pipe.close()
    process.wait()


def execute_job_sync(job: Any) -> dict[str, Any]:
    """Execute in a fresh Python process; never import submitted source here."""
    started = time.monotonic()
    validation_error = validate_job(job)
    if validation_error:
        return response("rejected", error=validation_error)
    payload = json.dumps(job, ensure_ascii=False, allow_nan=False).encode("utf-8")
    timeout_s = float(job.get("timeout_s", 10))
    with SCHEDULER.slot(job.get("tenant_id", "default"), _release) as index:
        uid = CONFIG.uid_base + index if CONFIG.isolation == "uid" else None
        process = _spawn(timeout_s, uid)
        try:
            output = _exchange(process, payload, time.monotonic() + timeout_s)
        finally:
            _terminate(process)
    elapsed = round((time.monotonic() - started) * 1000)
    if output == "timeout":
        return response("timeout", error="Překročen časový limit.", duration_ms=elapsed)
    try:
        if output == "too_large":
            raise ValueError("Výstup překračuje limit velikosti.")
        answer = json.loads(output)
        if not isinstance(answer, dict) or answer.get("status") not in {"ok", "error"}:
            raise ValueError("Neplatný výstup runneru.")
    except (ValueError, RecursionError):
        answer = response("error", error="Runner selhal nebo překročil limit zdrojů.")
    answer["duration_ms"] = elapsed
    return answer


async def execute_job(job: Any) -> dict[str, Any]:
    return await asyncio.to_thread(execute_job_sync, job)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/execute")
async def execute(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return response("rejected", error="Tělo úlohy překračuje 8 MB.")
        body.extend(chunk)
    try:
        job = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return response("rejected", error="Neplatný JSON úlohy.")
    return await execute_job(job)


def prepare_socket_dir(socket_path: str, client_gid: int) -> None:
    """Only the backend's group may reach the socket; job UIDs cannot even traverse."""
    folder = Path(socket_path).parent
    if folder.is_symlink() or not folder.is_dir():
        raise SystemExit("The sandbox socket directory must be a real directory.")
    # chgrp works without CAP_CHOWN because compose adds the client group to
    # the supervisor's supplementary groups.
    os.chown(folder, os.geteuid(), client_gid)
    os.chmod(folder, 0o750)


def healthy(socket_path: str | None) -> bool:
    import http.client
    import socket

    try:
        if socket_path:
            connection = http.client.HTTPConnection("sandbox", timeout=2)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(socket_path)
            connection.sock = sock
        else:
            connection = http.client.HTTPConnection("127.0.0.1", int(os.environ.get("SANDBOX_PORT", "8000")), timeout=2)
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    except OSError:
        return False


def main() -> None:
    socket_path = os.environ.get("SANDBOX_SOCKET") or None
    if sys.argv[1:] == ["--health"]:
        raise SystemExit(0 if healthy(socket_path) else 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    preflight()
    import uvicorn

    if socket_path:
        prepare_socket_dir(socket_path, int(os.environ.get("SANDBOX_CLIENT_GID", "10001")))
        uvicorn.run(app, uds=socket_path, access_log=False)
    else:
        uvicorn.run(app, host=os.environ.get("SANDBOX_HOST", "0.0.0.0"),
                    port=int(os.environ.get("SANDBOX_PORT", "8000")), access_log=False)


if __name__ == "__main__":
    main()
