# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Verify independent generator proposals without executing them in the backend."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import secrets
from typing import Callable

from .datasets import Dataset
from .evaluator import parser_output_valid
from .names import require_name, require_run_id
from .policy import Policy
from .recipe import canonical_json
from .static_analysis import analyze_code
from .types import SandboxProtocol


ERROR = "The examiner did not produce verifiable test data."


def _checked_generator(code: object, policy: Policy) -> str:
    if not isinstance(code, str):
        raise ValueError(ERROR)
    try:
        if len(code.encode("utf-8")) > policy.skills.max_code_bytes:
            raise ValueError(ERROR)
        tree = ast.parse(code)
    except (ValueError, SyntaxError, RecursionError):
        raise ValueError(ERROR) from None
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "generate"]
    if len(definitions) != 1:
        raise ValueError(ERROR)
    args = definitions[0].args
    if ([arg.arg for arg in args.posonlyargs + args.args] != ["seed"] or args.defaults
            or args.kwonlyargs or args.vararg or args.kwarg):
        raise ValueError(ERROR)
    wrapped = code + '\n\ndef run(inputs, params):\n    return [generate(params["seed"])]\n'
    if analyze_code(wrapped, {"imports": list(policy.skills.allowed_imports)}, policy):
        raise ValueError(ERROR)
    return wrapped


def _bounded_json(value: object, limit: int) -> None:
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError(ERROR) from None
    if len(encoded) > limit:
        raise ValueError(ERROR)


def _generated_output(answer: dict, policy: Policy) -> tuple[list[str], list[list[int]]]:
    if not isinstance(answer, dict) or answer.get("status") != "ok":
        raise ValueError(ERROR)
    result = answer.get("result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError(ERROR)
    payload = result[0]
    _bounded_json(payload, policy.sandbox.max_input_bytes)
    lines, instances = payload.get("lines"), payload.get("instances")
    if not isinstance(lines, list) or not lines or not all(isinstance(line, str) and line and len(line.encode("utf-8")) <= 8192 and "\n" not in line and "\r" not in line for line in lines):
        raise ValueError(ERROR)
    if not isinstance(instances, list) or len(instances) < policy.datasets.min_instances_per_attack_type:
        raise ValueError(ERROR)
    used = set()
    groups = []
    for instance in instances:
        if not isinstance(instance, dict):
            raise ValueError(ERROR)
        indices = instance.get("lines")
        if (not isinstance(indices, list) or not indices or any(type(i) is not int or not 0 <= i < len(lines) for i in indices)
                or len(indices) != len(set(indices)) or used.intersection(indices)):
            raise ValueError(ERROR)
        used.update(indices)
        groups.append(sorted(indices))
    return lines, groups


def _benign_lines(baseline: Dataset) -> list[str]:
    if baseline.labels.get("log_source") != "ssh":
        raise ValueError(ERROR)
    attacks = set().union(*(set(instance["lines"]) for instance in baseline.labels["instances"]))
    return [line for i, line in enumerate(baseline.lines) if i not in attacks]


def _iso_epoch(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        raise ValueError(ERROR) from None


async def prepare_datasets(run_id: str, slug: str, code: str, baseline_datasets: dict[str, Dataset],
                           sandbox: SandboxProtocol, policy: Policy,
                           skill_loader: Callable[[str], tuple[str, dict, dict]]) -> dict[str, Dataset]:
    """Return only verified data, never private seeds or generator metadata."""
    require_run_id(run_id)
    require_name(slug)
    if set(baseline_datasets) != {"tuning", "validation"}:
        raise ValueError(ERROR)
    baseline_datasets = deepcopy(baseline_datasets)
    wrapped = _checked_generator(code, policy)
    seeds = [secrets.randbits(32), secrets.randbits(32)]
    while seeds[1] == seeds[0]:
        seeds[1] = secrets.randbits(32)
    prepared = {}
    identity_sets = {}
    parser_digest = None
    for name, seed in zip(("tuning", "validation"), seeds):
        answer = await sandbox.run(wrapped, [], {"seed": seed}, allowed_imports=list(policy.skills.allowed_imports), timeout_s=policy.sandbox.run_timeout_s)
        generated_lines, generated_instances = _generated_output(answer, policy)
        benign = _benign_lines(baseline_datasets[name])
        combined = generated_lines + benign
        _bounded_json(combined, policy.sandbox.max_input_bytes)
        parser_code, parser_manifest, parser_meta = skill_loader("ssh_parser")
        if parser_manifest.get("kind") != "parser" or parser_meta.get("origin") != "seed":
            raise ValueError(ERROR)
        digest = parser_meta.get("sha256")
        if not isinstance(digest, str) or parser_digest is not None and digest != parser_digest:
            raise ValueError(ERROR)
        parser_digest = digest
        parsed = await sandbox.run(parser_code, combined, {}, allowed_imports=list(policy.skills.allowed_imports), timeout_s=policy.sandbox.run_timeout_s)
        if not isinstance(parsed, dict) or parsed.get("status") != "ok" or not parser_output_valid(parsed.get("result"), len(combined)):
            raise ValueError(ERROR)
        events = parsed["result"]
        indexed = {event["_line"]: event for event in events}
        if len(indexed) != len(events):
            raise ValueError(ERROR)
        coverage = sum(i in indexed for i in range(len(generated_lines)))
        if coverage < .95 * len(generated_lines):
            raise ValueError(ERROR)
        target_indices = set().union(*(set(indices) for indices in generated_instances))
        if not target_indices <= indexed.keys() or any(len(generated_lines) + i not in indexed for i in range(len(benign))):
            raise ValueError(ERROR)
        identities = {"ips": set(), "users": set()}
        kept_indices = target_indices | {len(generated_lines) + i for i in range(len(benign))}
        for index in kept_indices:
            event = indexed[index]
            if not isinstance(event.get("src_ip"), str) or not isinstance(event.get("user"), str) or not event["user"]:
                raise ValueError(ERROR)
            try:
                identities["ips"].add(str(ipaddress.ip_address(event["src_ip"])))
            except ValueError:
                raise ValueError(ERROR) from None
            identities["users"].add(event["user"])
        identity_sets[name] = identities
        selected = [(indexed[index]["ts"], 1, index, generated_lines[index]) for index in sorted(target_indices)]
        selected += [(indexed[len(generated_lines) + i]["ts"], 0, i, line) for i, line in enumerate(benign)]
        selected.sort(key=lambda item: (item[0], item[1], item[2]))
        remapped = {original: new for new, (_, origin, original, _) in enumerate(selected) if origin == 1}
        instances = []
        for count, indices in enumerate(generated_instances, 1):
            attack_events = [indexed[index] for index in indices]
            instances.append({"id": f"exm_{count:04d}", "attack_type": slug, "lines": sorted(remapped[i] for i in indices),
                              "start": _iso_epoch(min(event["ts"] for event in attack_events)),
                              "end": _iso_epoch(max(event["ts"] for event in attack_events)),
                              "src_ips": sorted({str(ipaddress.ip_address(event["src_ip"])) for event in attack_events}),
                              "users": sorted({event["user"] for event in attack_events}), "notes": ""})
        merged = [line for _, _, _, line in selected]
        labels = {"dataset": name, "log_source": "ssh", "generator_version": 1,
                  "line_count": len(merged), "instances": instances, "counts": {slug: len(instances)}}
        _bounded_json(merged, policy.sandbox.max_input_bytes)
        prepared[name] = Dataset(merged, labels)
    if identity_sets["tuning"]["ips"] & identity_sets["validation"]["ips"] or identity_sets["tuning"]["users"] & identity_sets["validation"]["users"]:
        raise ValueError(ERROR)
    if skill_loader("ssh_parser")[2].get("sha256") != parser_digest:
        raise ValueError(ERROR)
    return prepared
