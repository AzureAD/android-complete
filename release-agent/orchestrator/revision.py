"""Workflow identity and deliberate, ownership-safe revision adoption.

Only the compact phase manifest is durable. Source and contract inputs are read
afresh; hashing never invokes a handler or a provider.
"""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from functools import wraps
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import re
from typing import get_args, get_origin


ROOT = Path(__file__).resolve().parent.parent
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[\w-]+\Z")
_REVISION_CHECK_SCOPES = ContextVar("revision_check_scopes", default=())
_REVISION_PROVIDER = ContextVar("revision_provider", default=None)


def is_hash(value):
    return isinstance(value, str) and bool(_HASH.fullmatch(value))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def validate_binding(binding):
    if binding is None:
        return
    if (not isinstance(binding, dict)
            or set(binding) != {"runtime_hash", "phases", "last_adoption"}
            or not is_hash(binding["runtime_hash"])
            or not isinstance(binding["phases"], list) or not binding["phases"]):
        raise ValueError("Invalid workflow revision binding")
    seen = set()
    for phase in binding["phases"]:
        if (not isinstance(phase, dict)
                or set(phase) != {"id", "definition_hash", "step_keys"}
                or not isinstance(phase["id"], str) or not _ID.fullmatch(phase["id"])
                or phase["id"] in seen or not is_hash(phase["definition_hash"])
                or not isinstance(phase["step_keys"], list) or not phase["step_keys"]):
            raise ValueError("Invalid workflow revision phase manifest")
        seen.add(phase["id"])
        keys = phase["step_keys"]
        if (any(not isinstance(key, str) or not key.startswith(phase["id"] + ".")
                or not _ID.fullmatch(key[len(phase["id"]) + 1:]) for key in keys)
                or len(keys) != len(set(keys))):
            raise ValueError("Invalid workflow revision step keys")
    adoption = binding["last_adoption"]
    if adoption is not None and (
            not isinstance(adoption, dict)
            or set(adoption) != {"from_revision", "at", "by", "reason"}
            or not is_hash(adoption["from_revision"])
            or any(not _text(adoption[key]) for key in ("at", "by", "reason"))):
        raise ValueError("Invalid workflow revision adoption receipt")


def revision_id(binding):
    """Derive the identity; last_adoption is audit metadata, not identity."""
    validate_binding(binding)
    if binding is None:
        return None
    return digest({key: binding[key] for key in ("runtime_hash", "phases")})


def _runtime_identity(root=None, *, config_path=None, readiness_path=None):
    """Hash production sources/assets and supported dependency identities.

    No mtime cache: replacement with unchanged size/mtime must still invalidate.
    Local overrides, secrets, docs, simulations and run artifacts are not inputs.
    """
    root = Path(root) if root is not None else ROOT
    excluded = {"__pycache__", "tests", "docs", "scenarios", ".pytest_cache"}
    paths = {}
    for directory in ("orchestrator", "steps", "tools", "phases", "templates", "config"):
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if (not path.is_file() or any(part in excluded for part in relative.parts)
                    or any(part.startswith(".") for part in relative.parts)
                    or (path.suffix != ".py" and any(
                        token in part.lower() for part in relative.parts
                        for token in ("secret", "credential")))
                    or path.name.startswith("mocks.local") or path.suffix in (".pyc", ".pyo")
                    or relative == Path("config") / "phases.yaml"
                    or (config_path and path.suffix != ".py"
                        and path.resolve() == Path(config_path).resolve())):
                continue
            # Runtime Python plus explicitly shipped execution assets only.
            if directory in ("orchestrator", "steps", "tools", "phases") and path.suffix != ".py":
                continue
            paths[str(relative).replace("\\", "/")] = path
    for pattern in ("requirements*.txt", "pyproject.toml", "poetry.lock", "uv.lock"):
        for path in sorted(root.glob(pattern)):
            paths[path.name] = path
    source_names = {key for key, path in paths.items()
                    if path.suffix == ".py" or path.parent == root}
    if config_path:
        custom = Path(config_path).resolve().parent
        if custom != (root / "config").resolve():
            for path in sorted(custom.glob("*.yaml")):
                if (path.resolve() == Path(config_path).resolve()
                        or any(token in path.name.lower() for token in ("secret", "credential"))
                        or path.name.startswith((".", "mocks.local"))):
                    continue
                paths["execution-config/" + path.name] = path
    if readiness_path and Path(readiness_path).is_file():
        paths["readiness-config"] = Path(readiness_path)
    with ThreadPoolExecutor(max_workers=8) as readers:
        hashes = readers.map(lambda path: hashlib.sha256(path.read_bytes()).hexdigest(), paths.values())
        inputs = dict(zip(paths, hashes))
    dependencies = {}
    for package in ("PyYAML", "tzdata"):
        try:
            dependencies[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            dependencies[package] = None
    environment = {
        "dependencies": dependencies,
        "python": platform.python_version(), "implementation": platform.python_implementation(),
    }
    return (
        digest({"files": inputs, **environment}),
        digest({"files": {key: inputs[key] for key in source_names}, **environment}),
    )


def runtime_hash(root=None, *, config_path=None, readiness_path=None):
    return _runtime_identity(
        root, config_path=config_path, readiness_path=readiness_path)[0]


class FilesystemRevisionProvider:
    """Read every runtime identity input on each operation boundary."""

    def identity(self, *, config_path=None, readiness_path=None):
        return _runtime_identity(
            config_path=config_path, readiness_path=readiness_path)


@dataclass(frozen=True)
class StaticRevisionProvider:
    """Explicit immutable identity for tests that do not exercise runtime drift."""

    runtime_hash: str
    imported_source_hash: str

    @classmethod
    def capture(cls):
        return cls(*_runtime_identity())

    def identity(self, *, config_path=None, readiness_path=None):
        return self.runtime_hash, self.imported_source_hash


_FILESYSTEM_REVISION_PROVIDER = FilesystemRevisionProvider()


def active_revision_provider():
    return _REVISION_PROVIDER.get() or _FILESYSTEM_REVISION_PROVIDER


@contextmanager
def use_revision_provider(provider):
    if not callable(getattr(provider, "identity", None)):
        raise TypeError("Revision provider must define identity()")
    token = _REVISION_PROVIDER.set(provider)
    try:
        yield
    finally:
        _REVISION_PROVIDER.reset(token)


# This pins imported code, not filesystem reads: every comparison hashes afresh.
_IMPORTED_SOURCE_HASH = _runtime_identity()[1]


def _semantic(value):
    if isinstance(value, Enum):
        return value.value
    if value is MISSING:
        return {"required": True}
    if is_dataclass(value) and not isinstance(value, type):
        return {"type": type(value).__qualname__,
                "fields": {item.name: _semantic(getattr(value, item.name)) for item in fields(value)}}
    if isinstance(value, Mapping):
        return {str(_semantic(key)): _semantic(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_semantic(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_semantic(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if get_origin(value):
        return {"origin": str(get_origin(value)), "args": [_semantic(item) for item in get_args(value)]}
    if isinstance(value, type):
        return value.__module__ + "." + value.__qualname__
    if value is Ellipsis:
        return "..."
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"Unsupported workflow identity input: {type(value).__name__}")


def phase_manifest(workflow, handlers):
    phases = []
    for phase in workflow.phases:
        steps = []
        for step in phase.steps:
            handler = handlers.get(phase.id, step.id)
            definition = {item.name: _semantic(getattr(step, item.name))
                          for item in fields(step) if item.name not in ("name", "raw")}
            definition["parameters"] = {
                role.value: [_semantic(item) for item in schema.fields]
                for role, schema in handler.parameters.items()
            }
            definition.update(
                evidence=_semantic(handler.evidence), writes=_semantic(handler.writes),
                notification=handler.notification, status_email=handler.status_email,
                fire_at_local=handler.fire_at_local,
            )
            steps.append(definition)
        phases.append({
            "id": phase.id,
            "definition_hash": digest({
                "version": workflow.version, "id": phase.id, "execution": phase.execution,
                "anchor": phase.anchor, "conditional": phase.conditional, "steps": steps,
            }),
            "step_keys": [step.key for step in phase.steps],
        })
    return phases


def _current_revision(orch):
    workflow = orch._workflow_definition()
    provider = getattr(orch, "_revision_provider", active_revision_provider())
    runtime, sources = provider.identity(
        config_path=orch.config_path, readiness_path=orch.readiness_path)
    return {
        "runtime_hash": runtime,
        "phases": phase_manifest(workflow, orch.handlers),
        "last_adoption": None,
    }, sources == _IMPORTED_SOURCE_HASH


def current_revision(orch):
    return _current_revision(orch)[0]


def revision_checked(operation):
    """Reuse one fresh revision check within a single engine operation."""
    @wraps(operation)
    def wrapped(orch, *args, **kwargs):
        scopes = _REVISION_CHECK_SCOPES.get()
        if any(scoped_orch is orch for scoped_orch, _ in scopes):
            return operation(orch, *args, **kwargs)
        token = _REVISION_CHECK_SCOPES.set((*scopes, (orch, {})))
        try:
            return operation(orch, *args, **kwargs)
        finally:
            _REVISION_CHECK_SCOPES.reset(token)
    return wrapped


def operation_cache(orch):
    for scoped_orch, cache in reversed(_REVISION_CHECK_SCOPES.get()):
        if scoped_orch is orch:
            return cache
    return None


def bind_initial(orch):
    """Explicit fresh-state binding; never call this while loading existing state."""
    if orch.state.workflow_revision is not None or getattr(orch.state, "_loaded_from_disk", False):
        raise ValueError("Initial binding requires a new, never-loaded release state")
    orch.state.workflow_revision = current_revision(orch)
    return orch.state.workflow_revision


def mismatch_reason(orch):
    cache = operation_cache(orch)
    if cache is not None and "mismatch_reason" in cache:
        return cache["mismatch_reason"]
    if orch.state.workflow_revision is None:
        reason = "Workflow revision is unbound; only diagnostics are permitted."
    else:
        current, imported_sources_match = _current_revision(orch)
        if revision_id(orch.state.workflow_revision) != revision_id(current):
            reason = ("Workflow revision mismatch; preview workflow-adopt. Existing owned work "
                      "requires restoring its pinned runtime before recovery.")
        elif not imported_sources_match or current["runtime_hash"] != orch._loaded_runtime_hash:
            reason = "Runtime files changed during this process; restart on the pinned runtime before executing."
        else:
            reason = ""
    if cache is not None:
        cache["mismatch_reason"] = reason
    return reason


def assert_current(orch):
    reason = mismatch_reason(orch)
    if reason:
        raise ValueError(reason)
    return revision_id(orch.state.workflow_revision)


def _resource_blockers(value, path="resources"):
    blockers = []
    if isinstance(value, dict):
        if value.get("status") in (
                "creating", "created", "uncertain", "deleting", "delete_uncertain"):
            blockers.append(f"{path}: unresolved resource {value['status']}")
        for key, item in sorted(value.items()):
            if key not in ("attempt_history", "attempts", "history", "superseded") and isinstance(item, (dict, list)):
                blockers.extend(_resource_blockers(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            blockers.extend(_resource_blockers(item, f"{path}[{index}]"))
    return blockers


def adoption_preview(orch, *, registry_entries=()):
    from . import delivery
    from .delivery_retention import is_progress_receipt

    old = orch.state.workflow_revision
    new, imported_sources_match = _current_revision(orch)
    if old is None:
        raise ValueError("Unbound release state cannot adopt; explicit fresh initialization is required")
    validate_binding(old)
    runtime_changed = old["runtime_hash"] != new["runtime_hash"]
    first = 0 if runtime_changed else None
    if first is None:
        for index in range(max(len(old["phases"]), len(new["phases"]))):
            before = old["phases"][index] if index < len(old["phases"]) else None
            after = new["phases"][index] if index < len(new["phases"]) else None
            if before != after:
                first = index
                break
    affected = set()
    if first is not None:
        for phase in old["phases"][first:] + new["phases"][first:]:
            affected.update(phase["step_keys"])
        if runtime_changed:
            affected.update(orch.state.steps)
    blockers = [f"steps.{key}: owned execution" for key, record in sorted(orch.state.steps.items())
                if record.get("execution") is not None]
    blockers.extend(_resource_blockers(orch.state.resources))
    if not imported_sources_match or new["runtime_hash"] != orch._loaded_runtime_hash:
        blockers.append("runtime: restart on the reviewed runtime before adopting changed source files")
    fenced = []
    for key, record in sorted(orch.state.notification_deliveries.items()):
        if is_progress_receipt(record):
            delivery.validate_record(orch, record)
            continue
        delivery.validate_record_state(orch.state, record)
        if record["status"] in ("claimed", "uncertain"):
            blockers.append(f"notification_deliveries.{key}: {record['status']}")
        elif record["status"] == "sent" and (
                not isinstance(record.get("completion"), dict)
                or record["completion"].get("status") not in ("applied", "suppressed")):
            blockers.append(f"notification_deliveries.{key}: unfinished completion")
        elif first is not None and record["status"] == "prepared":
            fenced.append(key)
        elif first is not None and record["status"] == "not_sent":
            # Historical attempts stay intact. Their existing revision scope fence
            # must make any retry invalid under the adopted revision.
            matches = record["descriptor"]["scope"].get("state_matches", [])
            if not any(item.get("path") == ["workflow_revision"] for item in matches):
                blockers.append(f"notification_deliveries.{key}: retry lacks revision fence")
    for entry in registry_entries:
        if entry.get("scope") == "shared" or entry.get("release") == orch.state.release_id:
            latest = (entry.get("attempts") or [{}])[-1]
            if (entry.get("status") in ("creating", "uncertain", "deleting", "delete_uncertain", "blocked")
                    or latest.get("status") in ("claimed", "uncertain")):
                blockers.append(f"automations.{entry.get('key')}: unresolved {entry['status']}")
    new_keys = {key for phase in new["phases"] for key in phase["step_keys"]}
    invalidation = {
        "step_keys": sorted(affected),
        "new_step_keys": sorted(new_keys - {key for phase in old["phases"] for key in phase["step_keys"]}),
        "removed_step_keys": sorted({key for phase in old["phases"] for key in phase["step_keys"]} - new_keys),
        "completed_step_keys": sorted(
            key for key in affected
            if orch.state.get_step(*key.split(".", 1)).status in ("done", "skipped")),
        "blocked_step_keys": sorted(
            key for key in affected
            if orch.state.get_step(*key.split(".", 1)).status == "blocked"),
        "gate_decisions": [index for index, decision in enumerate(orch.state.gate_decisions)
                           if decision["step"] in affected],
        "gate_decision_records": [
            {
                "index": index,
                "step": decision["step"],
                "decision": decision["decision"],
                "at": decision["at"],
                "by": decision.get("by"),
            }
            for index, decision in enumerate(orch.state.gate_decisions)
            if decision["step"] in affected
        ],
        "notification_offers": fenced,
    }
    invalidation["summary"] = {
        "affected": len(invalidation["step_keys"]),
        "completed_reset": len(invalidation["completed_step_keys"]),
        "blocked_reset": len(invalidation["blocked_step_keys"]),
        "gate_decisions_removed": len(invalidation["gate_decision_records"]),
        "notification_offers_removed": len(invalidation["notification_offers"]),
    }
    plan = {
        "old_revision": revision_id(old), "new_revision": revision_id(new),
        "runtime_changed": runtime_changed, "invalidation": invalidation,
        "blockers": sorted(blockers),
    }
    return {**plan, "hash": digest(plan), "permission_to_adopt": False}


def adopt(orch, reviewed_hash, *, by, reason, registry_entries=(), persist=None):
    """Caller holds release then registry lock; persist is one atomic state save."""
    from .state import _now

    if not _text(by) or not _text(reason):
        raise ValueError("Workflow adoption requires non-empty by and reason")
    plan = adoption_preview(orch, registry_entries=registry_entries)
    if reviewed_hash != plan["hash"]:
        raise ValueError("Stale workflow adoption review; preview again")
    if plan["blockers"]:
        raise ValueError("Workflow adoption blocked: " + "; ".join(plan["blockers"]))
    if plan["old_revision"] == plan["new_revision"]:
        raise ValueError("Workflow revision is already current")
    new, imported_sources_match = _current_revision(orch)
    if revision_id(new) != plan["new_revision"]:
        raise ValueError("Workflow changed during adoption; preview again")
    if not imported_sources_match or new["runtime_hash"] != orch._loaded_runtime_hash:
        raise ValueError("Restart on the reviewed runtime before adopting changed source files")
    before = deepcopy(vars(orch.state))
    stamp = _now()
    try:
        for key in plan["invalidation"]["step_keys"]:
            phase, step = key.split(".", 1)
            record = orch.state.get_step(phase, step)
            record.status = "pending"
            record.completed_at = None
            record.invalidated_at = stamp
            record.invalidation_reason = f"Workflow adoption: {reason.strip()}"
            orch.state.set_step(phase, step, record)
        affected = set(plan["invalidation"]["step_keys"])
        orch.state.gate_decisions = [decision for decision in orch.state.gate_decisions
                                    if decision["step"] not in affected]
        for key in plan["invalidation"]["notification_offers"]:
            del orch.state.notification_deliveries[key]
        new["last_adoption"] = {
            "from_revision": plan["old_revision"], "at": stamp,
            "by": by.strip(), "reason": reason.strip(),
        }
        orch.state.workflow_revision = new
        (persist or orch.state.checkpoint)()
    except BaseException:
        vars(orch.state).clear()
        vars(orch.state).update(before)
        raise
    return {**plan, "adopted": True}
