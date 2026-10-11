# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from sandbox import server


def job(code: str = "def run(inputs, params):\n    return inputs\n", **overrides):
    value = {"job_id": uuid4().hex, "mode": "run", "files": {"skill.py": code},
             "entrypoint": "run", "inputs": [], "params": {}, "allowed_imports": [], "timeout_s": 2}
    value.update(overrides)
    return value


def execute(code: str, **kwargs):
    return server.execute_job_sync(job(code, **kwargs))


def test_health_and_original_entrypoint():
    from sandbox.main import app
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        answer = client.post("/execute", json=job(inputs=[{"value": "česky"}]))
        assert answer.status_code == 200
        assert answer.json()["result"] == [{"value": "česky"}]


def test_infinite_loop_is_wall_limited():
    started = time.monotonic()
    result = execute("def run(inputs, params):\n    while True:\n        pass\n", timeout_s=0.4)
    assert result["status"] == "timeout"
    assert time.monotonic() - started < 2.4
    assert "časový limit" in result["error"]


def test_memory_allocation_is_limited():
    result = execute("def run(inputs, params):\n    return ['x' * 1_000_000_000]\n")
    assert result["status"] == "error"
    assert "MemoryError" in result["error"] or "limit" in result["error"]


@pytest.mark.parametrize("module", ["socket", "os", "subprocess", "ctypes", "importlib"])
def test_runtime_import_guard_even_without_static_checks(module):
    result = execute(f"import {module}\ndef run(inputs, params):\n    return []\n")
    assert result["status"] == "error"
    assert f"Zakázaný import: {module}" in result["error"]


def test_import_allowlist_cannot_be_widened_by_job():
    assert execute("import os\ndef run(inputs, params):\n    return []", allowed_imports=["os"])["status"] == "rejected"


def test_runtime_open_guard():
    result = execute("def run(inputs, params):\n    return [open('/etc/passwd').read()]\n")
    assert result["status"] == "error"
    assert "PermissionError" in result["error"]
    assert "/etc/passwd" not in result["error"]


@pytest.mark.parametrize("code,imports", [
    ("import dataclasses\ndef run(inputs, params):\n    return [dataclasses.sys.modules['os'].getcwd()]", ["dataclasses"]),
    ("from operator import attrgetter\ndef run(inputs, params):\n    return [attrgetter('__class__')([])]", ["operator"]),
    ("def run(inputs, params):\n    return [().__class__.__mro__]", []),
    ("import json\ndef run(inputs, params):\n    return [json.codecs.open('/etc/passwd').read()]", ["json"]),
])
def test_internal_module_and_introspection_routes_are_blocked(code, imports):
    result = execute(code, allowed_imports=imports)
    assert result["status"] == "error"
    assert "PermissionError" in result["error"]


def test_huge_output_is_rejected():
    result = execute("def run(inputs, params):\n    return ['x' * 10_000_001]\n")
    assert result["status"] == "error"
    assert "10 MB" in result["error"]


@pytest.mark.parametrize("output", ["{}", "float('nan')", "[float('nan')]", "[set([1])]", "[object()]"])
def test_non_json_or_non_list_output(output):
    assert execute(f"def run(inputs, params):\n    return {output}")["status"] == "error"


def test_exception_type_message_and_clipping():
    result = execute("def run(inputs, params):\n    raise ValueError('chyba')")
    assert result["status"] == "error"
    assert result["error"] == "ValueError: chyba"
    long = execute("def run(inputs, params):\n    raise ValueError('x' * 3000)")
    assert len(long["error"]) <= 2000


def test_test_counts_failure_order_and_continue_after_exception():
    tests = """from skill import run
def test_first():
    assert run([], {}) == []
def test_third():
    assert False, 'třetí'
def test_second():
    raise RuntimeError('druhý')
def test_last():
    assert True
"""
    with TestClient(server.app) as client:
        answer = client.post("/execute", json=job(mode="test", files={"skill.py": "def run(inputs, params):\n    return []", "test_skill.py": tests}))
    result = answer.json()
    assert result["status"] == "ok"
    assert result["tests"]["total"] == 4
    assert result["tests"]["failed"] == 2
    assert [failure["name"] for failure in result["tests"]["failures"]] == ["test_third", "test_second"]
    assert result["tests"]["failures"][0]["error"] == "AssertionError: třetí"


def test_standard_library_warmup_and_discarded_output():
    code = """from datetime import datetime
from collections import defaultdict
import json
def run(inputs, params):
    print('ignored' * 1000)
    groups = defaultdict(int)
    groups['x'] += 1
    return [datetime.strptime('2026-10-08', '%Y-%m-%d').year, json.loads('{"ok": true}'), groups['x']]
"""
    result = execute(code, allowed_imports=["datetime", "collections", "json"])
    assert result["status"] == "ok", result
    assert result["result"] == [2026, {"ok": True}, 1]


@pytest.mark.parametrize("code,timeout,status", [
    ("def run(inputs, params):\n    return []", 2, "ok"),
    ("def run(inputs, params):\n    raise ValueError('x')", 2, "error"),
    ("def run(inputs, params):\n    while True:\n        pass", 0.15, "timeout"),
])
def test_clean_child_environment_and_pipe_transport(monkeypatch, code, timeout, status):
    monkeypatch.setenv("APIFY_TOKEN", "test-secret-that-must-not-pass")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "another-secret")
    original_popen = subprocess.Popen
    observed = []

    def spy(*args, **kwargs):
        observed.append(kwargs)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(server.subprocess, "Popen", spy)
    result = server.execute_job_sync(job(code, timeout_s=timeout))
    assert result["status"] == status
    spawn = observed[0]
    assert spawn["env"] == {"PATH": os.defpath}
    assert spawn["start_new_session"] is True
    # preexec_fn is unsafe in a threaded server; limits are applied by the runner.
    assert "preexec_fn" not in spawn
    # Jobs travel through pipes, so no job file is ever written to disk.
    assert spawn["cwd"] == "/"
    assert spawn["stdin"] == subprocess.PIPE and spawn["stdout"] == subprocess.PIPE


def _track_children(monkeypatch):
    original_popen = subprocess.Popen
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0}

    class TrackedProcess:
        def __init__(self, *args, **kwargs):
            self.process = original_popen(*args, **kwargs)
            self.done = False
            with lock:
                counts["active"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])

        def __getattr__(self, name):
            return getattr(self.process, name)

        def wait(self, *args, **kwargs):
            result = self.process.wait(*args, **kwargs)
            if not self.done:
                self.done = True
                with lock:
                    counts["active"] -= 1
            return result

    monkeypatch.setattr(server.subprocess, "Popen", TrackedProcess)
    return counts


LOOP = "def run(inputs, params):\n    while True:\n        pass"


def test_no_more_than_two_real_children_at_a_time(monkeypatch):
    counts = _track_children(monkeypatch)
    jobs = [job(LOOP, timeout_s=0.2, tenant_id=f"tenant-{n}") for n in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(server.execute_job_sync, jobs))
    assert [answer["status"] for answer in answers] == ["timeout"] * 4
    assert counts == {"active": 0, "peak": 2}


def test_one_tenant_cannot_occupy_every_slot(monkeypatch):
    counts = _track_children(monkeypatch)
    jobs = [job(LOOP, timeout_s=0.2, tenant_id="noisy")] * 3
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(server.execute_job_sync, jobs))
    assert [answer["status"] for answer in answers] == ["timeout"] * 3
    assert counts == {"active": 0, "peak": 1}


@pytest.mark.parametrize("tenant", ["", "../x", "a" * 65, 7, "tenant id"])
def test_invalid_tenant_is_rejected(tenant):
    assert server.execute_job_sync(job(tenant_id=tenant))["status"] == "rejected"


@pytest.mark.parametrize("env", [{"SANDBOX_ISOLATION": "chroot"}, {"SANDBOX_UID_BASE": "0"},
                                 {"SANDBOX_MAX_JOBS": "0"}, {"SANDBOX_MAX_JOBS_PER_TENANT": "3"}])
def test_invalid_sandbox_configuration_fails_closed(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        server.SandboxConfig.from_env()


def test_uid_isolation_refuses_to_start_without_root(monkeypatch):
    monkeypatch.setattr(server, "CONFIG", server.SandboxConfig(isolation="uid"))
    monkeypatch.setattr(server.os, "geteuid", lambda: 1000)
    with pytest.raises(SystemExit):
        server.preflight()


def test_real_resource_limits_in_child(monkeypatch, tmp_path):
    # A trusted probe replaces the runner solely in this test. It applies the
    # runner's own limit function with the arguments the server passes, then
    # reports what the kernel actually enforces in the child.
    runner_dir = Path(server.RUNNER_PATH).parent
    probe = tmp_path / "probe.py"
    probe.write_text(f"""import json, os, resource, sys
sys.path.insert(0, {str(runner_dir)!r})
import runner
runner.apply_limits(int(sys.argv[1]), sys.argv[2] == "1")
kinds = [resource.RLIMIT_CPU, resource.RLIMIT_AS, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE, resource.RLIMIT_CORE]
result = dict(status='ok', result=[resource.getrlimit(k)[0] for k in kinds],
              tests=dict(total=0, failed=0, failures=[]), error=None, duration_ms=0)
os.read(0, 10_000_000)
os.write(1, json.dumps(result).encode())
""")
    monkeypatch.setattr(server, "RUNNER_PATH", probe)
    assert server.execute_job_sync(job(timeout_s=2))["result"] == [3, 256 * 1024 * 1024, 0, 64, 0]


def test_skill_output_cannot_corrupt_result_pipe():
    # print() is discarded in-process; the runner also points fds 1 and 2 at
    # /dev/null, so even raw writes to stdout cannot reach the result pipe.
    code = "def run(inputs, params):\n    print('{\"status\": \"ok\"}' * 100)\n    return [1]\n"
    result = execute(code)
    assert result["status"] == "ok", result
    assert result["result"] == [1]


def test_large_input_is_streamed_without_deadlock():
    lines = ["x" * 1000] * 4000  # About 4 MB: far larger than a pipe buffer.
    result = execute("def run(inputs, params):\n    return [len(inputs)]\n", inputs=lines, timeout_s=10)
    assert result["status"] == "ok", result
    assert result["result"] == [4000]


def test_each_job_has_fresh_module_state():
    code = "counter = []\ndef run(inputs, params):\n    counter.append(1)\n    return counter"
    assert execute(code)["result"] == [1]
    assert execute(code)["result"] == [1]


@pytest.mark.parametrize("overrides", [
    {"files": {"../skill.py": ""}}, {"files": {"/tmp/skill.py": ""}},
    {"files": {"skill.py": 42}}, {"files": {"test_skill.py": ""}},
    {"mode": "other"}, {"mode": []}, {"mode": "test"}, {"entrypoint": "eval"},
    {"inputs": {}}, {"params": []}, {"allowed_imports": "re"},
    {"timeout_s": 0}, {"timeout_s": -1}, {"timeout_s": 31},
    {"timeout_s": True}, {"timeout_s": float("nan")}, {"job_id": ""},
    {"timeout_s": 10 ** 1000},
    {"inputs": ["x" * 5_000_001]},
])
def test_rejected_jobs_never_spawn(overrides, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A rejected job started a child")
    monkeypatch.setattr(server.subprocess, "Popen", forbidden)
    assert server.execute_job_sync(job(**overrides))["status"] == "rejected"


def test_bad_json_and_body_limit_return_protocol_200():
    with TestClient(server.app) as client:
        for body in (b"{", b"[]", b"x" * 8_000_001):
            answer = client.post("/execute", content=body)
            assert answer.status_code == 200
            assert answer.json()["status"] == "rejected"


needs_root = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0,
                                reason="UID isolation needs Linux and root, as in the sandbox container.")


@pytest.fixture
def isolated(monkeypatch):
    config = server.SandboxConfig(isolation="uid", uid_base=23000, max_jobs=2, max_jobs_per_tenant=1)
    monkeypatch.setattr(server, "CONFIG", config)
    monkeypatch.setattr(server, "SCHEDULER", server.Scheduler(config.max_jobs, config.max_jobs_per_tenant))
    server.preflight()
    return config


@pytest.fixture
def public_probe(monkeypatch):
    # Job UIDs cannot read pytest's private tmp_path, so probes live in a
    # world-readable directory for the duration of the test.
    folder = Path(tempfile.mkdtemp(prefix="probe-", dir="/tmp"))
    folder.chmod(0o755)

    def install(source: str) -> None:
        probe = folder / "probe.py"
        probe.write_text(source)
        probe.chmod(0o644)
        monkeypatch.setattr(server, "RUNNER_PATH", probe)

    yield install
    shutil.rmtree(folder)


REPORT = """
import sys
sys.path.insert(0, %r)
import runner
runner.apply_limits(int(sys.argv[1]), sys.argv[2] == "1")

def report(value):
    import json, os
    os.read(0, 10_000_000)
    os.write(1, json.dumps(dict(status='ok', result=value, tests=dict(total=0, failed=0, failures=[]),
                                error=None, duration_ms=0)).encode())
""" % str(Path(server.__file__).resolve().parent)


@needs_root
def test_isolated_job_runs_as_private_uid_without_capabilities(isolated, public_probe):
    public_probe(REPORT + """
import os, resource
status = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines() if ':' in line)
report([os.getuid(), os.geteuid(), os.getgid(), os.getgroups(), status['CapEff'].strip(),
        status['CapPrm'].strip(), status['NoNewPrivs'].strip() if 'NoNewPrivs' in status else None,
        resource.getrlimit(resource.RLIMIT_NPROC)[0]])
""")
    result = server.execute_job_sync(job())
    uid = result["result"][0]
    assert uid in (23000, 23001)
    assert result["result"][:4] == [uid, uid, uid, []]
    assert result["result"][4] == result["result"][5] == "0000000000000000"
    assert result["result"][7] == 16


@needs_root
def test_isolated_job_leaves_no_process_behind(isolated, public_probe):
    # A probe that escapes its process group stands in for code that broke
    # out of the Python guards; the UID sweep must still find it.
    public_probe(REPORT + """
import os, time
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        time.sleep(60)
    os._exit(0)
time.sleep(0.2)
report([os.getuid()])
""")
    started = time.monotonic()
    result = server.execute_job_sync(job(timeout_s=5))
    assert result["status"] == "ok", result
    # The orphan keeps the result pipe open; the server must not wait for it.
    assert time.monotonic() - started < 2
    assert server._processes_of(result["result"][0]) == []


@needs_root
def test_real_skill_runs_under_isolation(isolated):
    code = "import re\ndef run(inputs, params):\n    return [bool(re.match('a', x)) for x in inputs]\n"
    result = execute(code, inputs=["abc", "xyz"], allowed_imports=["re"])
    assert result["status"] == "ok", result
    assert result["result"] == [True, False]


@needs_root
def test_isolated_job_cannot_write_files(isolated, public_probe):
    public_probe(REPORT + """
import os
outcome = []
for path in ('/tmp/leak', '/dev/shm/leak', '/proc/self/cwd/leak'):
    try:
        with open(path, 'w') as handle:
            handle.write('x')
        outcome.append('written')
    except (OSError, IOError) as error:
        outcome.append(type(error).__name__)
report(outcome)
""")
    result = server.execute_job_sync(job())
    # RLIMIT_FSIZE=0 turns every write into EFBIG, wherever a file can be created.
    assert result["status"] == "ok", result
    assert "written" not in result["result"], result


@pytest.mark.parametrize("code", [
    "def run(inputs, params):\n    item = (x for x in inputs)\n    return [str(item.gi_frame)]\n",
    "def run(inputs, params):\n    return ['{0.real}'.format(1)]\n",
    "def run(inputs, params):\n    return [params['t'].format(1)]\n",
    "def run(inputs, params):\n    return ['{x}'.format_map({'x': 1})]\n",
])
def test_runtime_rejects_introspection_without_gatekeeper(code):
    # The runner re-checks submitted code, so a caller that skips the
    # gatekeeper still cannot use these routes.
    result = execute(code, params={"t": "{0}"})
    assert result["status"] == "error"
    assert "PermissionError" in result["error"]


def test_runtime_allows_plain_format_templates():
    result = execute("def run(inputs, params):\n    return ['{}-{:>3}'.format('a', 7)]\n")
    assert result == {**result, "status": "ok", "result": ["a-  7"]}


def test_runner_and_policy_share_the_introspection_denylist():
    from gatekeeper.policy import BASELINE_FORBIDDEN_ATTRIBUTES
    from sandbox import runner
    assert runner.FORBIDDEN_ATTRIBUTES == BASELINE_FORBIDDEN_ATTRIBUTES
