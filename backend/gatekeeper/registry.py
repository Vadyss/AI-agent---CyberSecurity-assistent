# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""The sole writer of skill artifacts, candidates and approved rule files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import threading
import uuid
from .audit import AuditLog, now_iso
from .names import require_name, require_run_id, safe_join
from .recipe import canonical_json
from .types import SkillInfo


LOGGER = logging.getLogger(__name__)


class IntegrityError(ValueError):
    """A persisted or approved artifact no longer matches its verified digest."""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def recipe_sha256(recipe: dict) -> str:
    return sha256(canonical_json(recipe).encode("utf-8"))


def atomic_write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise IntegrityError("The target file must not be a symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".tmp-{uuid.uuid4().hex}"
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _sync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict) -> None:
    atomic_write(path, (canonical_json(value) + "\n").encode("utf-8"))


class Registry:
    def __init__(self, data_dir: Path, audit: AuditLog):
        self.root = Path(data_dir).resolve()
        self.audit = audit
        self.lock = threading.RLock()
        self.registry_dir = safe_join(self.root, "registry")
        self.candidates_dir = safe_join(self.root, "candidates")
        self.rules_dir = safe_join(self.root, "rules")
        self.quarantine_dir = safe_join(self.root, "quarantine")
        for folder in (self.registry_dir, self.candidates_dir, self.rules_dir, self.quarantine_dir, self.rules_dir / "history"):
            folder.mkdir(parents=True, exist_ok=True)
        self.index_path = safe_join(self.registry_dir, "registry.json")
        if self.index_path.exists():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(self.index, dict) or self.index.get("format") != 1 or not isinstance(self.index.get("skills"), dict):
                raise IntegrityError("Invalid registry index.")
            for name, entry in self.index["skills"].items():
                require_name(name)
                if not isinstance(entry, dict) or entry.get("origin") not in {"seed", "agent"}:
                    raise IntegrityError("Invalid registry entry.")
        else:
            self.index = {"format": 1, "skills": {}}
            atomic_json(self.index_path, self.index)

    def _candidate_path(self, run_id: str, name: str | None = None) -> Path:
        run_dir = safe_join(self.candidates_dir, require_run_id(run_id))
        return safe_join(run_dir, require_name(name)) if name is not None else run_dir

    def _folder(self, name: str) -> Path:
        return safe_join(self.registry_dir, require_name(name))

    def _integrity(self, run_id: str | None, name: str, reason: str) -> None:
        self.audit.append("integrity_violation", run_id, {"skill": name, "reason": reason})

    def _read_verified(self, folder: Path, expected: dict, run_id: str | None, name: str) -> tuple[str, dict, dict]:
        try:
            payloads = {}
            for filename, field in (("skill.py", "sha256"), ("manifest.json", "manifest_sha256"), ("test_skill.py", "tests_sha256")):
                path = safe_join(folder, filename)
                if (folder / filename).is_symlink():
                    raise IntegrityError("An artifact must not be a symbolic link.")
                payloads[filename] = path.read_bytes()
                if expected.get(field) != sha256(payloads[filename]):
                    raise IntegrityError(f"Digest mismatch for file {filename}.")
            manifest = json.loads(payloads["manifest.json"])
            if manifest.get("name") != name or manifest.get("version") != expected.get("version") or manifest.get("kind") != expected.get("kind"):
                raise IntegrityError("The manifest does not match the index.")
            return payloads["skill.py"].decode("utf-8"), manifest, deepcopy(expected)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._integrity(run_id, name, "The skill has invalid artifacts or digests.")
            raise IntegrityError("Skill integrity has been compromised.") from error

    def _entry(self, manifest: dict, origin: str, run_id: str | None, payloads: dict[str, bytes], created_at: str | None = None) -> dict:
        return {"version": manifest["version"], "kind": manifest["kind"], "origin": origin,
                "sha256": sha256(payloads["skill.py"]), "manifest_sha256": sha256(payloads["manifest.json"]),
                "tests_sha256": sha256(payloads["test_skill.py"]), "created_by_run": run_id,
                "created_at": created_at or now_iso(), "times_used": 0, "last_used_at": None}

    def _info(self, manifest: dict, meta: dict, status: str) -> SkillInfo:
        return SkillInfo(name=manifest["name"], version=manifest["version"], kind=manifest["kind"],
                         description=manifest["description"], origin=meta["origin"], status=status,
                         created_by_run=meta["created_by_run"], created_at=meta["created_at"])

    def initialize(self, seed_dir: Path) -> None:
        with self.lock:
            self.verify_integrity()
            for path in sorted(Path(seed_dir).iterdir()):
                if not path.is_dir() or path.is_symlink():
                    continue
                name = require_name(path.name)
                manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
                if manifest.get("name") != name:
                    raise IntegrityError("The seed has an invalid manifest.")
                old = self.index["skills"].get(name)
                code = (path / "skill.py").read_bytes()
                if old is not None and old["origin"] != "seed":
                    raise IntegrityError("The seed conflicts with an agent skill.")
                if old is not None and old["sha256"] == sha256(code):
                    continue
                manifest["version"] = old["version"] + 1 if old else 1
                payloads = {"manifest.json": (canonical_json(manifest) + "\n").encode("utf-8"),
                            "skill.py": code, "test_skill.py": (path / "test_skill.py").read_bytes()}
                folder = self._folder(name)
                stage = self.registry_dir / f".tmp-{name}-{uuid.uuid4().hex}"
                stage.mkdir()
                backup = None
                old_index = deepcopy(self.index)
                published = False
                try:
                    for filename, payload in payloads.items():
                        atomic_write(stage / filename, payload)
                    if folder.exists():
                        backup = self.registry_dir / f".old-{name}-{uuid.uuid4().hex}"
                        os.replace(folder, backup)
                    os.replace(stage, folder)
                    published = True
                    entry = self._entry(manifest, "seed", None, payloads)
                    if old:
                        entry["times_used"] = old["times_used"]
                        entry["last_used_at"] = old["last_used_at"]
                    self.index["skills"][name] = entry
                    atomic_json(self.index_path, self.index)
                    self.audit.append("seed_updated" if old else "seed_installed", detail={"skill": name, "sha256": sha256(code), "version": manifest["version"]})
                except Exception:
                    if published and folder.exists():
                        shutil.rmtree(folder)
                    if backup is not None and backup.exists():
                        os.replace(backup, folder)
                    self.index = old_index
                    atomic_json(self.index_path, self.index)
                    raise
                finally:
                    if stage.exists(): shutil.rmtree(stage)
                    if backup is not None and backup.exists(): shutil.rmtree(backup)
            for leftover in self.candidates_dir.iterdir():
                if leftover.is_symlink() or leftover.is_file():
                    leftover.unlink()
                elif leftover.is_dir():
                    shutil.rmtree(leftover)
            for leftover in self.registry_dir.glob(".tmp-*"):
                if leftover.is_dir(): shutil.rmtree(leftover)

    def verify_integrity(self) -> list[str]:
        removed = []
        with self.lock:
            for name, meta in list(self.index["skills"].items()):
                try:
                    self._read_verified(self._folder(name), meta, None, name)
                except (IntegrityError, ValueError):
                    self._integrity(None, name, "The skill was quarantined at startup.")
                    path = self.registry_dir / name
                    if path.exists() or path.is_symlink():
                        quarantine = self.quarantine_dir / f"{name}__{uuid.uuid4().hex}"
                        os.replace(path, quarantine)
                    self.index["skills"].pop(name)
                    removed.append(name)
            if removed:
                atomic_json(self.index_path, self.index)
        return removed

    def save_candidate(self, run_id: str, manifest: dict, code: str, tests: str, attempt: int = 1) -> SkillInfo:
        manifest = deepcopy(manifest)
        if manifest.get("name") == "data":
            raise ValueError("The name data is reserved for private run datasets.")
        with self.lock:
            folder = self._candidate_path(run_id, manifest["name"])
            if manifest["name"] in self.index["skills"]:
                raise IntegrityError("The skill is already installed.")
            payloads = {"manifest.json": (canonical_json(manifest) + "\n").encode("utf-8"),
                        "skill.py": code.encode("utf-8"), "test_skill.py": tests.encode("utf-8")}
            meta = self._entry(manifest, "agent", run_id, payloads)
            meta["attempt"] = attempt
            stage = folder.parent / f".tmp-{manifest['name']}-{uuid.uuid4().hex}"
            stage.mkdir(parents=True)
            try:
                for filename, payload in payloads.items():
                    atomic_write(stage / filename, payload)
                atomic_json(stage / "meta.json", meta)
                if folder.exists(): shutil.rmtree(folder)
                os.replace(stage, folder)
                _sync_dir(folder.parent)
            finally:
                if stage.exists(): shutil.rmtree(stage)
            self.audit.append("candidate_created", run_id, {"skill": manifest["name"], "sha256": meta["sha256"], "attempt": attempt})
            return self._info(manifest, meta, "candidate")

    def save_examiner_data(self, run_id: str, datasets: dict) -> dict[str, str]:
        """Persist private verified run data through the registry writer only."""
        require_run_id(run_id)
        if set(datasets) != {"tuning", "validation"}:
            raise ValueError("Invalid examiner datasets.")
        datasets = deepcopy(datasets)
        with self.lock:
            target = self._candidate_path(run_id, "data")
            payloads = {}
            for name, dataset in datasets.items():
                if dataset.labels.get("dataset") != name or dataset.labels.get("log_source") != "ssh" or "seed" in dataset.labels:
                    raise ValueError("Invalid private examiner metadata.")
                payloads[f"{name}/auth.log"] = ("\n".join(dataset.lines) + "\n").encode("utf-8")
                payloads[f"{name}/labels.json"] = (canonical_json(dataset.labels) + "\n").encode("utf-8")
            stage = target.parent / f".tmp-data-{uuid.uuid4().hex}"
            stage.mkdir(parents=True)
            try:
                for relative, payload in payloads.items():
                    atomic_write(safe_join(stage, relative), payload)
                if target.exists():
                    raise IntegrityError("Private data for this run already exists.")
                os.replace(stage, target)
                _sync_dir(target.parent)
            finally:
                if stage.exists(): shutil.rmtree(stage)
            return {f"data/{relative}": sha256(payload) for relative, payload in payloads.items()}

    def read_skill(self, run_id: str | None, name: str) -> tuple[str, dict, dict]:
        require_name(name)
        with self.lock:
            if name in self.index["skills"]:
                return self._read_verified(self._folder(name), self.index["skills"][name], run_id, name)
            if run_id is None:
                raise KeyError(name)
            folder = self._candidate_path(run_id, name)
            try:
                meta = json.loads(safe_join(folder, "meta.json").read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise IntegrityError("Candidate metadata is missing.") from error
            if meta.get("created_by_run") != run_id or meta.get("origin") != "agent":
                raise IntegrityError("The candidate belongs to another run.")
            return self._read_verified(folder, meta, run_id, name)

    def review(self, run_id: str | None, name: str) -> dict:
        """Code, tests and manifest of an installed skill or a run's candidate, integrity-checked."""
        code, manifest, meta = self.read_skill(run_id, name)
        with self.lock:
            folder = self._folder(name) if name in self.index["skills"] else self._candidate_path(run_id, name)
            tests = safe_join(folder, "test_skill.py").read_bytes()
        if sha256(tests) != meta["tests_sha256"]:
            self._integrity(run_id, name, "The tests changed after they were verified.")
            raise IntegrityError("Skill integrity has been compromised.")
        return {"name": name, "version": meta["version"], "kind": meta["kind"], "origin": meta["origin"],
                "manifest": manifest, "code": code, "tests": tests.decode("utf-8"),
                "code_sha256": meta["sha256"], "tests_sha256": meta["tests_sha256"],
                "created_by_run": meta["created_by_run"], "created_at": meta["created_at"]}

    def describe_digest(self, code_sha256: str) -> dict | None:
        """Name and version of the installed skill or candidate with this code digest."""
        with self.lock:
            for name, meta in sorted(self.index["skills"].items()):
                if meta.get("sha256") == code_sha256:
                    return {"name": name, "version": meta["version"], "origin": meta["origin"], "status": "installed"}
            for meta_path in sorted(self.candidates_dir.glob("run_*/*/meta.json")):
                if meta_path.is_symlink():
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(meta, dict) and meta.get("sha256") == code_sha256:
                    return {"name": meta_path.parent.name, "version": meta.get("version"),
                            "origin": "agent", "status": "candidate"}
        return None

    def manifests(self, run_id: str | None = None) -> list[dict]:
        with self.lock:
            names = sorted(self.index["skills"])
            output = [self.read_skill(None, name)[1] for name in names]
            if run_id is not None:
                output += [self.read_skill(run_id, info.name)[1] for info in self.candidate_infos(run_id)]
            return output

    def skill_info(self, name: str) -> SkillInfo:
        _, manifest, meta = self.read_skill(None, name)
        return self._info(manifest, meta, "installed")

    def installed_skills(self) -> list[SkillInfo]:
        with self.lock:
            return [self.skill_info(name) for name in sorted(self.index["skills"])]

    def candidate_infos(self, run_id: str) -> list[SkillInfo]:
        with self.lock:
            root = self._candidate_path(run_id)
            if not root.exists(): return []
            infos = []
            for path in sorted(root.iterdir()):
                if path.is_dir() and not path.name.startswith(".") and path.name != "data":
                    _, manifest, meta = self.read_skill(run_id, require_name(path.name))
                    infos.append(self._info(manifest, meta, "candidate"))
            return infos

    def note_reuse(self, run_id: str, names: list[str]) -> None:
        require_run_id(run_id)
        with self.lock:
            for name in set(names):
                self.read_skill(None, name)
            for name in set(names):
                self.index["skills"][name]["times_used"] += 1
                self.index["skills"][name]["last_used_at"] = now_iso()
            atomic_json(self.index_path, self.index)

    def promote(self, run_id: str, recipe: dict, new_skills: list[SkillInfo], validated: dict, comment: str | None = None) -> list[SkillInfo]:
        recipe, new_skills, validated = deepcopy((recipe, new_skills, validated))
        require_run_id(run_id)
        require_name(recipe.get("name"))
        with self.lock:
            record = validated.get(run_id) if run_id in validated else validated
            def metric(value):
                return value.model_dump(mode="json") if hasattr(value, "model_dump") else value
            tuning, validation = metric(record.get("metrics_tuning")), metric(record.get("metrics_validation"))
            if record.get("recipe_sha256") != recipe_sha256(recipe) or record.get("attack_type") != recipe.get("attack_type") or not isinstance(validation, dict) or validation.get("passed") is not True or not isinstance(tuning, dict) or tuning.get("passed") is not True:
                self._integrity(run_id, recipe["name"], "Promotion does not match the successfully validated recipe.")
                raise IntegrityError("The recipe was not successfully validated in this run.")
            candidates = self.candidate_infos(run_id)
            given = [s if isinstance(s, SkillInfo) else SkillInfo.model_validate(s) for s in new_skills]
            if len({s.name for s in given}) != len(given) or {s.name: s.model_dump() for s in given} != {s.name: s.model_dump() for s in candidates}:
                self._integrity(run_id, recipe["name"], "The skill list does not match the run candidates.")
                raise IntegrityError("Promotion must contain exactly the candidates from this run.")
            artifacts = {info.name: self.read_skill(run_id, info.name) for info in candidates}
            snapshots = {}
            for name, (_, _, meta) in artifacts.items():
                folder = self._candidate_path(run_id, name)
                payloads = {filename: safe_join(folder, filename).read_bytes() for filename in ("manifest.json", "skill.py", "test_skill.py")}
                for filename, digest in (("manifest.json", "manifest_sha256"), ("skill.py", "sha256"), ("test_skill.py", "tests_sha256")):
                    if sha256(payloads[filename]) != meta[digest]:
                        self._integrity(run_id, name, "The candidate changed during promotion.")
                        raise IntegrityError("The candidate changed during promotion.")
                snapshots[name] = payloads
            references = [recipe["parser"], recipe["aggregation"]["skill"]] + [s["skill"] for s in recipe.get("enrich", [])]
            used = {name: self.read_skill(run_id, name)[2] for name in references}
            expected_skills = record.get("skill_digests")
            if expected_skills is not None and expected_skills != {name: used[name]["sha256"] for name in used}:
                self._integrity(run_id, recipe["name"], "Skills have changed since validation.")
                raise IntegrityError("Skills have changed since validation.")
            if any(name in self.index["skills"] for name in artifacts):
                raise IntegrityError("The candidate conflicts with the registry.")
            staged = []
            old_index = deepcopy(self.index)
            rule_path = safe_join(self.rules_dir, recipe["name"] + ".json")
            history_path = safe_join(self.rules_dir / "history", recipe["name"] + "__" + run_id + ".json")
            old_rule = rule_path.read_bytes() if rule_path.exists() else None
            if history_path.exists():
                raise IntegrityError("The run already has an approved rule.")
            installed = []
            try:
                for name, (_, _, meta) in artifacts.items():
                    folder = self._candidate_path(run_id, name)
                    stage = self.registry_dir / f".tmp-{name}-{uuid.uuid4().hex}"
                    stage.mkdir()
                    staged.append(stage)
                    for filename in ("manifest.json", "skill.py", "test_skill.py"):
                        atomic_write(stage / filename, snapshots[name][filename])
                    os.replace(stage, self._folder(name))
                    installed.append(name)
                    new_meta = {key: value for key, value in meta.items() if key != "attempt"}
                    self.index["skills"][name] = new_meta
                saved = {"recipe": deepcopy(recipe), "attack_type": recipe["attack_type"], "metrics_tuning": tuning,
                         "metrics_validation": validation, "skills": [{"name": name, "version": meta["version"], "sha256": meta["sha256"]} for name, meta in used.items()],
                         "approved_at": now_iso(), "run_id": run_id, "comment": comment}
                atomic_json(self.index_path, self.index)
                atomic_json(history_path, saved)
                atomic_json(rule_path, saved)
                result = [self._info(artifacts[name][1], self.index["skills"][name], "installed") for name in installed]
                for name in installed:
                    self.audit.append("skill_promoted", run_id, {"skill": name, "sha256": self.index["skills"][name]["sha256"]})
                self.audit.append("rule_approved", run_id, {"rule_name": recipe["name"], "recipe_sha256": recipe_sha256(recipe)})
            except Exception:
                for name in installed:
                    shutil.rmtree(self._folder(name))
                self.index = old_index
                atomic_json(self.index_path, self.index)
                history_path.unlink(missing_ok=True)
                if old_rule is None: rule_path.unlink(missing_ok=True)
                else: atomic_write(rule_path, old_rule)
                try:
                    self.audit.append("integrity_violation", run_id, {"rule_name": recipe["name"], "reason": "Promotion was rolled back after a write failure."})
                except Exception:
                    LOGGER.warning("The audit could not record the promotion rollback.")
                raise
            finally:
                for stage in staged:
                    try:
                        if stage.exists(): shutil.rmtree(stage)
                    except OSError:
                        LOGGER.warning("The temporary promotion directory could not be cleaned up.")
            try:
                self.discard(run_id)
            except Exception:
                LOGGER.warning("The approved run has leftover candidates; they will be cleaned up at the next startup.")
            return result

    def approved_rule(self, attack_type: str) -> dict | None:
        with self.lock:
            rules = []
            for path in self.rules_dir.glob("*.json"):
                if path.is_symlink(): raise IntegrityError("A rule must not be a symbolic link.")
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("attack_type") == attack_type:
                    rules.append(record)
            if not rules: return None
            return deepcopy(max(rules, key=lambda r: r["approved_at"])["recipe"])

    def discard(self, run_id: str) -> None:
        with self.lock:
            folder = self._candidate_path(run_id)
            if folder.exists(): shutil.rmtree(folder)


def erase_tenant_data(data_dir: Path) -> None:
    """Delete everything a tenant has on disk: tools, candidates, rules, lessons, usage and audit."""
    root = Path(data_dir)
    if root.is_symlink():
        raise IntegrityError("A tenant data directory must not be a symbolic link.")
    if root.exists():
        shutil.rmtree(root)


def remove_run_files(data_dir: Path, run_ids: list[str]) -> int:
    """Delete per-run files (usage records) of runs removed by the retention policy."""
    removed = 0
    runs = Path(data_dir) / "runs"
    for run_id in run_ids:
        folder = safe_join(runs, require_run_id(run_id))
        if folder.is_dir() and not folder.is_symlink():
            shutil.rmtree(folder)
            removed += 1
    return removed


class UsageStore:
    """Atomic metadata snapshots; never modifies skills, recipes or audit records."""
    def __init__(self, data_dir: Path, run_id: str):
        require_run_id(run_id)
        self.run_id = run_id
        self.root = Path(data_dir).resolve()
        self.path = self.root / "runs" / run_id / "usage.json"

    def save(self, payload: dict) -> None:
        if set(payload) != {"run_id", "records", "summary"} or payload["run_id"] != self.run_id:
            raise ValueError("Invalid usage snapshot.")
        for path in (self.root / "runs", self.path.parent, self.path):
            if path.is_symlink():
                raise ValueError("Usage storage must not contain symbolic links.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.parent / (".usage-" + uuid.uuid4().hex + ".tmp")
        try:
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
                json.dump(payload, file, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
