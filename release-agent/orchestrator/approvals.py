"""Frozen external-gate authorization, distinct from automatic effects."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import hashlib
import json
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ApprovalRequest:
    org: str
    project: str
    build_id: int
    stage: str
    approval_id: str
    comment: str

    def __post_init__(self):
        for name in ("org", "project", "stage", "approval_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"Approval request requires nonempty {name}")
        url = urlsplit(self.org)
        if url.scheme != "https" or not url.netloc or url.username or url.password or url.query or url.fragment:
            raise ValueError("Approval organization must be an HTTPS URL without credentials/query/fragment")
        if type(self.build_id) is not int or self.build_id <= 0:
            raise ValueError("Approval build_id must be a positive integer")
        if not isinstance(self.comment, str):
            raise ValueError("Approval comment must be text")


@dataclass(frozen=True, eq=False)
class ApprovalPermit:
    """One-process, one-call authority; never persisted or reconstructed."""
    phase: str
    step: str
    execution_id: str


def request_from_dict(value) -> ApprovalRequest:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(ApprovalRequest)}:
        raise ValueError("Approval request fields must exactly match the frozen request contract")
    return ApprovalRequest(**value)


def request_hash(state, key, request, workflow_revision):
    body = {
        "release": state.release_id, "step": key,
        "generation": (state.steps.get(key) or {}).get("invalidated_at") or "initial",
        "workflow_revision": workflow_revision, "request": asdict(request),
    }
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Approval timestamps must be timezone-aware ISO strings")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Approval timestamps must include their timezone")
    return parsed


def validate_approval(value, *, state=None, key=None, status=None, started_at=None):
    from .revision import is_hash, revision_id

    if not isinstance(value, dict) or set(value) != {
        "request", "request_hash", "workflow_revision", "approved_by",
        "submission_started_at", "receipt",
    }:
        raise ValueError("Invalid approval authorization fields")
    request = request_from_dict(value["request"])
    if not is_hash(value["request_hash"]) or not is_hash(value["workflow_revision"]):
        raise ValueError("Approval authorization requires request and workflow hashes")
    if not isinstance(value["approved_by"], str) or not value["approved_by"].strip():
        raise ValueError("Approval authorization requires approved_by")
    if state is not None:
        if (value["workflow_revision"] != revision_id(state.workflow_revision)
                or value["request_hash"] != request_hash(state, key, request, value["workflow_revision"])):
            raise ValueError("Approval request/workflow/generation binding changed")
    attempted = value["submission_started_at"]
    attempt_time = _timestamp(attempted) if attempted is not None else None
    if started_at is not None and attempt_time is not None and attempt_time < _timestamp(started_at):
        raise ValueError("Approval attempt predates its reservation")
    if status is not None and (
        (status == "running" and attempted is not None)
        or (status in ("in_flight", "blocked") and attempted is None)
        or status not in ("running", "in_flight", "blocked")
    ):
        raise ValueError("Approval execution status contradicts its attempt boundary")
    receipt = value["receipt"]
    if receipt is not None:
        if (not isinstance(receipt, dict) or set(receipt) != {"approval_id", "status", "observed_at"}
                or receipt["approval_id"] != request.approval_id or receipt["status"] != "approved"
                or attempt_time is None or _timestamp(receipt["observed_at"]) < attempt_time):
            raise ValueError("Approval receipt must confirm the exact attempted approval")
    return request


def validate_closed_approval(value):
    if not isinstance(value, dict) or not isinstance(value.get("execution_id"), str) or not value["execution_id"]:
        raise ValueError("Closed approval receipt requires its execution identity")
    approval = {key: item for key, item in value.items() if key != "execution_id"}
    validate_approval(approval)
    if approval["receipt"] is None:
        raise ValueError("Closed approval requires a confirmed provider receipt")


def checkpoint_transition(orch, operation):
    """Rollback only unsaved local mutations; durable ownership is never cleared."""
    from .transitions import TransitionResult

    before = deepcopy(asdict(orch.state))
    result = operation()
    if isinstance(result, TransitionResult) and not result.changed:
        raise ValueError(result.message)
    try:
        orch.state.checkpoint()
    except BaseException:
        for name, value in before.items():
            setattr(orch.state, name, value)
        if isinstance(result, ApprovalPermit):
            orch._transition_kernel().discard_approval_permit(result)
        raise
    return result


def preview(orch, phase, step, *, comment=""):
    from . import revision
    from .handler_contracts import HookRole
    from .outcomes import Blocked

    revision.assert_current(orch)
    kernel = orch._transition_kernel()
    if rejected := kernel.validate_approval_start(phase, step):
        raise ValueError(rejected.message)
    if orch.mocks.get(f"{phase}.{step}"):
        raise ValueError("Gate approval mocks are preview-only; use injected provider fakes for offline execution")
    handler = orch.handler(phase, step)
    generation = kernel._generation(orch.state.get_step(phase, step))
    request = handler.prepare_approval(orch.context(
        phase, step, parameters={"comment": comment}, role=HookRole.APPROVAL_PREPARE))
    revision.assert_current(orch)
    if (rejected := kernel.validate_approval_start(phase, step)):
        raise ValueError(rejected.message)
    if kernel._generation(orch.state.get_step(phase, step)) != generation:
        raise ValueError("Gate changed while preparing the approval; review again")
    if isinstance(request, Blocked):
        raise ValueError(request.reason)
    binding = revision.revision_id(orch.state.workflow_revision)
    return {
        "phase": phase, "step": step, "request": asdict(request),
        "review_hash": request_hash(orch.state, f"{phase}.{step}", request, binding),
        "permission_to_execute": False,
    }


def execute(orch, phase, step, *, comment="", review_hash=None, approved_by=None,
            executor=None, execution_id=None, reserve_only=False):
    from . import revision
    from .handler_contracts import HookRole

    revision.assert_current(orch)
    approved_by = approved_by.strip() if isinstance(approved_by, str) else approved_by
    if orch.mocks.get(f"{phase}.{step}"):
        raise ValueError("Gate approval mocks cannot authorize provider receipts; use injected provider fakes")
    kernel = orch._transition_kernel()
    handler = orch.handler(phase, step)
    record = orch.state.get_step(phase, step)
    if record.execution:
        if not execution_id or record.execution.get("id") != execution_id:
            raise ValueError("Gate already owns work; provide its exact --execution-id to reconcile, never resubmit")
        if rejected := kernel.validate_approval_owner(phase, step, execution_id):
            raise ValueError(rejected.message)
        approval = record.execution["approval"]
        if comment and comment != approval["request"]["comment"]:
            raise ValueError("Approval comment changed after reservation")
        if review_hash and review_hash != approval["request_hash"]:
            raise ValueError("Approval review hash changed after reservation")
        if approved_by and approved_by != approval["approved_by"]:
            raise ValueError("Approval reviewer changed after reservation")
    else:
        if execution_id:
            raise ValueError("Approval execution is no longer active; inspect the saved gate decision")
        prepared = preview(orch, phase, step, comment=comment)
        if review_hash != prepared["review_hash"]:
            raise ValueError("An exact current --review-hash is required; preview and review the approval first")
        checkpoint_transition(orch, lambda: kernel.reserve_approval(
            phase, step, request_from_dict(prepared["request"]), review_hash,
            approved_by, executor or approved_by))
        execution_id = orch.step_execution(phase, step)["id"]

    record = orch.state.get_step(phase, step)
    approval = record.execution["approval"]
    if reserve_only:
        return {"status": "reserved" if approval["submission_started_at"] is None else "recovery_required",
                "phase": phase, "step": step, "execution_id": execution_id, "permission_to_execute": False}
    if approval["receipt"] is None:
        if approval["submission_started_at"] is None:
            # Resuming an unattempted reservation still requires the original review.
            if review_hash != approval["request_hash"] or approved_by != approval["approved_by"]:
                raise ValueError("Unattempted approval requires its original --review-hash and --approved-by")
            permit = checkpoint_transition(
                orch, lambda: kernel.begin_approval_submission(phase, step, execution_id))
            generation = kernel._generation(orch.state.get_step(phase, step))
            try:
                result = handler.submit_approval(orch.context(
                    phase, step, permit=permit, role=HookRole.APPROVAL))
                if result[0] and not orch._approval_results.get(permit):
                    raise ValueError("Approval handler reported success without a confirmed provider submission")
            finally:
                kernel.discard_approval_permit(permit)
                orch._approval_results.pop(permit, None)
        else:
            generation = kernel._generation(orch.state.get_step(phase, step))
            result = handler.reconcile_approval(orch.context(
                phase, step, execution_id=execution_id, role=HookRole.APPROVAL_RECONCILE))
        if kernel._generation(orch.state.get_step(phase, step)) != generation:
            raise ValueError("Gate execution changed during provider IO; stale result was not applied")
        ok, detail = result
        if not ok:
            checkpoint_transition(orch, lambda: kernel.hold_approval(phase, step, execution_id, detail))
            return {"status": "recovery_required", "phase": phase, "step": step,
                    "execution_id": execution_id, "message": detail, "permission_to_execute": False}
        checkpoint_transition(orch, lambda: kernel.record_approval_receipt(phase, step, execution_id))
    rejected = kernel.validate_approval_owner(phase, step, execution_id, active=True)
    if rejected:
        return {"status": "receipt_recorded", "phase": phase, "step": step,
                "execution_id": execution_id, "message": rejected.message, "permission_to_execute": False}
    transition = checkpoint_transition(
        orch, lambda: kernel.finalize_approval(phase, step, execution_id))
    return {"status": "approved", "phase": phase, "step": step,
            "execution_id": execution_id, "message": transition.message, "permission_to_execute": False}
