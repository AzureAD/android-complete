"""Persisted release facts. Lifecycle, frontier and holds are projections."""
from __future__ import annotations
import json
import os
from copy import deepcopy
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from typing import Callable, ClassVar, Optional

from orchestrator.effects import execution_input_is_valid


SCHEMA_VERSION = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepState:
    """Persisted state for a single step."""
    status: str = "pending"          # pending | running (reserved) | done | skipped | blocked | in_flight
    completed_at: Optional[str] = None
    invalidated_at: Optional[str] = None
    invalidation_reason: Optional[str] = None
    note: Optional[str] = None
    by: Optional[str] = None         # 'agent' (stub) or 'human'
    links: list = field(default_factory=list)   # [{name, url}] — durable refs (wiki page, CG alerts)
    execution: Optional[dict] = None            # active {id, owner, started_at}; cleared at terminal
    data: dict = field(default_factory=dict)    # domain evidence and checkpoints only


@dataclass
class GateDecision:
    """A recorded human decision at a gate (audit trail)."""
    step: str
    decision: str                    # approved | denied | held
    at: str
    by: str = "human"
    comment: Optional[str] = None


@dataclass
class ReleaseState:
    """The Release State Record — one per monthly release."""
    schema_version: int = SCHEMA_VERSION
    workflow_revision: Optional[dict] = None
    release_id: str = ""             # e.g. 2026-07
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Release owner — the engineer running this release (release metadata). The
    # push reminders email this address; resolved from the signed-in user at init.
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    # The owner's IANA timezone (e.g. 'America/Los_Angeles'), captured at init from the
    # owner's machine. Phase due-ness + every fire_at_local are evaluated on THIS zone,
    # so a headless automation running in a UTC process still uses the owner's clock.
    timezone: Optional[str] = None
    # Code Complete Date — the anchor the phases hang off of (orchestrator's truth,
    # seeded from / written back to pipeline 3038). ccd is 'YYYY-MM-DD'.
    ccd: Optional[str] = None
    ccd_source: Optional[str] = None      # 'default' (2nd Wed) | 'override' | 'manual'
    ccd_conflict: Optional[str] = None    # a pipeline override date that DIFFERS from ccd (unresolved)
    # The month the release is NAMED for (the ship/rollout month) — the display name of the
    # release, e.g. 'the September 2026 release'. By convention this is the CCD month + 1
    # (a release that code-completes in August ships in September). Stored as 'YYYY-MM',
    # defaulted at init to CCD-month+1 and CONFIRMED by the owner. release_id stays the
    # work/CCD month; this is display-only so the docs/comms never misname the release.
    target_month: Optional[str] = None
    cancellation: Optional[dict] = None   # {reason, at}; independent from emergency halt
    halt: Optional[dict] = None           # {reason, at}
    active_conditionals: list = field(default_factory=list)
    # Readiness is derived from these facts and config.
    readiness_items: dict = field(default_factory=dict)   # item_id -> {status,...}
    # persisted detail
    steps: dict = field(default_factory=dict)         # "phase.step" -> StepState (as dict)
    gate_decisions: list = field(default_factory=list)
    last_notified_date: Optional[str] = None          # YYYY-MM-DD of the last daily digest sent
    last_status_email_date: Optional[str] = None       # YYYY-MM-DD of the last partner status email sent
    escalation_checkpoints: dict = field(default_factory=dict)  # successfully sent alert key -> {sent_at, target}
    notification_deliveries: dict = field(default_factory=dict)  # id -> immutable descriptor, status, attempts, completion
    resources: dict = field(default_factory=dict)  # release-owned external identities; survives step reopen
    _checkpoint: ClassVar[Optional[Callable[[], None]]] = None
    # Phase-2 release-pipeline runs — the RECORD of what verification resolved, reused by
    # the RC report + gate (no re-discovery). Because a re-triggered 'Trigger RC Testing'
    # stage spawns NEW MRWP runs, these are re-resolved (newest wins) — not a fixed cache.
    # Nested schema:
    #   { checker:      {run_id, when, resolved_at},
    #     orchestrator: {run_id, versions:{Common,Msal,Broker}, parked, resolved_at},
    #     final:        {orchestrator_run_id, mrwp_run_id, authenticator_build_id,
    #                    authenticator_version, resolved_at},
    #     rcs: [ {rc, ecs:{run_id,id_source,complete,ran,total,failed_stages,
    #                       yellow_stages,never_ran,tests,failed_suites,resolved_at},
    #            local:{...same...}, resolved_at} ] }
    # There is exactly ONE checker + ONE orchestrator, but MULTIPLE RC iterations (each a
    # re-trigger of RC Testing spawns a new ecs/local pair). The LATEST RC is rcs[-1] — the
    # report + gate always use it. A per-provider id change appends a new rc entry.
    pipeline_runs: dict = field(default_factory=dict)
    # Release payload versions, populated at Phase 2 (orchestrator_health) from the orchestrator
    # run tags — stored so later steps (e.g. the release announcement) reuse them instead of
    # re-discovering. Keys: common/msal/broker (version strings from Next*Version) and
    # authenticator (the release BRANCH, e.g. 'release/2026/08/22', from the AuthenticatorBranch
    # tag; the actual app version is resolved later and isn't needed here).
    versions: dict = field(default_factory=dict)

    # ---- persistence ----
    @classmethod
    def load(cls, path: str) -> "ReleaseState":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if type(data.get("schema_version")) is not int or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported release-state schema {data.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION}. Automatic migration is not supported; "
                "preserve this record and recover it using its original supported runtime."
            )
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                "Unknown release-state fields: " + ", ".join(sorted(unknown))
            )
        obj = cls(**data)
        from orchestrator.revision import validate_binding, is_hash
        validate_binding(obj.workflow_revision)
        obj._loaded_from_disk = True
        for name, value in (("cancellation", obj.cancellation), ("halt", obj.halt)):
            if value is not None and (
                not isinstance(value, dict)
                or not isinstance(value.get("reason"), str)
                or not isinstance(value.get("at"), str)
            ):
                raise ValueError(f"Invalid release {name} fact")
        if (
            not isinstance(obj.active_conditionals, list)
            or any(not isinstance(value, str) for value in obj.active_conditionals)
            or len(set(obj.active_conditionals)) != len(obj.active_conditionals)
        ):
            raise ValueError("Invalid active conditionals")
        if not isinstance(obj.readiness_items, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in obj.readiness_items.items()
        ):
            raise ValueError("Invalid readiness facts")
        if not isinstance(obj.gate_decisions, list):
            raise ValueError("Invalid gate decisions")
        gate_fields = {"step", "decision", "at", "by", "comment"}
        for decision in obj.gate_decisions:
            if (
                not isinstance(decision, dict)
                or set(decision) != gate_fields
                or not isinstance(decision.get("step"), str)
                or decision.get("decision") not in ("approved", "denied")
                or not isinstance(decision.get("at"), str)
                or not isinstance(decision.get("by"), str)
                or (
                    decision.get("comment") is not None
                    and not isinstance(decision.get("comment"), str)
                )
            ):
                raise ValueError("Invalid gate decisions")
        if not isinstance(obj.steps, dict):
            raise ValueError("Invalid step state map")
        step_fields = {f.name for f in fields(StepState)}
        for key, raw in obj.steps.items():
            if not isinstance(key, str) or not isinstance(raw, dict) or set(raw) - step_fields:
                raise ValueError(f"Invalid step state for {key}")
            step = StepState(**raw)
            if step.status not in (
                "pending", "running", "in_flight", "blocked", "done", "skipped"
            ):
                raise ValueError(
                    f"Invalid step status for {key}: {step.status!r}"
                )
            if step.execution is not None and not isinstance(step.execution, dict):
                raise ValueError(f"Invalid step execution for {key}")
            if step.execution is not None:
                if "approval" in step.execution:
                    from .approvals import validate_approval
                    if set(step.execution) != {"id", "owner", "started_at", "approval"}:
                        raise ValueError(f"Invalid approval execution fields for {key}")
                    validate_approval(step.execution["approval"], state=obj, key=key,
                                      status=step.status, started_at=step.execution.get("started_at"))
                review = step.execution.get("write_review")
                if "write_review" in step.execution and (
                    not isinstance(review, dict)
                    or set(review) != {"hash", "approved_by"}
                    or not is_hash(review["hash"])
                    or not isinstance(review["approved_by"], str)
                    or not review["approved_by"].strip()
                ):
                    raise ValueError(f"Invalid execution write review for {key}")
                if (
                    not isinstance(step.execution.get("id"), str)
                    or not step.execution["id"]
                    or not isinstance(step.execution.get("owner"), str)
                    or not step.execution["owner"]
                    or not isinstance(step.execution.get("started_at"), str)
                    or not step.execution["started_at"]
                    or not isinstance(step.execution.get("refresh", False), bool)
                ):
                    raise ValueError(f"Invalid step execution for {key}")
                effect_mode = step.execution.get("effect_mode")
                if effect_mode is not None and (
                    effect_mode not in ("idempotent", "transactional")
                    or step.execution.get("effect_recovery")
                    not in ("frozen", "match_current")
                    or not isinstance(step.execution.get("operation_key"), str)
                    or not step.execution["operation_key"]
                    or not isinstance(step.execution.get("effect_input"), dict)
                    or step.execution.get("owner") != "engine"
                    or not execution_input_is_valid(
                        key.split(".", 1)[-1], step.execution
                    )
                ):
                    raise ValueError(f"Invalid step effect execution for {key}")
                if step.status not in ("running", "in_flight", "blocked"):
                    raise ValueError(
                        f"Step execution for {key} is incompatible with {step.status}"
                    )
            if not isinstance(step.data, dict) or not isinstance(step.links, list):
                raise ValueError(f"Invalid step evidence for {key}")
            if "last_approval" in step.data:
                from .approvals import validate_closed_approval
                validate_closed_approval(step.data["last_approval"])
            review = step.data.get("last_write_review")
            if "last_write_review" in step.data and (
                not isinstance(review, dict)
                or set(review) != {"execution_id", "hash", "approved_by", "approved_at", "workflow_revision"}
                or any(not isinstance(value, str) or not value.strip() for value in review.values())
                or not is_hash(review["hash"]) or not is_hash(review["workflow_revision"])
            ):
                raise ValueError(f"Invalid terminal write review for {key}")
            for timestamp in (
                step.completed_at, step.invalidated_at
            ):
                if timestamp is not None and not isinstance(timestamp, str):
                    raise ValueError(f"Invalid step timestamp for {key}")
        if not isinstance(obj.notification_deliveries, dict):
            raise ValueError("Invalid notification ledger; owner recovery required")
        if not isinstance(obj.resources, dict):
            raise ValueError("Invalid resource registry; owner recovery required")
        if not isinstance(obj.pipeline_runs, dict) or not isinstance(obj.versions, dict):
            raise ValueError("Invalid release evidence maps")
        return obj

    def save(self, path: str) -> None:
        self.updated_at = _now()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(self), fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def checkpoint(self) -> None:
        """Persist an external-action boundary through the active CLI transaction."""
        if self._checkpoint is None:
            raise RuntimeError("External resource work requires a locked, persisted release state")
        self._checkpoint()

    # ---- step helpers ----
    @staticmethod
    def key(phase: str, step: str) -> str:
        return f"{phase}.{step}"

    def get_step(self, phase: str, step: str) -> StepState:
        raw = self.steps.get(self.key(phase, step))
        return StepState(**deepcopy(raw)) if raw else StepState()

    def set_step(self, phase: str, step: str, state: StepState) -> None:
        self.steps[self.key(phase, step)] = asdict(state)

    def record_versions(self, versions: dict) -> None:
        """Merge resolved payload versions (common/msal/broker/authenticator) into the record,
        ignoring blank values. Idempotent — safe to call on every discovery."""
        self.versions.update({k: v for k, v in (versions or {}).items() if v})

    def is_done(self, phase: str, step: str) -> bool:
        """Raw terminal fact only; workflow completion is StateProjection.step_complete."""
        return self.get_step(phase, step).status in ("done", "skipped")
