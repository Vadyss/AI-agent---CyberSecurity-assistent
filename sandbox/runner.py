# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Disposable child process; trusted runtime is separate from skill namespaces."""
from __future__ import annotations

import ast
import builtins
import contextlib
import datetime
import dis
import importlib
import json
import os
import re
import resource
import string
import sys
import types
from typing import Any

MAX_OUTPUT_BYTES = 10_000_000
MEMORY_BYTES = 256 * 1024 * 1024
MAX_INPUT_BYTES = 8_000_000
_ORIGINAL_OPEN = builtins.open
_ORIGINAL_IMPORT = builtins.__import__
_SOURCES: frozenset[str] = frozenset()


class DiscardOutput:
    """Discard writes without retaining attacker-controlled text in memory."""
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        pass


def _skill_on_stack() -> bool:
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename in _SOURCES:
            return True
        frame = frame.f_back
    return False


def _audit(event: str, args: tuple[Any, ...]) -> None:
    # This also blocks I/O reached through standard-library aliases. Hooks are
    # installed once and cannot be removed by submitted code.
    forbidden = (event in {"open", "exec", "compile"} or event.startswith(("socket.", "subprocess.", "ctypes."))
                 or event in {"os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.exec",
                              "os.spawn", "os.remove", "os.rmdir", "os.rename", "os.mkdir", "os.chdir",
                              "os.listdir", "os.scandir", "os.chmod", "os.chown", "os.link", "os.symlink"})
    if forbidden and _skill_on_stack():
        raise PermissionError("Sandbox nepovoluje soubory, síť ani podprocesy.")


def _error(exc: BaseException, limit: int = 2000) -> str:
    message = f"{type(exc).__name__}: {exc}"
    # User-raised exceptions may contain absolute paths; do not export them.
    message = re.sub(r"(?<!\w)/(?:[^\s'\"<>:]+/?)+", "[cesta]", message)
    return "".join(c for c in message if ord(c) >= 32 or c in "\n\t")[:limit]


# Keep in sync with gatekeeper.policy.BASELINE_FORBIDDEN_ATTRIBUTES.
FORBIDDEN_ATTRIBUTES = frozenset(
    "gi_frame gi_code gi_yieldfrom cr_frame cr_code cr_await ag_frame ag_code ag_await f_back f_globals "
    "f_locals f_builtins f_code f_trace tb_frame tb_next co_code co_consts co_names func_globals func_code "
    "format_map".split())


def _plain_format_template(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    try:
        parts = list(string.Formatter().parse(node.value))
    except ValueError:
        return False
    return all(field is None or ("." not in field and "[" not in field) for _, field, _, _ in parts) \
        and all(not spec or "{" not in spec for _, _, spec, _ in parts)


def _runtime_ast_check(source: str, filename: str) -> ast.Module:
    tree = ast.parse(source, filename)
    # Import/open hooks remain active even when callers bypass the gatekeeper.
    # Deny introspection that could reach this trusted runner or its globals.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRIBUTES):
            raise PermissionError("Sandbox nepovoluje introspekci atributů.")
        if isinstance(node, ast.Attribute) and node.attr == "format" and not _plain_format_template(node.value):
            raise PermissionError("Sandbox povoluje str.format jen na literálu bez atributů.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise PermissionError("Sandbox nepovoluje introspekci jmen.")
    return tree


def _filename(name: str) -> str:
    # Virtual path: sources never touch the disk, and the audit hook uses it to
    # recognise frames that belong to submitted code.
    return f"/job/{name}.py"


def _load_module(name: str, source: str, safe_builtins: dict[str, Any]) -> types.ModuleType:
    filename = _filename(name)
    tree = _runtime_ast_check(source, filename)
    module = types.ModuleType(name)
    module.__dict__["__builtins__"] = safe_builtins
    module.__dict__["__file__"] = filename
    sys.modules[name] = module
    exec(compile(tree, filename, "exec"), module.__dict__)
    return module


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    global _SOURCES
    files = job["files"]
    _SOURCES = frozenset(_filename(name) for name in ("skill", "test_skill"))
    allowed = frozenset(job.get("allowed_imports", []))
    # Preload and warm lazy imports before the I/O guard is installed.
    for name in allowed:
        importlib.import_module(name)
    datetime.datetime.strptime("2026-10-08", "%Y-%m-%d")
    datetime.datetime.fromisoformat("2026-10-08T00:00:00+02:00").timestamp()

    # Standard modules sometimes expose sys/os as implementation details.
    # A facade prevents those objects from becoming a route around the hooks.
    views: dict[str, types.ModuleType] = {}

    class ModuleView(types.ModuleType):
        def __getattribute__(self, name: str) -> Any:
            if name.startswith("_"):
                raise PermissionError("Sandbox nepovoluje soukromé atributy modulů.")
            value = getattr(sys.modules[types.ModuleType.__getattribute__(self, "__name__")], name)
            if isinstance(value, types.ModuleType):
                if value.__name__ not in allowed:
                    raise PermissionError("Sandbox nepovoluje přístup k interním modulům.")
                return module_view(value)
            if types.ModuleType.__getattribute__(self, "__name__") == "operator" and name in {"attrgetter", "methodcaller"}:
                def guarded_factory(*args: Any, **kwargs: Any) -> Any:
                    selectors = args if name == "attrgetter" else args[:1]
                    if any(not isinstance(selector, str) or any(part.startswith("_") for part in selector.split("."))
                           for selector in selectors):
                        raise PermissionError("Sandbox nepovoluje soukromé atributy.")
                    return value(*args, **kwargs)
                return guarded_factory
            return value

    def module_view(module: types.ModuleType) -> types.ModuleType:
        if module.__name__ not in views:
            views[module.__name__] = ModuleView(module.__name__)
        return views[module.__name__]

    def import_guard(name: str, globals: dict[str, Any] | None = None,
                     locals: dict[str, Any] | None = None, fromlist: tuple = (), level: int = 0) -> Any:
        caller = (globals or {}).get("__name__", "")
        if caller in {"skill", "test_skill"}:
            # datetime's C implementation calls __import__ with the skill's
            # globals even after _strptime was warmed. Permit that cached C
            # dependency, while an explicit IMPORT_NAME remains forbidden.
            frame = sys._getframe(1)
            if (name == "_strptime" and "datetime" in allowed and name in sys.modules
                    and not level and frame.f_code.co_code[frame.f_lasti] != dis.opmap["IMPORT_NAME"]):
                return sys.modules[name]
            if level or name not in allowed and not (name == "skill" and caller == "test_skill"):
                raise ImportError(f"Zakázaný import: {name}.")
            if name in allowed:
                return module_view(sys.modules[name])
        return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)

    def open_guard(*args: Any, **kwargs: Any) -> Any:
        if _skill_on_stack():
            raise PermissionError("Sandbox nepovoluje přístup k souborům.")
        return _ORIGINAL_OPEN(*args, **kwargs)

    builtins.__import__ = import_guard
    builtins.open = open_guard
    safe = dict(builtins.__dict__)
    for name in ("eval", "exec", "compile", "input", "breakpoint", "globals", "locals", "vars",
                 "getattr", "setattr", "delattr", "dir", "help", "exit", "quit", "memoryview"):
        safe.pop(name, None)
    sys.addaudithook(_audit)
    answer = {"status": "ok", "result": None,
              "tests": {"total": 0, "failed": 0, "failures": []}, "error": None, "duration_ms": 0}
    try:
        with contextlib.redirect_stdout(DiscardOutput()), contextlib.redirect_stderr(DiscardOutput()):
            skill = _load_module("skill", files["skill.py"], safe)
            if job["mode"] == "run":
                answer["result"] = skill.run(job.get("inputs", []), job.get("params", {}))
                if not isinstance(answer["result"], list):
                    raise TypeError("Výstup dovednosti musí být seznam.")
            else:
                tests = _load_module("test_skill", files["test_skill.py"], safe)
                for name, function in list(tests.__dict__.items()):
                    if (not name.startswith("test_") or not isinstance(function, types.FunctionType)
                            or function.__module__ != "test_skill"):
                        continue
                    answer["tests"]["total"] += 1
                    try:
                        function()
                    except BaseException as exc:
                        answer["tests"]["failed"] += 1
                        if len(answer["tests"]["failures"]) < 100:
                            answer["tests"]["failures"].append({"name": name[:200], "error": _error(exc, 500)})
            encoded = json.dumps(answer, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_OUTPUT_BYTES:
                raise ValueError("Výstup překračuje limit 10 MB.")
    except BaseException as exc:
        answer["status"] = "error"
        answer["result"] = None
        answer["error"] = _error(exc)
    return answer


def apply_limits(cpu_seconds: int, isolated: bool) -> None:
    """Lower resource limits before any untrusted byte is parsed or executed."""
    limits = [(resource.RLIMIT_CPU, cpu_seconds), (resource.RLIMIT_AS, MEMORY_BYTES),
              (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_CORE, 0)]
    if isolated:
        # RLIMIT_NPROC counts per user, so it is only meaningful when the job
        # owns a private UID; otherwise it would count the supervisor too.
        limits.append((resource.RLIMIT_NPROC, 16))
    for kind, value in limits:
        resource.setrlimit(kind, (value, value))


def _read_job(limit: int = MAX_INPUT_BYTES) -> Any:
    chunks, size = [], 0
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ValueError("The job exceeds the input limit.")
        chunks.append(chunk)
    os.close(0)
    return json.loads(b"".join(chunks))


def main() -> None:
    apply_limits(int(sys.argv[1]), sys.argv[2] == "1")
    # CPython may add LC_CTYPE when it starts; no secret-bearing inherited env
    # survives. The executed skill sees exactly the explicitly supplied PATH.
    path = os.environ.get("PATH", os.defpath)
    os.environ.clear()
    os.environ["PATH"] = path
    # Keep a private copy of the result pipe, then point stdout and stderr at
    # /dev/null so nothing the skill prints can corrupt the result envelope.
    result_fd = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        answer = run_job(_read_job())
    except (ValueError, KeyError, TypeError, RecursionError, MemoryError):
        answer = {"status": "error", "result": None, "tests": {"total": 0, "failed": 0, "failures": []},
                  "error": "Invalid job envelope.", "duration_ms": 0}
    payload = json.dumps(answer, ensure_ascii=False, allow_nan=False).encode("utf-8")
    view = memoryview(payload)
    while view:
        view = view[os.write(result_fd, view):]
    os.close(result_fd)


if __name__ == "__main__":
    main()
