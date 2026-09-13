"""Versioned, crash-recoverable Scout automation registry."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
import uuid

from orchestrator.locking import file_lock


REGISTRY_SCHEMA_VERSION = 3
KINDS = ("release-level", "step-driving")
STATUSES = frozenset({
    "prepared",
    "creating",
    "uncertain",
    "active",
    "missing",
    "blocked",
    "deleting",
    "delete_uncertain",
})
_LOCK_TIMEOUT = 30.0
_OWNED = frozenset({"creating", "uncertain", "deleting", "delete_uncertain"})
_PROVIDER_REQUIRED = frozenset({
    "name", "description", "prompt", "model", "enabled", "oneShot",
    "triggerType", "conditionCheckInterval", "browserHeadless", "teamsNotify",
})
_PROVIDER_KEYS = _PROVIDER_REQUIRED | {"schedule", "condition", "steps"}


def provider_spec(value: dict) -> dict:
    """Validate complete transient m_create_automation kwargs; never fill defaults."""
    if not isinstance(value, dict):
        raise ValueError("A complete provider spec object is required")
    if set(value) - _PROVIDER_KEYS or _PROVIDER_REQUIRED - set(value):
        raise ValueError("Provider spec has unknown settings or missing explicit defaults")
    for key in ("name", "description", "prompt", "model"):
        if not isinstance(value[key], str):
            raise ValueError(f"Provider spec {key} must be text")
    if not value["name"].strip() or not value["prompt"].strip():
        raise ValueError("Provider name and prompt must be non-empty")
    for key in ("enabled", "oneShot", "browserHeadless"):
        if type(value[key]) is not bool:
            raise ValueError(f"Provider spec {key} must be boolean")
    if value["teamsNotify"] not in ("always", "auto", "never"):
        raise ValueError("Invalid teamsNotify")
    if (type(value["conditionCheckInterval"]) is not int
            or value["conditionCheckInterval"] not in (5, 15, 30, 60)):
        raise ValueError("Invalid conditionCheckInterval")
    if value["triggerType"] == "schedule":
        if "condition" in value or not isinstance(value.get("schedule"), str) or not value["schedule"].strip():
            raise ValueError("Scheduled spec requires schedule and forbids condition")
    elif value["triggerType"] == "condition":
        if "schedule" in value or not isinstance(value.get("condition"), str) or not value["condition"].strip():
            raise ValueError("Condition spec requires condition and forbids schedule")
        if value["oneShot"] is not True:
            raise ValueError("Condition automations must be explicitly oneShot")
    else:
        raise ValueError("Invalid triggerType")
    if "steps" in value:
        if not isinstance(value["steps"], list) or not value["steps"]:
            raise ValueError("Provider steps must be a non-empty list")
        for step in value["steps"]:
            if (not isinstance(step, dict) or set(step) != {"label", "prompt"}
                    or any(not isinstance(v, str) or not v.strip() for v in step.values())):
                raise ValueError("Provider steps require explicit label and prompt only")
    return deepcopy(value)


def _evidence_hash(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Owner/provider evidence is required")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manual(entry: dict) -> bool:
    rules = entry.get("cleanup_when")
    return entry.get("scope") == "shared" or "manual" in (
        rules if isinstance(rules, list) else [rules]
    )


def kind_of(entry: dict) -> str:
    return entry.get("kind")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_key(release: str, slug: str, shared: bool = False) -> str:
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug is required for stable automation identity")
    if shared:
        return f"shared:{slug.strip()}"
    if not isinstance(release, str) or not release.strip():
        raise ValueError("release is required for release-scoped automation identity")
    return f"release:{release.strip()}:{slug.strip()}"


def _intent_hash(entry: dict, spec: dict) -> str:
    fields = {
        key: entry.get(key)
        for key in (
            "key",
            "name",
            "slug",
            "kind",
            "scope",
            "release",
            "purpose",
            "steps",
            "schedule",
            "cleanup_when",
        )
    }
    rules = fields["cleanup_when"]
    fields["cleanup_when"] = sorted(set(rules if isinstance(rules, list) else [rules]))
    encoded = json.dumps(
        {"metadata": fields, "provider": provider_spec(spec)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AutomationRegistry:
    """Atomic local authority for desired and observed Scout automation identity."""

    def __init__(self, runs_root: str, release: str = None):
        self.runs_root = runs_root
        self.release = release
        self.shared_path = os.path.join(runs_root, "_automations.json")
        self.lock_path = os.path.join(runs_root, ".automations.lock")

    def _release_path(self, release: str) -> str:
        return os.path.join(self.runs_root, release, "_automations.json")

    def _release_files(self) -> list[str]:
        return sorted(
            glob.glob(os.path.join(self.runs_root, "*", "_automations.json"))
        )

    def _paths(self) -> list[str]:
        return [self.shared_path, *self._release_files()]

    def _load_file(self, path: str) -> list[dict]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise ValueError(f"Automation registry is unreadable: {path}") from exc
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != REGISTRY_SCHEMA_VERSION
            or not isinstance(document.get("entries"), list)
        ):
            raise ValueError(
                f"Unsupported automation registry schema in {path}; "
                "stop old workers and explicitly retire/review the old registry; "
                "automatic migration or rehash is forbidden."
            )
        entries = document["entries"]
        for entry in entries:
            self._validate_entry(entry)
        return entries

    def _save_file(self, path: str, entries: list[dict]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": REGISTRY_SCHEMA_VERSION,
                    "entries": entries,
                },
                handle,
                indent=2,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    @staticmethod
    def _validate_entry(entry: dict) -> None:
        if not isinstance(entry, dict):
            raise ValueError("Automation registry entry must be an object")
        if set(entry) - {
            "key", "id", "name", "slug", "kind", "scope", "release", "purpose", "steps",
            "schedule", "cleanup_when", "status", "attempts", "prepared_at", "intent_hash",
            "registered_at", "adopted_at", "observed_ids", "missing_at",
        }:
            raise ValueError("Automation registry contains unsupported fields")
        if (
            not isinstance(entry.get("key"), str)
            or not entry["key"]
            or entry.get("status") not in STATUSES
            or not isinstance(entry.get("slug"), str)
            or not entry["slug"]
            or entry.get("kind") not in KINDS
            or entry.get("scope") not in ("release", "shared")
            or not isinstance(entry.get("steps"), list)
            or not entry.get("cleanup_when")
            or not isinstance(entry.get("attempts"), list)
            or not isinstance(entry.get("intent_hash"), str)
            or len(entry["intent_hash"]) != 64
            or not isinstance(entry.get("prepared_at"), str)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("purpose"), str)
        ):
            raise ValueError("Invalid automation registry entry")
        if entry["scope"] == "release" and not entry.get("release"):
            raise ValueError("Release-scoped automation entry requires release")
        if entry["scope"] == "shared" and entry.get("release") is not None:
            raise ValueError("Shared automation entry cannot carry a release")
        if entry["status"] == "active" and not entry.get("id"):
            raise ValueError("Active automation entry requires provider id")
        for attempt in entry["attempts"]:
            if (not isinstance(attempt, dict)
                    or set(attempt) - {"id", "owner", "started_at", "status", "evidence", "acknowledged_at"}
                    or any(not isinstance(attempt.get(key), str) for key in ("id", "owner", "started_at", "status"))
                    or "evidence" in attempt and (
                        not isinstance(attempt["evidence"], str)
                        or not attempt["evidence"].startswith("sha256:")
                        or len(attempt["evidence"]) != 71
                    )):
                raise ValueError("Invalid automation attempt; only digest evidence may be persisted")

    def _path_for(self, entry: dict) -> str:
        if entry["scope"] == "release":
            return self._release_path(entry["release"])
        return self.shared_path

    def _all_unlocked(self) -> list[tuple[str, dict]]:
        return [
            (path, entry)
            for path in self._paths()
            for entry in self._load_file(path)
        ]

    def _find_unlocked(self, *, key: str = None, auto_id: str = None):
        matches = [
            (path, entry)
            for path, entry in self._all_unlocked()
            if (key is not None and entry.get("key") == key)
            or (auto_id is not None and entry.get("id") == auto_id)
        ]
        if len(matches) > 1:
            raise ValueError("Duplicate automation registry identity")
        return matches[0] if matches else (None, None)

    def _upsert_unlocked(self, entry: dict) -> None:
        self._validate_entry(entry)
        target = self._path_for(entry)
        for path in self._paths():
            entries = self._load_file(path)
            kept = [item for item in entries if item.get("key") != entry["key"]]
            if path == target:
                kept.append(deepcopy(entry))
            if kept != entries:
                self._save_file(path, kept)
        if target not in self._paths():
            self._save_file(target, [deepcopy(entry)])

    def _remove_unlocked(self, key: str) -> bool:
        removed = False
        for path in self._paths():
            entries = self._load_file(path)
            kept = [entry for entry in entries if entry.get("key") != key]
            if len(kept) != len(entries):
                self._save_file(path, kept)
                removed = True
        return removed

    @staticmethod
    def _spec(
        name: str,
        *,
        release: str,
        shared: bool,
        purpose: str,
        steps: list,
        kind: str,
        schedule: str,
        slug: str,
        cleanup_when,
        spec: dict,
    ) -> dict:
        if steps is not None and not isinstance(steps, list):
            raise ValueError("Driven steps must be a list")
        if not isinstance(purpose, str):
            raise ValueError("Automation purpose must be text")
        steps = list(steps or [])
        derived = "step-driving" if steps else "release-level"
        kind = kind or derived
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        if (kind == "step-driving") != bool(steps):
            raise ValueError("step-driving requires steps; release-level forbids them")
        from orchestrator.automations import _cleanup_rules, _valid_cleanup_rule
        if not cleanup_when or not all(
            _valid_cleanup_rule(rule) for rule in _cleanup_rules(cleanup_when)
        ):
            raise ValueError("Valid cleanup_when is required for every automation")
        if any(not isinstance(step, str) or "." not in step for step in steps):
            raise ValueError("Driven steps must be phase.step identifiers")
        spec = provider_spec(spec)
        if name != spec["name"] or (schedule or None) != spec.get("schedule"):
            raise ValueError("Registry name/schedule must exactly match provider spec")
        key = stable_key(release, slug, shared)
        entry = {
            "key": key,
            "id": None,
            "name": name,
            "slug": slug,
            "kind": kind,
            "scope": "shared" if shared else "release",
            "release": None if shared else release,
            "purpose": purpose or "",
            "steps": steps,
            "schedule": schedule or None,
            "cleanup_when": cleanup_when,
            "status": "prepared",
            "attempts": [],
            "prepared_at": _now(),
        }
        entry["intent_hash"] = _intent_hash(entry, spec)
        return entry

    def prepare(
        self,
        name: str,
        *,
        release: str = None,
        shared: bool = False,
        purpose: str = "",
        steps: list = None,
        kind: str = None,
        schedule: str = None,
        slug: str,
        cleanup_when=None,
        spec: dict,
    ) -> dict:
        release = release or self.release
        desired = self._spec(
            name,
            release=release,
            shared=shared,
            purpose=purpose,
            steps=steps or [],
            kind=kind,
            schedule=schedule,
            slug=slug,
            cleanup_when=cleanup_when,
            spec=spec,
        )
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, current = self._find_unlocked(key=desired["key"])
            if current:
                if current["intent_hash"] == desired["intent_hash"]:
                    return deepcopy(current)
                raise ValueError(
                    "Cannot change existing automation intent; restore its exact reviewed "
                    "spec or safely retire it before preparing a replacement"
                )
            barrier = self._creation_barrier_unlocked(desired)
            if barrier:
                raise ValueError(barrier)
            self._upsert_unlocked(desired)
            return deepcopy(desired)

    def reconcile_create(
        self,
        key: str,
        observations: dict,
        *,
        spec: dict,
        executor: str = None,
        claim: bool = False,
    ) -> dict:
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(key=key)
            if not entry:
                raise ValueError("Automation intent is not prepared")
            spec = self._verify_spec(entry, spec)
            rows = self._observed_unlocked(entry, observations)
            observed = sorted(row["id"] for row in rows)
            if entry["status"] in _OWNED:
                return {
                    "status": entry["status"], "permission_to_create": False,
                    "reason": "Automation operation is unresolved; owning receipt or owner recovery required",
                    "observed_ids": observed,
                }
            if len(observed) > 1:
                entry["status"] = "blocked"
                entry["observed_ids"] = observed
                self._upsert_unlocked(entry)
                return {
                    "status": "blocked",
                    "permission_to_create": False,
                    "reason": "Multiple live automations match the stable identity",
                    "observed_ids": observed,
                }
            if observed:
                observed_id = observed[0]
                if (rows[0]["spec"] != spec
                        or entry.get("id") not in (None, observed_id)):
                    entry["status"] = "blocked"
                    entry["observed_ids"] = observed
                    self._upsert_unlocked(entry)
                    return {
                        "status": "blocked", "permission_to_create": False,
                        "reason": "Observed provider identity/spec differs; owner review required",
                    }
                barrier = self._creation_barrier_unlocked(entry)
                if barrier:
                    return {"status": entry["status"], "permission_to_create": False,
                            "reason": barrier}
                _, other = self._find_unlocked(auto_id=observed_id)
                if other and other["key"] != key:
                    raise ValueError("Observed provider id belongs to another automation")
                entry.update(
                    id=observed_id,
                    status="active",
                    registered_at=entry.get("registered_at") or _now(),
                    adopted_at=_now(),
                )
                entry.pop("observed_ids", None)
                self._upsert_unlocked(entry)
                return {
                    "status": "active",
                    "permission_to_create": False,
                    "adopted": True,
                    "entry": deepcopy(entry),
                }
            if entry["status"] == "active":
                entry["status"] = "missing"
                entry["missing_at"] = _now()
                self._upsert_unlocked(entry)
                return {
                    "status": "missing",
                    "permission_to_create": False,
                    "reason": "Registered automation is not visible; owner review required",
                }
            if entry["status"] in ("missing", "blocked"):
                return {
                    "status": entry["status"], "permission_to_create": False,
                    "reason": "Absent/duplicate identity requires owner-reviewed absence recovery",
                }
            barrier = self._creation_barrier_unlocked(entry)
            if barrier:
                return {"status": entry["status"], "permission_to_create": False,
                        "reason": barrier}
            if not claim:
                return {
                    "status": "prepared",
                    "permission_to_create": False,
                    "create_available": True,
                    "entry": deepcopy(entry),
                }
            if not executor or not executor.strip():
                raise ValueError("executor is required to claim automation creation")
            attempt = {
                "id": uuid.uuid4().hex,
                "owner": executor.strip(),
                "started_at": _now(),
                "status": "claimed",
            }
            entry["attempts"].append(attempt)
            entry["status"] = "creating"
            self._upsert_unlocked(entry)
            return {
                "status": "creating",
                "attempt_id": attempt["id"],
                "permission_to_create": True,
                "spec": spec,
            }

    def create_result(
        self,
        key: str,
        attempt_id: str,
        outcome: str,
        evidence: str,
        *,
        automation_id: str = None,
        spec: dict,
    ) -> dict:
        if outcome not in ("created", "not_created", "uncertain"):
            raise ValueError("Create outcome must be created, not_created, or uncertain")
        evidence = _evidence_hash(evidence)
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(key=key)
            if not entry or not entry["attempts"]:
                raise ValueError("No automation create claim")
            self._verify_spec(entry, spec)
            attempt = entry["attempts"][-1]
            if entry["status"] not in ("creating", "uncertain") or attempt["id"] != attempt_id:
                raise ValueError("Only the owning create attempt may record its result")
            attempt.update(
                status=outcome,
                evidence=evidence,
                acknowledged_at=_now(),
            )
            if outcome == "created":
                if not automation_id or not str(automation_id).strip():
                    raise ValueError("Created outcome requires automation id")
                _, other = self._find_unlocked(auto_id=str(automation_id).strip())
                if other and other["key"] != key:
                    raise ValueError("Automation id is already registered to another identity")
                entry.update(
                    id=str(automation_id).strip(),
                    status="active",
                    registered_at=_now(),
                )
            elif outcome == "not_created":
                entry["status"] = "prepared"
            else:
                entry["status"] = "uncertain"
            self._upsert_unlocked(entry)
            return deepcopy(entry)

    @staticmethod
    def _verify_spec(entry: dict, spec: dict) -> dict:
        spec = provider_spec(spec)
        if _intent_hash(entry, spec) != entry["intent_hash"]:
            raise ValueError("Provider spec does not match the reviewed intent hash")
        return spec

    @staticmethod
    def _observed_unlocked(entry: dict, observations: dict) -> list:
        """An exhaustive, timestamped read; match recorded id OR reviewed name."""
        if (not isinstance(observations, dict)
                or set(observations) != {"observed_at", "complete", "automations"}
                or observations["complete"] is not True
                or not isinstance(observations["automations"], list)):
            raise ValueError("Fresh complete observation envelope is required")
        try:
            observed_at = datetime.fromisoformat(observations["observed_at"])
            now = datetime.fromisoformat(_now())
            if observed_at.tzinfo is None or not 0 <= (now - observed_at).total_seconds() <= 300:
                raise ValueError()
            for field in ("prepared_at", "registered_at", "adopted_at", "missing_at"):
                if entry.get(field) and observed_at < datetime.fromisoformat(entry[field]):
                    raise ValueError()
            if entry["attempts"]:
                attempt = entry["attempts"][-1]
                if observed_at < datetime.fromisoformat(
                    attempt.get("acknowledged_at") or attempt["started_at"]
                ):
                    raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Provider observation is stale or has an invalid UTC timestamp") from None
        matched, seen = [], set()
        for row in observations["automations"]:
            if (not isinstance(row, dict) or set(row) != {"id", "spec"}
                    or not isinstance(row["id"], str) or not row["id"].strip()
                    or row["id"] in seen):
                raise ValueError("Observations require unique ids and complete normalized specs")
            seen.add(row["id"])
            actual = provider_spec(row["spec"])
            if (row["id"] == entry.get("id") or row["id"] in entry.get("observed_ids", [])
                    or actual["name"] == entry["name"]):
                matched.append({"id": row["id"], "spec": actual})
        return matched

    def _siblings_unlocked(self, entry: dict) -> list:
        if entry["scope"] != "release":
            return []
        return [
            sibling for _, sibling in self._all_unlocked()
            if sibling["key"] != entry["key"]
            and sibling.get("release") == entry["release"] and not _manual(sibling)
        ]

    def _creation_barrier_unlocked(self, entry: dict) -> str:
        if _manual(entry):
            return ""
        if any(s["kind"] == "release-level"
               and s["status"] in ("deleting", "delete_uncertain")
               for s in self._siblings_unlocked(entry)):
            return "Release-level deletion outstanding; no new worker/creation allowed"
        return ""

    def _deletion_barrier_unlocked(self, entry: dict) -> str:
        if _manual(entry):
            return "Shared/manual automations are exempt from automatic retirement"
        siblings = self._siblings_unlocked(entry)
        if entry["kind"] == "release-level":
            if any(s["kind"] == "step-driving" or s["status"] != "active"
                   for s in siblings):
                return "Cleanup barrier: child intents or unresolved sibling operations remain"
            # The release-wide driver is also the recovery worker for other helpers.
            if entry["slug"] == "push-reminders" and siblings:
                return "Cleanup barrier: retire release helpers before push-reminders"
        return ""

    def confirm_absent(
        self, key: str, observations: dict, reason: str, *,
        owner_confirmed: bool = False, no_inflight: bool = False,
    ) -> dict:
        evidence = _evidence_hash(reason)
        if not owner_confirmed or not no_inflight:
            raise ValueError("Owner confirmation and proof of no in-flight provider operation are required")
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(key=key)
            if not entry or entry["status"] not in (
                "uncertain", "missing", "blocked", "delete_uncertain"
            ):
                raise ValueError("Only uncertain/missing/blocked state supports absence recovery; settle owning live claims first")
            if self._observed_unlocked(entry, observations):
                raise ValueError("Cannot confirm absence while matching automations exist")
            if entry["status"] == "delete_uncertain":
                self._remove_unlocked(key)
                return {"status": "deleted", "key": key}
            entry["attempts"].append({
                "id": uuid.uuid4().hex,
                "owner": "release-owner",
                "started_at": _now(),
                "status": "confirmed_absent",
                "evidence": evidence,
            })
            entry.update(id=None, status="prepared")
            entry.pop("observed_ids", None)
            self._upsert_unlocked(entry)
            return deepcopy(entry)

    def abandon_prepared(
        self, key: str, observations: dict, reason: str, *,
        owner_confirmed: bool = False, terminal_check=None,
    ) -> dict:
        _evidence_hash(reason)
        if not owner_confirmed:
            raise ValueError("Explicit owner confirmation is required")
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(key=key)
            if not entry or entry["status"] != "prepared" or entry.get("id"):
                raise ValueError("Only ID-less prepared intents can be abandoned; resolve live operations first")
            if entry["attempts"] and entry["attempts"][-1]["status"] not in (
                "not_created", "confirmed_absent"
            ):
                raise ValueError("Unresolved creation cannot be abandoned")
            if _manual(entry):
                raise ValueError("Shared/manual intents cannot be automatically abandoned")
            if not callable(terminal_check) or not terminal_check(entry["release"]):
                raise ValueError("Abandon requires a terminal release")
            if self._observed_unlocked(entry, observations):
                raise ValueError("Cannot abandon while matching automations exist")
            self._remove_unlocked(key)
            return {"status": "abandoned", "key": key}

    def claim_delete(self, auto_id: str, executor: str) -> dict:
        if not executor or not executor.strip():
            raise ValueError("executor is required to claim automation deletion")
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(auto_id=auto_id)
            if not entry or entry["status"] != "active":
                raise ValueError("Only an active automation may be claimed for deletion")
            barrier = self._deletion_barrier_unlocked(entry)
            if barrier:
                return {"permission_to_delete": False, "reason": barrier, "key": entry["key"]}
            attempt = {
                "id": uuid.uuid4().hex,
                "owner": executor.strip(),
                "started_at": _now(),
                "status": "claimed",
            }
            entry["attempts"].append(attempt)
            entry["status"] = "deleting"
            self._upsert_unlocked(entry)
            return {
                "permission_to_delete": True,
                "attempt_id": attempt["id"],
                "automation_id": entry["id"],
                "key": entry["key"],
            }

    def delete_result(
        self, auto_id: str, attempt_id: str, outcome: str, evidence: str
    ) -> dict:
        if outcome not in ("deleted", "not_deleted", "uncertain"):
            raise ValueError("Delete outcome must be deleted, not_deleted, or uncertain")
        evidence = _evidence_hash(evidence)
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(auto_id=auto_id)
            if not entry or not entry["attempts"]:
                raise ValueError("No automation delete claim")
            attempt = entry["attempts"][-1]
            if (
                entry["status"] not in ("deleting", "delete_uncertain")
                or attempt["id"] != attempt_id
            ):
                raise ValueError("Only the owning delete attempt may record its result")
            attempt.update(
                status=outcome,
                evidence=evidence,
                acknowledged_at=_now(),
            )
            if outcome == "deleted":
                key = entry["key"]
                self._remove_unlocked(key)
                return {"status": "deleted", "key": key}
            entry["status"] = (
                "active" if outcome == "not_deleted" else "delete_uncertain"
            )
            self._upsert_unlocked(entry)
            return deepcopy(entry)

    def register(
        self,
        auto_id: str,
        name: str,
        release: str = None,
        shared: bool = False,
        purpose: str = "",
        steps: list = None,
        kind: str = None,
        schedule: str = None,
        slug: str = None,
        cleanup_when=None,
    ) -> dict:
        raise ValueError(
            "Direct register is disabled; prepare and reconcile-create with a complete "
            "reviewed spec and fresh full observations, or acknowledge the owning create"
        )

    def deregister(self, auto_id: str) -> bool:
        raise ValueError(
            "Direct deregistration is disabled; use claim-delete and delete-result"
        )

    def get(self, *, key: str = None, auto_id: str = None):
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            _, entry = self._find_unlocked(key=key, auto_id=auto_id)
            return deepcopy(entry) if entry else None

    def list(
        self,
        release: str = None,
        scope: str = None,
        step: str = None,
        kind: str = None,
    ) -> list:
        with file_lock(self.lock_path, _LOCK_TIMEOUT):
            entries = [entry for _, entry in self._all_unlocked()]
        if release is not None:
            entries = [entry for entry in entries if entry.get("release") == release]
        if scope is not None:
            entries = [entry for entry in entries if entry.get("scope") == scope]
        if step is not None:
            entries = [
                entry for entry in entries if step in (entry.get("steps") or [])
            ]
        if kind is not None:
            entries = [entry for entry in entries if kind_of(entry) == kind]
        return deepcopy(entries)
