"""Read-only diagnostics for persisted facts, including partially corrupted records."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime

from orchestrator.effects import execution_input_is_valid
from orchestrator.outcomes import valid_links
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import StepKind, WorkflowDefinition


STEP_STATUSES = frozenset({"pending", "running", "in_flight", "blocked", "done", "skipped"})


@dataclass(frozen=True)
class InvariantViolation:
    code: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "severity": self.severity}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value):
    if not _text(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_step(state: ReleaseState, workflow: WorkflowDefinition, key: str):
    """Validate raw values before constructing StepState or dereferencing metadata."""
    issues = []

    def add(code, detail, severity="error"):
        issues.append(InvariantViolation(code, f"Step {key} {detail}", severity))

    definition = workflow.step_by_key.get(key)
    if not definition:
        add("unknown_step_record", "is removed/unknown.", "warning")
        return tuple(issues)
    if not isinstance(state.steps, dict):
        add("invalid_steps", "cannot be read: steps must be a mapping.")
        return tuple(issues)
    raw = state.steps.get(key, {})
    if not isinstance(raw, dict) or set(raw) - {f.name for f in fields(StepState)}:
        add("invalid_step_record", "has malformed record fields; owner repair required.")
        return tuple(issues)
    status = raw.get("status", "pending")
    if not isinstance(status, str) or status not in STEP_STATUSES:
        add("invalid_step_status", f"has unsupported status {status!r}.")
    if not isinstance(raw.get("data", {}), dict):
        add("invalid_step_data", "has non-mapping evidence data.")
    elif "last_write_review" in raw.get("data", {}):
        from orchestrator.revision import is_hash
        review = raw["data"]["last_write_review"]
        if (not isinstance(review, dict) or set(review) != {
                "execution_id", "hash", "approved_by", "approved_at", "workflow_revision"}
                or not all(_text(review.get(k)) for k in ("execution_id", "approved_by"))
                or _timestamp(review.get("approved_at")) is None
                or not is_hash(review.get("hash")) or not is_hash(review.get("workflow_revision"))):
            add("invalid_closed_write_review", "has malformed closed write authorization evidence.")
    if isinstance(raw.get("data"), dict) and "last_approval" in raw["data"]:
        from .approvals import validate_closed_approval
        try:
            validate_closed_approval(raw["data"]["last_approval"])
        except (ValueError, TypeError) as exc:
            add("invalid_closed_approval", f"has malformed closed approval evidence: {exc}")
    if not valid_links(raw.get("links", [])):
        add("invalid_step_links", "requires a list of evidence link mappings.")
    if raw.get("invalidated_at") is not None and _timestamp(raw["invalidated_at"]) is None:
        add("invalid_execution_generation", "has malformed invalidation metadata.")
    execution = raw.get("execution")
    if execution is None:
        if status == "running" and (definition.approval_command or definition.kind == StepKind.EXTERNAL or (
            definition.effect_mode and definition.effect_mode.writes_external_state)):
            add("missing_execution", "is running without an execution owner.")
        return tuple(issues)
    if not isinstance(execution, dict):
        add("invalid_execution", "has non-mapping execution metadata.")
        return tuple(issues)
    if "write_review" in execution or definition.write_command:
        from orchestrator.revision import is_hash
        review = execution.get("write_review")
        if (not definition.write_command or definition.kind != StepKind.EXTERNAL
                or not isinstance(review, dict) or set(review) != {"hash", "approved_by"}
                or not is_hash(review.get("hash")) or not _text(review.get("approved_by"))):
            add("invalid_write_review", "requires the exact reviewed write authorization.")
    if not all(_text(execution.get(k)) for k in ("id", "owner", "started_at")) or not _timestamp(execution.get("started_at")):
        add("invalid_execution", "requires a nonempty execution id/owner and valid started_at.")
    if _text(execution.get("id")) and any(
        other_key != key and isinstance(other, dict) and isinstance(other.get("execution"), dict)
        and other["execution"].get("id") == execution["id"]
        for other_key, other in state.steps.items()
    ):
        add("duplicate_execution", f"shares execution identity {execution['id']} with another step.")
    if "refresh" in execution and type(execution["refresh"]) is not bool:
        add("invalid_execution", "has a non-boolean refresh marker.")
    if execution.get("refresh") is True and execution.get("previous_status") not in (None, "done", "blocked", "pending"):
        add("invalid_execution", "has an invalid refresh baseline status.")
    if status not in ("running", "in_flight", "blocked"):
        add("inactive_execution", f"retains an active execution while {status!r}.")
    approval = execution.get("approval")
    if "approval" in execution:
        from .approvals import validate_approval
        try:
            if (not definition.is_gate or not definition.approval_command
                    or set(execution) != {"id", "owner", "started_at", "approval"}):
                raise ValueError("Approval ownership requires an external gate and exact execution fields")
            validate_approval(approval, state=state, key=key, status=status,
                              started_at=execution.get("started_at"))
        except (ValueError, TypeError) as exc:
            add("invalid_approval_execution", f"has invalid approval ownership: {exc}")
    if definition.kind not in (StepKind.AUTO, StepKind.EXTERNAL) and not (
            definition.is_gate and definition.approval_command and "approval" in execution):
        add("misclassified_execution", "cannot own an execution: it is human/gate work.")
    effectful = bool(definition.effect_mode and definition.effect_mode.writes_external_state)
    has_effect = any(k in execution for k in ("effect_mode", "effect_recovery", "effect_input", "operation_key"))
    if has_effect or (definition.kind == StepKind.AUTO and effectful):
        if (
            not effectful
            or execution.get("effect_mode") != definition.effect_mode.value
            or execution.get("effect_recovery") != definition.effect_recovery.value
            or execution.get("owner") != "engine"
            or not _text(execution.get("operation_key"))
            or not execution_input_is_valid(definition.id, execution)
            or execution.get("notification_id") is not None
        ):
            add("invalid_effect_execution", "has execution metadata that contradicts its effect policy/input hash.")
    elif definition.kind == StepKind.AUTO:
        add("misclassified_execution", "is read-only auto work with an unexpected execution owner.")
    invalidated = raw.get("invalidated_at")
    if invalidated:
        at, started = _timestamp(invalidated), _timestamp(execution.get("started_at"))
        if at is not None and started is not None:
            if (at.tzinfo is None) != (started.tzinfo is None):
                add("invalid_execution_generation", "has inconsistent execution/invalidation time zones.")
            elif at > started or status == "blocked":
                add("invalidated_execution", (
                    "retains an already-invalidated engine effect; automatic settlement/replay is unsafe. "
                    "Owner-reviewed recovery or state repair is required; never reset its ownership."
                    if definition.kind == StepKind.AUTO and effectful
                    else "retains invalidated ownership for review."),
                    "warning" if status == "blocked" else "error")
    notification_id = execution.get("notification_id")
    if notification_id is not None and not _text(notification_id):
        add("invalid_notification_binding", "has a malformed notification identity.")
    elif notification_id is not None:
        from orchestrator.delivery import require_receipt, validate_record_state

        ledger = state.notification_deliveries.get(notification_id) if isinstance(state.notification_deliveries, dict) else None
        try:
            item = validate_record_state(state, ledger)
            if ledger["status"] == "sent":
                require_receipt(ledger)
            attempt = ledger["attempts"][-1] if ledger["attempts"] else {}
            if (item["id"] != notification_id or item["completion"].get("kind") != "step"
                    or f'{item["scope"].get("phase")}.{item["scope"].get("step")}' != key
                    or attempt.get("id") != execution.get("id")
                    or attempt.get("owner") != execution.get("owner")):
                raise ValueError("Notification identity/owner does not match its bound execution.")
        except (ValueError, TypeError) as exc:
            add("invalid_notification_binding", f"has invalid delivery evidence: {exc}")
    return tuple(issues)


def validate_snapshot(state: ReleaseState, workflow: WorkflowDefinition, projection=None):
    """Never let malformed nested state prevent inspection or targeted recovery."""
    issues = []
    if not isinstance(state.steps, dict):
        issues.append(InvariantViolation("invalid_steps", "Steps must be a mapping; owner repair required."))
    else:
        for key in state.steps:
            if not isinstance(key, str):
                issues.append(InvariantViolation("invalid_step_record", "Step keys must be strings."))
                continue
            issues.extend(validate_step(state, workflow, key))
    if not isinstance(state.gate_decisions, list):
        issues.append(InvariantViolation("invalid_gate_decision", "Gate decisions must be a list."))
        return tuple(issues)
    latest = {}
    for decision in state.gate_decisions:
        if not isinstance(decision, dict) or not _text(decision.get("step")):
            issues.append(InvariantViolation("invalid_gate_decision", "Malformed gate decision; owner repair required."))
            continue
        key = decision["step"]
        definition = workflow.step_by_key.get(key)
        if not definition or not definition.is_gate:
            issues.append(InvariantViolation(
                "decision_for_unknown_gate", f"Gate decision references unknown/non-gate step {key!r}.",
                "error" if definition else "warning"))
        if decision.get("decision") not in ("approved", "denied"):
            issues.append(InvariantViolation("invalid_gate_decision", f"Gate {key!r} has invalid decision {decision.get('decision')!r}."))
        latest[key] = decision.get("decision")
    if isinstance(state.steps, dict):
        for key, definition in workflow.step_by_key.items():
            if not definition.is_gate:
                continue
            raw = state.steps.get(key, {})
            if not isinstance(raw, dict):
                continue
            status = raw.get("status", "pending")
            if status in ("done", "skipped") and (status != "done" or latest.get(key) != "approved"):
                issues.append(InvariantViolation("unapproved_terminal_gate", f"Gate {key} is terminal without approval; it projects as pending.", "warning"))
            if latest.get(key) == "approved" and status != "done":
                issues.append(InvariantViolation("approval_without_completion", f"Gate {key} is approved but its step status is {status!r}.", "warning"))
    return tuple(issues)
