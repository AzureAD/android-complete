"""Generic eligibility and state transitions for configured release steps."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import uuid
from typing import Callable, Optional

from orchestrator.projection import StateProjection
from orchestrator.effects import EffectMode, EffectRecovery
from orchestrator.invariants import validate_snapshot, validate_step
from orchestrator.outcomes import AutoOutcome, Blocked, Done, InProgress, require_auto_outcome, valid_links
from orchestrator.state import GateDecision, ReleaseState, StepState
from orchestrator.workflow import PhaseDefinition, StepDefinition, StepKind, WorkflowDefinition
from orchestrator.approvals import ApprovalPermit, ApprovalRequest


class TransitionIntent(str, Enum):
    EXECUTE = "execute"
    PREPARE = "prepare"
    POLL = "poll"
    RESERVE = "reserve"
    RECORD = "record"
    REFRESH = "refresh"
    COMPLETE = "complete"
    SKIP = "skip"
    REOPEN = "reopen"
    MOCK = "mock"
    EFFECT = "effect"
    WRITE = "write"
    RECOVER_EVIDENCE = "recover_evidence"


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str = ""
    phase: Optional[PhaseDefinition] = None
    step: Optional[StepDefinition] = None
    execution: Optional[dict] = None


@dataclass(frozen=True)
class TransitionResult:
    kind: str
    message: str
    phase: Optional[str] = None
    step: Optional[str] = None
    changed: bool = False
    affected: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class OutcomePermit:
    """Single-use, in-memory invocation right; never serialized into release state."""

    phase: str
    step: str
    intent: TransitionIntent


class EligibilityEvaluator:
    """One policy for every event that targets a configured step."""

    def __init__(
        self,
        state: ReleaseState,
        workflow: WorkflowDefinition,
        projection: StateProjection,
    ):
        self.state = state
        self.workflow = workflow
        self.projection = projection

    def evaluate(
        self,
        intent: TransitionIntent,
        phase_id: str,
        step_id: str,
    ) -> Eligibility:
        if self.projection.revision_problem:
            return Eligibility(False, self.projection.revision_problem)
        phase = self.workflow.phase(phase_id)
        step = self.workflow.step(phase_id, step_id)
        if not phase or not step:
            return Eligibility(False, f"No such step: {phase_id}/{step_id}")
        errors = [v.message for v in validate_snapshot(self.state, self.workflow) if v.severity == "error"]
        if errors:
            return Eligibility(False, "Invalid release state: " + "; ".join(errors))
        record = self.state.get_step(phase_id, step_id)
        execution = (
            dict(record.execution) if isinstance(record.execution, dict) else {}
        )
        selection = self.projection.scheduling()
        phase_readiness = selection.phase(phase_id)
        step_readiness = selection.step(phase_id, step_id)

        if intent == TransitionIntent.REOPEN:
            denied = bool(
                step.is_gate
                and (self.projection.latest_gate_decision(step) or {}).get("decision")
                == "denied"
            )
            closure = self.workflow.invalidation_closure(phase_id, step_id)
            has_downstream_state = any(
                self.state.get_step(item.phase_id, item.id).status != "pending"
                or any(
                    decision.get("step") == item.key
                    for decision in self.state.gate_decisions
                )
                for item in closure
            )
            if (
                record.status == "pending"
                and not denied
                and not execution
                and not has_downstream_state
            ):
                return Eligibility(False, f"Step is already pending: {phase_id}/{step_id}")
            return Eligibility(True, phase=phase, step=step, execution=execution)

        refresh_reservation = (
            intent == TransitionIntent.RESERVE
            and step.kind == StepKind.EXTERNAL
            and step.repeatable
            and step_readiness.complete
        )
        if (
            intent != TransitionIntent.REFRESH
            and not refresh_reservation
            and step_readiness.complete
        ):
            return Eligibility(
                False,
                f"Already {record.status}: {phase_id}/{step_id}; no action required.",
                phase,
                step,
                execution,
            )

        projected_status = selection.status
        if intent == TransitionIntent.POLL:
            if projected_status in ("halted", "blocked", "readiness_gate", "cancelled"):
                return Eligibility(
                    False,
                    f"Step is not currently eligible: release is {projected_status}.",
                    phase,
                    step,
                    execution,
                )
            if (
                step.kind != StepKind.EXTERNAL
                or not step.pollable
                or record.status != "in_flight"
            ):
                return Eligibility(
                    False,
                    "Only pollable in-flight external steps may be polled.",
                    phase,
                    step,
                    execution,
                )
            if (
                not selection.frontier or selection.frontier.id != phase_id
                or not phase_readiness.due or not step_readiness.prerequisites_met
                or not step_readiness.time_ready
            ):
                return Eligibility(False, "Poll is outside the ready phase/prerequisite/time frontier.", phase, step)
            return Eligibility(True, phase=phase, step=step, execution=execution)
        if intent == TransitionIntent.REFRESH:
            if projected_status in ("halted", "blocked", "readiness_gate", "cancelled"):
                return Eligibility(
                    False,
                    f"Step is not currently eligible: release is {projected_status}.",
                    phase,
                    step,
                    execution,
                )
            frontier = selection.frontier
            if projected_status == "complete" or not frontier or frontier.id != phase_id:
                return Eligibility(
                    False,
                    "Completed work may be refreshed only while its owning phase "
                    "is the current frontier.",
                    phase,
                    step,
                    execution,
                )
            if step.kind != StepKind.EXTERNAL or not step.repeatable:
                return Eligibility(
                    False,
                    "Only repeatable external steps accept refreshed results.",
                    phase,
                    step,
                    execution,
                )
            if not step_readiness.complete:
                return Eligibility(
                    False,
                    "Refresh requires a previously completed step.",
                    phase,
                    step,
                    execution,
                )
            if record.status == "skipped" or execution:
                return Eligibility(
                    False,
                    "Skipped or reserved work cannot be refreshed.",
                    phase,
                    step,
                    execution,
                )
            if not phase_readiness.due or not step_readiness.time_ready:
                return Eligibility(
                    False,
                    "Step refresh is not currently within its configured time window.",
                    phase,
                    step,
                    execution,
                )
            if not step_readiness.prerequisites_met:
                return Eligibility(False, "Waiting on prerequisite steps to complete.", phase, step)
            return Eligibility(True, phase=phase, step=step, execution=execution)
        if refresh_reservation:
            if projected_status in ("halted", "blocked", "readiness_gate", "cancelled"):
                return Eligibility(
                    False,
                    f"Step is not currently eligible: release is {projected_status}.",
                    phase,
                    step,
                    execution,
                )
            frontier = selection.frontier
            if projected_status == "complete" or not frontier or frontier.id != phase_id:
                return Eligibility(
                    False,
                    "Completed work may be refreshed only while its owning phase "
                    "is the current frontier.",
                    phase,
                    step,
                    execution,
                )
            if execution:
                return Eligibility(
                    False,
                    f"Reserved by {execution.get('owner')} "
                    f"(execution {execution.get('id')}).",
                    phase,
                    step,
                    execution,
                )
            if record.status == "skipped":
                return Eligibility(
                    False,
                    "Skipped work cannot be refreshed.",
                    phase,
                    step,
                    execution,
                )
            if (
                not phase_readiness.due
                or not step_readiness.time_ready
            ):
                return Eligibility(False, "Owning phase is not due.", phase, step)
            if not step_readiness.prerequisites_met:
                return Eligibility(False, "Waiting on prerequisite steps to complete.", phase, step)
            return Eligibility(True, phase=phase, step=step, execution={})
        if projected_status in (
            "complete", "halted", "blocked", "readiness_gate", "cancelled"
        ):
            return Eligibility(
                False,
                f"Step is not currently eligible: release is {projected_status}.",
                phase,
                step,
                execution,
            )
        frontier = selection.frontier
        if not frontier or frontier.id != phase_id or not phase_readiness.due:
            return Eligibility(
                False,
                "Step is not currently eligible: release/phase is not ready.",
                phase,
                step,
                execution,
            )
        if not step_readiness.prerequisites_met:
            return Eligibility(
                False,
                "Waiting on prerequisite steps to complete.",
                phase,
                step,
                execution,
            )
        if not step_readiness.time_ready:
            return Eligibility(
                False,
                "Step is not currently eligible: scheduled time has not arrived.",
                phase,
                step,
                execution,
            )

        if intent == TransitionIntent.PREPARE and record.status in ("running", "in_flight"):
            return Eligibility(
                False,
                f"Step is already {record.status}; use its configured follow-up or poller.",
                phase,
                step,
                execution,
            )
        if intent == TransitionIntent.EXECUTE and step.kind != StepKind.AUTO:
            return Eligibility(False, "Only auto steps execute in-process.", phase, step, execution)
        if intent == TransitionIntent.MOCK and (step.is_gate or not step_readiness.mocked):
            return Eligibility(False, "Only explicitly mocked non-gate steps accept mock outcomes.", phase, step)
        if intent == TransitionIntent.PREPARE and step.kind == StepKind.AUTO:
            return Eligibility(
                False,
                "Auto steps run in-process via next, not step-action.",
                phase,
                step,
                execution,
            )
        if intent == TransitionIntent.RESERVE:
            if step.is_gate:
                return Eligibility(
                    False,
                    "Gate steps cannot be reserved; they require approve or deny.",
                    phase,
                    step,
                    execution,
                )
            if step.kind != StepKind.EXTERNAL:
                return Eligibility(
                    False,
                    "Only external steps support execution reservations.",
                    phase,
                    step,
                    execution,
                )
            if execution:
                return Eligibility(
                    False,
                    f"Reserved by {execution.get('owner')} "
                    f"(execution {execution.get('id')}).",
                    phase,
                    step,
                    execution,
                )
        if intent == TransitionIntent.RECORD and step.is_gate:
            return Eligibility(
                False,
                f"record-step cannot complete a human gate: {phase_id}/{step_id}",
                phase,
                step,
                execution,
            )
        if intent == TransitionIntent.RECORD and step.kind != StepKind.EXTERNAL:
            return Eligibility(
                False,
                "Only external steps accept generic record-step results.",
                phase,
                step,
                execution,
            )
        if intent == TransitionIntent.SKIP and step.is_gate:
            return Eligibility(
                False,
                f"Cannot skip {phase_id}/{step_id}: gate steps require approve or deny.",
                phase,
                step,
                execution,
            )
        if intent == TransitionIntent.COMPLETE:
            if step.is_gate:
                return Eligibility(
                    False,
                    f"Cannot complete {phase_id}/{step_id}: gate steps require "
                    "approve or deny.",
                    phase,
                    step,
                    execution,
                )
            if execution and execution.get("effect_mode"):
                return Eligibility(
                    False,
                    "Engine-owned effects must finish through execute/reconcile; "
                    "done cannot clear their ownership.",
                    phase,
                    step,
                    execution,
                )
            if execution:
                return Eligibility(True, phase=phase, step=step, execution=execution)
            if not step.is_human_action:
                return Eligibility(
                    False,
                    f"Cannot complete {phase_id}/{step_id}: done is only for "
                    "human non-gate actions.",
                    phase,
                    step,
                    execution,
                )
        if execution and intent not in (TransitionIntent.COMPLETE, TransitionIntent.RECORD):
            return Eligibility(
                False,
                f"Reserved by {execution.get('owner')} (execution {execution.get('id')}). "
                "Review the reserved execution first.",
                phase,
                step,
                execution,
            )
        return Eligibility(True, phase=phase, step=step, execution=execution)


class TransitionKernel:
    """The only generic writer for release/step lifecycle state."""

    def __init__(
        self,
        state: ReleaseState,
        workflow: WorkflowDefinition,
        projection_factory: Callable[[], StateProjection],
        now: Callable[[], str],
    ):
        self.state = state
        self.workflow = workflow
        self.projection_factory = projection_factory
        self.now = now
        self._outcome_permits: dict[OutcomePermit, tuple] = {}
        self._approval_permits: dict[ApprovalPermit, tuple] = {}

    def _invalid(self, phase_id=None, step_id=None):
        if problem := self.projection_factory().revision_problem:
            return self._reject(problem, phase_id, step_id)
        issues = (validate_step(self.state, self.workflow, f"{phase_id}.{step_id}")
                  if phase_id and step_id else validate_snapshot(self.state, self.workflow))
        errors = [v.message for v in issues if v.severity == "error"]
        return self._reject("Invalid release state: " + "; ".join(errors), phase_id, step_id) if errors else None

    @staticmethod
    def _generation(record: StepState) -> tuple:
        return (
            record.status, record.completed_at, record.invalidated_at,
            deepcopy(record.execution),
        )

    def _owned_outcome_check(
        self, phase_id: str, step_id: str, execution_id: str
    ) -> TransitionResult | None:
        if rejected := self._invalid():
            return rejected
        projection = self.projection_factory()
        if projection.workflow is not self.workflow:
            return self._reject("Workflow generation changed; result was not applied.", phase_id, step_id)
        step = self.workflow.step(phase_id, step_id)
        if not step:
            return self._reject(f"No such step: {phase_id}/{step_id}", phase_id, step_id)
        if step.is_gate or step.kind not in (StepKind.AUTO, StepKind.EXTERNAL):
            return self._reject("Only external work or auto effects accept owned results.", phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        execution = record.execution or {}
        if not execution_id or execution.get("id") != execution_id:
            return self._reject("Only the owning execution can record this result.", phase_id, step_id)
        effect = bool(execution.get("effect_mode"))
        if step.kind == StepKind.AUTO and (
            not effect or not step.effect_mode
            or not step.effect_mode.writes_external_state
            or execution["effect_mode"] != step.effect_mode.value
            or execution.get("owner") != "engine"
        ):
            return self._reject("Auto results require the configured engine-owned effect.", phase_id, step_id)
        if step.kind == StepKind.EXTERNAL and effect:
            return self._reject("External results cannot settle an auto effect.", phase_id, step_id)
        if record.invalidated_at and (
            (record.status == "blocked" and not execution.get("write_review"))
            or datetime.fromisoformat(record.invalidated_at.replace("Z", "+00:00"))
            > datetime.fromisoformat(execution["started_at"].replace("Z", "+00:00"))
        ):
            return self._reject("Execution generation was invalidated; owner review required.", phase_id, step_id)
        if record.status not in (
            ("running", "in_flight", "blocked")
            if effect or execution.get("write_review") else ("running", "in_flight")
        ):
            return self._reject("Interrupted execution requires owner review before done or reopen.", phase_id, step_id)
        selection = projection.scheduling()
        readiness = selection.step(phase_id, step_id)
        if (
            not selection.frontier or selection.frontier.id != phase_id
            or not readiness.prerequisites_met
        ):
            return self._reject("Execution no longer belongs to the ready prerequisite frontier.", phase_id, step_id)
        return None

    def _notification_outcome_check(
        self, phase_id: str, step_id: str,
    ) -> TransitionResult | None:
        execution = self.state.get_step(phase_id, step_id).execution or {}
        if not execution.get("notification_id"):
            return None
        delivery = self.state.notification_deliveries.get(execution["notification_id"], {})
        attempts = delivery.get("attempts") or []
        if (
            delivery.get("status") != "sent" or not attempts
            or attempts[-1].get("id") != execution["id"]
        ):
            return self._reject("Notification completion requires its acknowledged delivery receipt.", phase_id, step_id)
        if self.projection_factory().scheduling().suspension:
            return self._reject("Notification evidence retained; finalization waits for release resume.", phase_id, step_id)
        return None

    def authorize_outcome(
        self, intent: TransitionIntent, phase_id: str, step_id: str,
        *, execution_id: str | None = None,
    ) -> OutcomePermit | TransitionResult:
        """Authorize an invocation; suspended recovery grants reads/evidence only."""
        if rejected := (self._invalid(phase_id, step_id)
                        if intent == TransitionIntent.RECOVER_EVIDENCE else self._invalid()):
            return rejected
        if self.projection_factory().workflow is not self.workflow:
            return self._reject("Workflow generation changed; invocation denied.", phase_id, step_id)
        if intent not in (
            TransitionIntent.EXECUTE, TransitionIntent.MOCK, TransitionIntent.EFFECT,
            TransitionIntent.WRITE, TransitionIntent.RECOVER_EVIDENCE,
            TransitionIntent.PREPARE, TransitionIntent.POLL,
            TransitionIntent.REFRESH, TransitionIntent.RECORD,
        ):
            return self._reject("Unsupported outcome invocation intent.", phase_id, step_id)
        if intent == TransitionIntent.RECOVER_EVIDENCE:
            step = self.workflow.step(phase_id, step_id)
            if not step or step.kind != StepKind.AUTO or not step.effect_mode:
                return self._reject("Evidence recovery requires an auto effect.", phase_id, step_id)
            record = self.state.get_step(phase_id, step_id)
            if (record.execution or {}).get("id") != execution_id:
                return self._reject("Evidence recovery requires the exact execution.", phase_id, step_id)
        elif intent in (TransitionIntent.EFFECT, TransitionIntent.WRITE):
            rejected = self._owned_outcome_check(phase_id, step_id, execution_id)
            if rejected:
                return rejected
            step = self.workflow.step(phase_id, step_id)
            if intent == TransitionIntent.EFFECT and step.kind != StepKind.AUTO:
                return self._reject("Only auto effects accept effect invocation permits.", phase_id, step_id)
            if intent == TransitionIntent.WRITE and step.kind != StepKind.EXTERNAL:
                return self._reject("Only external steps accept write invocation permits.", phase_id, step_id)
            selection = self.projection_factory().scheduling()
            if selection.suspension or not selection.phase(phase_id).due:
                return self._reject("Provider invocation is suspended or its phase is not due.", phase_id, step_id)
            if intent == TransitionIntent.WRITE and (
                not selection.step(phase_id, step_id).time_ready
                or self.state.get_step(phase_id, step_id).status != "running"
            ):
                return self._reject("External write execution is not active or its time has not arrived.", phase_id, step_id)
        else:
            check = self.eligibility().evaluate(intent, phase_id, step_id)
            if not check.allowed:
                return self._reject(check.reason, phase_id, step_id)
            record = self.state.get_step(phase_id, step_id)
            if record.execution or execution_id:
                if intent != TransitionIntent.POLL:
                    return self._reject("Only the owning execution can record this result.", phase_id, step_id)
                rejected = self._owned_outcome_check(phase_id, step_id, execution_id)
                if rejected:
                    return rejected
            if intent == TransitionIntent.RECORD and check.step.write_command:
                return self._reject(
                    f"{phase_id}/{step_id} requires an active {check.step.write_command} reservation.",
                    phase_id, step_id,
                )
        record = self.state.get_step(phase_id, step_id)
        definition = self.workflow.step(phase_id, step_id)
        if (intent != TransitionIntent.RECOVER_EVIDENCE
                and (intent == TransitionIntent.REFRESH or (record.execution or {}).get("refresh"))
                and definition.refresh_invalidation == "always"):
            if rejected := self._invalidation_check(self._dependent_steps(phase_id, step_id)):
                return rejected
        permit = OutcomePermit(phase_id, step_id, intent)
        self._outcome_permits[permit] = self._generation(self.state.get_step(phase_id, step_id))
        return permit

    def apply_outcome(
        self, permit: OutcomePermit, outcome: AutoOutcome, *,
        data: dict | None = None, block_holds: bool = True,
    ) -> TransitionResult:
        """Settle an issued invocation once, including when suspension began during it."""
        if rejected := self.validate_outcome_application(permit, outcome, data=data):
            return rejected
        pid, sid = permit.phase, permit.step
        result = self._apply_canonical(
            pid, sid, outcome, data=data, block_holds=block_holds,
            refresh=permit.intent == TransitionIntent.REFRESH,
        )
        if result.changed:
            self._outcome_permits = {
                key: value for key, value in self._outcome_permits.items()
                if (key.phase, key.step) != (pid, sid)
            }
        return result

    def validate_outcome_application(
        self, permit: OutcomePermit, outcome: AutoOutcome, *, data: dict | None = None,
    ) -> TransitionResult | None:
        """Preflight lifecycle and invalidation before any outcome evidence is applied."""
        outcome = require_auto_outcome(outcome)
        if data is not None and not isinstance(data, dict):
            raise TypeError("Outcome data must be a dictionary")
        if not isinstance(permit, OutcomePermit) or permit not in self._outcome_permits:
            return self._reject("Outcome permit is unknown, stale, or already consumed.")
        pid, sid = permit.phase, permit.step
        if rejected := self._invalid():
            return rejected
        projection = self.projection_factory()
        if projection.workflow is not self.workflow:
            return self._reject("Workflow generation changed; result was not applied.", pid, sid)
        previous = self.state.get_step(pid, sid)
        if self._outcome_permits[permit] != self._generation(previous):
            return self._reject("Outcome execution/generation changed; result was not applied.", pid, sid)
        definition = self.workflow.step(pid, sid)
        if not definition or definition.is_gate:
            return self._reject("Generic outcomes cannot complete approval gates.", pid, sid)
        if definition.kind != StepKind.EXTERNAL and permit.intent not in (
            TransitionIntent.EXECUTE, TransitionIntent.MOCK, TransitionIntent.EFFECT,
        ):
            return self._reject("Human actions require done/skip, not generic outcomes.", pid, sid)
        if (
            permit.intent == TransitionIntent.EXECUTE
            and definition.effect_mode and definition.effect_mode.writes_external_state
            and not isinstance(outcome, Blocked)
        ):
            return self._reject("Effect outcomes require a reserved execution before application.", pid, sid)
        selection = projection.scheduling()
        if (
            not selection.frontier or selection.frontier.id != pid
            or not selection.step(pid, sid).prerequisites_met
        ):
            return self._reject("Outcome no longer belongs to the ready prerequisite frontier.", pid, sid)
        if previous.execution:
            rejected = self._owned_outcome_check(pid, sid, previous.execution["id"])
            if rejected:
                return rejected
            rejected = self._notification_outcome_check(pid, sid)
            if rejected:
                return rejected
        return self._outcome_invalidation_check(
            pid, sid,
            status="done" if isinstance(outcome, Done) else (
                "blocked" if isinstance(outcome, Blocked) else "in_flight"),
            refresh=permit.intent == TransitionIntent.REFRESH,
        )

    def validate_evidence_permit(self, permit: OutcomePermit) -> TransitionResult | None:
        """Validate an existing invocation's evidence, including receipts during suspension."""
        if not isinstance(permit, OutcomePermit) or permit not in self._outcome_permits:
            return self._reject("Outcome permit is unknown, stale, or already consumed.")
        if rejected := (self._invalid(permit.phase, permit.step)
                        if permit.intent == TransitionIntent.RECOVER_EVIDENCE else self._invalid()):
            return rejected
        if self.projection_factory().workflow is not self.workflow:
            return self._reject("Workflow generation changed; invocation denied.", permit.phase, permit.step)
        record = self.state.get_step(permit.phase, permit.step)
        if self._outcome_permits[permit] != self._generation(record):
            return self._reject("Outcome execution/generation changed; invocation denied.", permit.phase, permit.step)
        return None

    def validate_outcome_permit(self, permit: OutcomePermit) -> TransitionResult | None:
        """Recheck invocation permission immediately before another provider call."""
        if rejected := self.validate_evidence_permit(permit):
            return rejected
        record = self.state.get_step(permit.phase, permit.step)
        if (permit.intent == TransitionIntent.WRITE and record.status == "in_flight"
                and (record.execution or {}).get("write_review")):
            if rejected := self._owned_outcome_check(
                    permit.phase, permit.step, record.execution["id"]):
                return rejected
            selection = self.projection_factory().scheduling()
            if (selection.suspension or not selection.phase(permit.phase).due
                    or not selection.step(permit.phase, permit.step).time_ready):
                return self._reject("Reviewed provider invocation is suspended.", permit.phase, permit.step)
            return None
        check = self.authorize_outcome(
            permit.intent, permit.phase, permit.step,
            execution_id=(record.execution or {}).get("id"),
        )
        if isinstance(check, TransitionResult):
            return check
        del self._outcome_permits[check]
        return None

    def settle_execution(
        self, phase_id: str, step_id: str, execution_id: str, outcome: AutoOutcome,
        *, data: dict | None = None,
    ) -> TransitionResult:
        """Record an exact owner's receipt, never authorize another provider invocation."""
        outcome = require_auto_outcome(outcome)
        if data is not None and not isinstance(data, dict):
            raise TypeError("Outcome data must be a dictionary")
        rejected = self._owned_outcome_check(phase_id, step_id, execution_id)
        if rejected:
            return rejected
        rejected = self._notification_outcome_check(phase_id, step_id)
        if rejected:
            return rejected
        result = self._apply_canonical(phase_id, step_id, outcome, data=data)
        if result.changed:
            self._outcome_permits = {
                key: value for key, value in self._outcome_permits.items()
                if (key.phase, key.step) != (phase_id, step_id)
            }
        return result

    def omit_execution(
        self, permit: OutcomePermit, reason: str, *, links: list | None = None,
    ) -> TransitionResult:
        """Apply an explicitly configured poller's omission only while still eligible."""
        if links is not None and not valid_links(links):
            raise TypeError("Omission links must be a list")
        links = deepcopy(links)
        rejected = self.validate_outcome_permit(permit)
        if rejected:
            return rejected
        if permit.intent != TransitionIntent.POLL:
            return self._reject("Automated omission requires an owned poll permit.")
        pid, sid = permit.phase, permit.step
        execution = self.state.get_step(pid, sid).execution or {}
        if not execution:
            return self._reject("Automated omission requires an exact execution owner.", pid, sid)
        result = self.skip(pid, sid, reason, execution_id=execution["id"])
        if result.changed:
            self.annotate_step(pid, sid, links=links, by="scout")
            self._outcome_permits = {
                key: value for key, value in self._outcome_permits.items()
                if (key.phase, key.step) != (pid, sid)
            }
        return result

    def _apply_canonical(
        self, phase_id: str, step_id: str, outcome: AutoOutcome, *,
        data: dict | None = None, block_holds: bool = True, refresh: bool = False,
    ) -> TransitionResult:
        return self._apply_outcome(
            phase_id, step_id,
            status="done" if isinstance(outcome, Done) else (
                "blocked" if isinstance(outcome, Blocked) else "in_flight"),
            note=outcome.reason if isinstance(outcome, Blocked) else outcome.note,
            by=outcome.by if isinstance(outcome, (Done, Blocked)) else "agent",
            links=outcome.links, data=data,
            poll_in_min=outcome.poll_in_min if isinstance(outcome, InProgress) else 30,
            block_holds=block_holds, refresh=refresh,
            preserve_execution=bool(
                (self.state.get_step(phase_id, step_id).execution or {}).get("effect_mode")
                or (self.state.get_step(phase_id, step_id).execution or {}).get("write_review")),
        )

    def eligibility(self) -> EligibilityEvaluator:
        return EligibilityEvaluator(
            self.state, self.workflow, self.projection_factory()
        )

    def skip(
        self,
        phase_id: str,
        step_id: str,
        reason: str,
        execution_id: str = None,
    ) -> TransitionResult:
        if rejected := self._invalid():
            return rejected
        if not reason or not reason.strip():
            return self._reject("A reason is required to skip a step.")
        previous = self.state.get_step(phase_id, step_id)
        if (previous.execution or {}).get("effect_mode"):
            return self._reject(
                "Engine-owned effect execution cannot be cleared by skip. "
                "Continue reconciliation or use verified retry recovery.",
                phase_id,
                step_id,
            )
        if previous.execution:
            definition = self.workflow.step(phase_id, step_id)
            if not definition or definition.is_gate:
                return self._reject(
                    f"Cannot skip {phase_id}/{step_id}: gate steps require approve or deny.",
                    phase_id,
                    step_id,
                )
            if not execution_id or previous.execution.get("id") != execution_id:
                return self._reject(
                    "Only the owning execution can terminate reserved work.",
                    phase_id,
                    step_id,
                )
        else:
            check = self.eligibility().evaluate(
                TransitionIntent.SKIP, phase_id, step_id
            )
            if not check.allowed:
                return self._reject(check.reason, phase_id, step_id)
        if rejected := self._outcome_invalidation_check(phase_id, step_id, status="skipped"):
            return rejected
        if self._refresh_invalidates(phase_id, step_id, status="skipped"):
            result = self.invalidate_dependents(
                phase_id, step_id, reason=f"Refreshed result for {phase_id}.{step_id}.")
            if result.kind != "invalidated":
                return result
        completed_data = deepcopy(previous.data)
        self._bind_notification_identity(completed_data, previous.execution)
        self._close_write_review(completed_data, previous.execution)
        self.state.set_step(
            phase_id,
            step_id,
            StepState(
                status="skipped",
                completed_at=self.now(),
                invalidated_at=None,
                invalidation_reason=None,
                note=f"Skipped: {reason.strip()}",
                by="human",
                links=previous.links,
                data=completed_data,
            ),
        )
        self._clear_pending(phase_id, step_id)
        return self._accept(
            "ran",
            f"Skipped {phase_id}/{step_id} — {reason.strip()}",
            phase_id,
            step_id,
        )

    def complete(
        self, phase_id: str, step_id: str, note: str
    ) -> TransitionResult:
        check = self.eligibility().evaluate(
            TransitionIntent.COMPLETE, phase_id, step_id
        )
        if not check.allowed:
            return self._reject(check.reason, phase_id, step_id)
        if check.execution and not note.strip():
            return self._reject(
                "Reserved execution needs owner-reviewed evidence in --note.",
                phase_id,
                step_id,
            )
        previous = self.state.get_step(phase_id, step_id)
        if rejected := self._outcome_invalidation_check(phase_id, step_id, status="done"):
            return rejected
        if self._refresh_invalidates(phase_id, step_id, status="done"):
            result = self.invalidate_dependents(
                phase_id, step_id, reason=f"Refreshed result for {phase_id}.{step_id}.")
            if result.kind != "invalidated":
                return result
        completed_data = deepcopy(previous.data)
        self._bind_notification_identity(completed_data, previous.execution)
        self._close_write_review(completed_data, previous.execution)
        self.state.set_step(
            phase_id,
            step_id,
            StepState(
                status="done",
                completed_at=self.now(),
                invalidated_at=None,
                invalidation_reason=None,
                note=note.strip() or "Marked done",
                by="human",
                links=previous.links,
                data=completed_data,
            ),
        )
        self._clear_pending(phase_id, step_id)
        tail = f" — {note.strip()}" if note and note.strip() else ""
        return self._accept(
            "ran", f"Done: {phase_id}/{step_id}{tail}", phase_id, step_id
        )

    def reopen(
        self, phase_id: str, step_id: str, reason: str = ""
    ) -> TransitionResult:
        check = self.eligibility().evaluate(
            TransitionIntent.REOPEN, phase_id, step_id
        )
        if not check.allowed:
            return self._reject(check.reason, phase_id, step_id)
        if check.execution and check.execution.get("approval"):
            return self._reject("External gate ownership cannot be cleared by reopen; reconcile its exact approval.",
                                phase_id, step_id)
        if check.execution and check.execution.get("effect_mode"):
            return self._reject(
                "Engine-owned effect execution cannot be cleared by reopen. "
                "Continue reconciliation or use verified retry recovery.",
                phase_id,
                step_id,
            )
        if (check.execution or self.state.get_step(phase_id, step_id).data.get("last_write_review")) and not reason.strip():
            return self._reject(
                "Reopening owned or previously reviewed work requires owner-reviewed evidence in --reason.",
                phase_id,
                step_id,
            )
        affected = self.workflow.invalidation_closure(phase_id, step_id)
        rejected = self._invalidate_steps(
            affected,
            reason=f"Reopened from {phase_id}.{step_id}: {reason or 'owner request'}",
            force_clear={f"{phase_id}.{step_id}"},
        )
        if rejected:
            return rejected
        tail = f" — {reason.strip()}" if reason and reason.strip() else ""
        return TransitionResult(
            "ran",
            f"Reopened {phase_id}/{step_id}{tail}; invalidated "
            f"{len(affected)} dependent step(s).",
            phase_id,
            step_id,
            True,
            tuple(step.key for step in affected),
        )

    def reserve(
        self, phase_id: str, step_id: str, executor: str, *, write_review: dict | None = None,
    ) -> TransitionResult:
        if rejected := self._invalid():
            return rejected
        record = self.state.get_step(phase_id, step_id)
        was_complete = bool(
            (step := self.workflow.step(phase_id, step_id))
            and self.projection_factory().step_complete(step)
        ) or bool(record.invalidated_at)
        check = self.eligibility().evaluate(
            TransitionIntent.RESERVE, phase_id, step_id
        )
        if not check.allowed:
            return self._reject(check.reason, phase_id, step_id)
        if step.write_command:
            from orchestrator.revision import is_hash
            if (not isinstance(write_review, dict) or set(write_review) != {"hash", "approved_by"}
                    or not is_hash(write_review["hash"])
                    or not isinstance(write_review["approved_by"], str)
                    or not write_review["approved_by"].strip()):
                return self._reject(
                    f"Use {step.write_command} with an exact --review-hash and --approved-by; "
                    "generic reservations do not authorize provider writes.", phase_id, step_id)
        elif write_review is not None:
            return self._reject("This step has no reviewed write capability.", phase_id, step_id)
        if not executor or not executor.strip():
            return self._reject(
                "The claiming executor/session identifier is required",
                phase_id,
                step_id,
            )
        if was_complete and step.refresh_invalidation == "always":
            if rejected := self._invalidation_check(self._dependent_steps(phase_id, step_id)):
                return rejected
        previous_status = record.status
        record.status = "running"
        record.execution = {
            "id": uuid.uuid4().hex,
            "owner": executor.strip(),
            "started_at": self.now(),
            "refresh": was_complete,
            "previous_status": previous_status if was_complete else None,
        }
        if write_review is not None:
            record.execution["write_review"] = deepcopy(write_review)
        self.state.set_step(phase_id, step_id, record)
        return self._accept(
            "reserved",
            f"Reserved {phase_id}/{step_id}.",
            phase_id,
            step_id,
        )

    def begin_reviewed_write(self, phase_id: str, step_id: str, execution_id: str):
        """Fence an attempt before mutation; only this process receives its live permit."""
        permit = self.authorize_outcome(
            TransitionIntent.WRITE, phase_id, step_id, execution_id=execution_id)
        if isinstance(permit, TransitionResult):
            return permit
        record = self.state.get_step(phase_id, step_id)
        if not (record.execution or {}).get("write_review"):
            self._outcome_permits.pop(permit, None)
            return self._reject("Provider write requires a reviewed reservation.", phase_id, step_id)
        record.status = "in_flight"
        record.data["in_flight_since"] = self.now()
        self.state.set_step(phase_id, step_id, record)
        self._outcome_permits[permit] = self._generation(record)
        return permit

    def begin_effect(
        self,
        permit: OutcomePermit,
        *,
        effect_mode: EffectMode,
        operation_key: str,
        effect_input: dict,
    ) -> TransitionResult:
        """Persist ownership before an in-process auto handler may write externally."""
        rejected = self.validate_outcome_permit(permit)
        if rejected:
            return rejected
        phase_id, step_id = permit.phase, permit.step
        if permit.intent != TransitionIntent.EXECUTE:
            return self._reject("Effect start requires an execute permit.", phase_id, step_id)
        definition = self.workflow.step(phase_id, step_id)
        if (
            not definition
            or definition.kind != StepKind.AUTO
            or definition.effect_mode != effect_mode
            or not effect_mode.writes_external_state
        ):
            return self._reject(
                f"{phase_id}/{step_id} is not a configured effectful auto step.",
                phase_id,
                step_id,
            )
        check = self.eligibility().evaluate(
            TransitionIntent.EXECUTE, phase_id, step_id
        )
        if not check.allowed:
            return self._reject(check.reason, phase_id, step_id)
        if check.execution:
            return self._reject(
                f"Effect is already owned by execution {check.execution.get('id')}.",
                phase_id,
                step_id,
            )
        if not operation_key or not operation_key.strip():
            return self._reject(
                "Effect operation key is required.", phase_id, step_id
            )
        record = self.state.get_step(phase_id, step_id)
        record.status = "running"
        record.execution = {
            "id": uuid.uuid4().hex,
            "owner": "engine",
            "started_at": self.now(),
            "refresh": False,
            "effect_mode": effect_mode.value,
            "effect_recovery": definition.effect_recovery.value,
            "operation_key": operation_key.strip(),
            "effect_input": deepcopy(effect_input),
        }
        self.state.set_step(phase_id, step_id, record)
        self._outcome_permits.pop(permit, None)
        return self._accept(
            "reserved",
            f"Reserved effect {phase_id}/{step_id}.",
            phase_id,
            step_id,
        )

    def _claim_notification_step(self, notification_id, approved_hash, executor):
        from orchestrator.delivery import validate_record_state

        ledger = deepcopy(self.state.notification_deliveries.get(notification_id))
        item = validate_record_state(self.state, ledger)
        if item["id"] != notification_id or item["hash"] != approved_hash:
            return self._reject("Notification identity/approved hash does not match.")
        if ledger["status"] not in ("prepared", "not_sent") or item["completion"].get("kind") != "step":
            return self._reject("A step notification requires an unclaimed prepared snapshot.")
        pid, sid = item["scope"].get("phase"), item["scope"].get("step")
        result = self.reserve(pid, sid, executor)
        if not result.changed:
            return result
        record = self.state.get_step(pid, sid)
        record.execution["notification_id"] = notification_id
        ledger["status"] = "claimed"
        ledger["attempts"].append({**deepcopy(record.execution), "status": "claimed", "hash": approved_hash})
        self.state.set_step(pid, sid, record)
        self.state.notification_deliveries[notification_id] = ledger
        return result

    def release_notification_step(self, notification_id, execution_id):
        """A receipt is independent evidence; only its exact current owner is releasable."""
        from orchestrator.delivery import validate_record_state

        ledger = self.state.notification_deliveries.get(notification_id)
        item = validate_record_state(self.state, ledger)
        if item["id"] != notification_id or item["completion"].get("kind") != "step":
            return self._reject("Notification is not a bound step delivery.")
        attempt = ledger["attempts"][-1] if ledger["attempts"] else {}
        if (ledger["status"] != "not_sent" or attempt.get("id") != execution_id
                or not isinstance(attempt.get("evidence"), str) or not attempt["evidence"].strip()):
            return self._reject("Release requires the exact proven not_sent transport receipt.")
        pid, sid = item["scope"].get("phase"), item["scope"].get("step")
        if not self.workflow.step(pid, sid):
            return self._reject("Notification owning step no longer exists.")
        if rejected := self._invalid(pid, sid):
            return rejected
        record = self.state.get_step(pid, sid)
        execution = record.execution or {}
        if (record.status != "running" or execution.get("id") != execution_id
                or execution.get("owner") != attempt.get("owner")
                or execution.get("notification_id") != notification_id
                or item["scope"].get("generation", "initial") != (record.invalidated_at or "initial")):
            return self._reject("Notification execution/generation changed; current ownership preserved.", pid, sid)
        record.status = "pending"
        record.execution = None
        self.state.set_step(pid, sid, record)
        return self._accept("released", "Proven not_sent notification owner released.", pid, sid)

    def record_notification_evidence(self, notification_id):
        from orchestrator.delivery import require_receipt, validate_record_state

        ledger = self.state.notification_deliveries.get(notification_id)
        item = validate_record_state(self.state, ledger)
        if item["id"] != notification_id or ledger["status"] != "sent":
            return self._reject("Notification evidence requires its confirmed sent receipt.")
        require_receipt(ledger)
        if self.projection_factory().scheduling().suspension:
            return self._reject("Notification evidence finalization waits for release resume.")
        completion = item["completion"]
        field = completion.get("release_field")
        checkpoint = completion.get("checkpoint")
        if field and getattr(self.state, field) is not None and not isinstance(getattr(self.state, field), str):
            return self._reject("Notification evidence date must be text or null.")
        if checkpoint and not isinstance(self.state.escalation_checkpoints, dict):
            return self._reject("Notification checkpoints must be a mapping.")
        changed = False
        if field and (getattr(self.state, field) or "") < completion["date"]:
            setattr(self.state, field, completion["date"])
            changed = True
        if checkpoint and checkpoint not in self.state.escalation_checkpoints:
            self.state.escalation_checkpoints[checkpoint] = {
                "sent_at": ledger["attempts"][-1]["acknowledged_at"],
                "target": deepcopy(item["target"]),
            }
            changed = True
        return TransitionResult("annotated", "Confirmed notification evidence recorded.", changed=changed)

    def _prepare_effect_recovery(self, phase_id, step_id, execution_id, reason, *, retry):
        if rejected := self._invalid(phase_id, step_id):
            return rejected
        definition = self.workflow.step(phase_id, step_id)
        if not definition or definition.kind != StepKind.AUTO or (
            (definition.effect_mode != EffectMode.TRANSACTIONAL or not definition.effect_retry) if retry
            else (definition.effect_mode != EffectMode.IDEMPOTENT or definition.effect_recovery != EffectRecovery.MATCH_CURRENT)
        ):
            return self._reject("Step does not support this effect recovery policy.", phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        if record.status != "blocked" or not isinstance(execution_id, str) or not execution_id or (record.execution or {}).get("id") != execution_id:
            return self._reject("Recovery requires the exact blocked execution.", phase_id, step_id)
        if not isinstance(reason, str) or not reason.strip():
            return self._reject("Effect recovery requires owner-reviewed evidence.", phase_id, step_id)
        return self._generation(record)

    def _apply_effect_recovery(self, phase_id, step_id, execution_id, reason, generation, *, retry):
        current = self._prepare_effect_recovery(phase_id, step_id, execution_id, reason, retry=retry)
        if isinstance(current, TransitionResult):
            return current
        if current != generation or self.projection_factory().workflow is not self.workflow:
            return self._reject("Effect execution/generation changed during recovery.", phase_id, step_id)
        return (self._authorize_effect_retry if retry else self._supersede_effect)(
            phase_id, step_id, execution_id, reason)

    def _supersede_effect(
        self, phase_id: str, step_id: str, execution_id: str, reason: str
    ) -> TransitionResult:
        """Replace an idempotent desired-state operation with newly prepared input."""
        definition = self.workflow.step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        execution = record.execution or {}
        if (
            not definition
            or definition.effect_mode != EffectMode.IDEMPOTENT
            or definition.effect_recovery != EffectRecovery.MATCH_CURRENT
        ):
            return self._reject(
                f"{phase_id}/{step_id} does not support idempotent supersede.",
                phase_id,
                step_id,
            )
        if (
            record.status != "blocked"
            or execution.get("id") != execution_id
            or execution.get("effect_mode") != EffectMode.IDEMPOTENT.value
        ):
            return self._reject(
                "Supersede requires the exact blocked idempotent execution.",
                phase_id,
                step_id,
            )
        if not reason or not reason.strip():
            return self._reject(
                "Idempotent supersede requires owner-reviewed evidence.",
                phase_id,
                step_id,
            )
        record.status = "pending"
        record.execution = None
        record.note = f"Idempotent operation superseded: {reason.strip()}"
        record.by = "human"
        self.state.set_step(phase_id, step_id, record)
        return self._accept(
            "ran",
            f"Superseded idempotent effect {phase_id}/{step_id}.",
            phase_id,
            step_id,
        )

    def _authorize_effect_retry(
        self, phase_id: str, step_id: str, execution_id: str, reason: str
    ) -> TransitionResult:
        """Clear an effect owner only after its handler verified no effect exists."""
        definition = self.workflow.step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        execution = record.execution or {}
        if (
            not definition
            or definition.effect_mode != EffectMode.TRANSACTIONAL
            or not definition.effect_retry
        ):
            return self._reject(
                f"{phase_id}/{step_id} does not support verified effect retry.",
                phase_id,
                step_id,
            )
        if (
            record.status != "blocked"
            or execution.get("id") != execution_id
            or execution.get("effect_mode") != EffectMode.TRANSACTIONAL.value
        ):
            return self._reject(
                "Effect retry requires the exact blocked transactional execution.",
                phase_id,
                step_id,
            )
        if not reason or not reason.strip():
            return self._reject(
                "Verified effect retry requires owner-reviewed evidence.",
                phase_id,
                step_id,
            )
        record.status = "pending"
        record.execution = None
        record.note = f"Verified absent; retry authorized: {reason.strip()}"
        record.by = "human"
        self.state.set_step(phase_id, step_id, record)
        return self._accept(
            "ran",
            f"Verified retry authorized for {phase_id}/{step_id}.",
            phase_id,
            step_id,
        )

    def hold_effect(
        self, phase_id: str, step_id: str, reason: str
    ) -> TransitionResult:
        """Preserve an uncertain effect owner while blocking automatic replay."""
        definition = self.workflow.step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        if (
            not definition
            or definition.kind != StepKind.AUTO
            or not definition.effect_mode
            or not definition.effect_mode.writes_external_state
            or not record.execution
        ):
            return self._reject(
                f"No active auto effect for {phase_id}/{step_id}.",
                phase_id,
                step_id,
            )
        record.status = "blocked"
        record.note = reason
        record.by = "agent"
        self.state.set_step(phase_id, step_id, record)
        return self._accept(
            "reminder",
            f"ACTION NEEDED - {definition.name}: {reason}",
            phase_id,
            step_id,
        )

    def validate_approval_start(self, phase_id, step_id):
        if rejected := self._invalid():
            return rejected
        selection = self.projection_factory().scheduling()
        step = self.workflow.step(phase_id, step_id)
        hold = selection.focus_hold
        if (not step or not step.is_gate or not step.approval_command
                or selection.status != "holding_gate" or not hold or hold.kind != "gate"
                or (hold.phase_id, hold.step_id) != (phase_id, step_id)
                or self.state.get_step(phase_id, step_id).execution):
            return self._reject("External approval requires the current eligible, unowned gate.", phase_id, step_id)
        return None

    def validate_approval_owner(self, phase_id, step_id, execution_id, *, active=False):
        if rejected := self._invalid(phase_id, step_id):
            return rejected
        projection = self.projection_factory()
        step = self.workflow.step(phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        execution = record.execution or {}
        if (projection.workflow is not self.workflow or not step or not step.approval_command
                or not step.is_gate or not execution_id or execution.get("id") != execution_id
                or not execution.get("approval")):
            return self._reject("External gate requires its exact active approval execution.", phase_id, step_id)
        if active:
            if rejected := self._invalid():
                return rejected
            selection = projection.scheduling()
            ready = selection.step(phase_id, step_id)
            if (selection.suspension or not selection.frontier or selection.frontier.id != phase_id
                    or not selection.phase(phase_id).due or not ready.time_ready or not ready.prerequisites_met):
                return self._reject("Approval receipt/ownership retained; gate execution waits for eligible release state.",
                                    phase_id, step_id)
        return None

    def reserve_approval(self, phase_id, step_id, request, approved_hash, approved_by, executor):
        from .approvals import request_hash
        from .revision import revision_id

        if rejected := self.validate_approval_start(phase_id, step_id):
            return rejected
        if (not isinstance(request, ApprovalRequest) or not isinstance(approved_by, str)
                or not approved_by.strip() or not isinstance(executor, str) or not executor.strip()):
            return self._reject("Approval requires a frozen request, --approved-by and executor.", phase_id, step_id)
        key = f"{phase_id}.{step_id}"
        binding = revision_id(self.state.workflow_revision)
        if approved_hash != request_hash(self.state, key, request, binding):
            return self._reject("Approval review hash changed; review the current request.", phase_id, step_id)
        from dataclasses import asdict
        record = self.state.get_step(phase_id, step_id)
        record.status = "running"
        record.note = "Approval reserved; provider submission has not started."
        record.execution = {
            "id": uuid.uuid4().hex, "owner": executor.strip(), "started_at": self.now(),
            "approval": {
                "request": asdict(request), "request_hash": approved_hash,
                "workflow_revision": binding, "approved_by": approved_by.strip(),
                "submission_started_at": None, "receipt": None,
            },
        }
        self.state.set_step(phase_id, step_id, record)
        return self._accept("reserved", record.note, phase_id, step_id)

    def begin_approval_submission(self, phase_id, step_id, execution_id):
        if rejected := self.validate_approval_owner(phase_id, step_id, execution_id, active=True):
            return rejected
        record = self.state.get_step(phase_id, step_id)
        if record.status != "running" or record.execution["approval"]["submission_started_at"] is not None:
            return self._reject("Approval attempt already started; reconcile without resubmitting.", phase_id, step_id)
        record.status = "in_flight"
        record.execution["approval"]["submission_started_at"] = self.now()
        record.note = "Approval submission may be in flight; reconcile its frozen identity, never blindly resend."
        self.state.set_step(phase_id, step_id, record)
        permit = ApprovalPermit(phase_id, step_id, execution_id)
        self._approval_permits[permit] = self._generation(record)
        return permit

    def validate_approval_permit(self, permit):
        if not isinstance(permit, ApprovalPermit) or permit not in self._approval_permits:
            return self._reject("Approval submission permit is unknown or already consumed.")
        if rejected := self.validate_approval_owner(
                permit.phase, permit.step, permit.execution_id, active=True):
            return rejected
        if self._approval_permits[permit] != self._generation(self.state.get_step(permit.phase, permit.step)):
            return self._reject("Approval execution changed after authorization.", permit.phase, permit.step)
        return None

    def consume_approval_permit(self, permit):
        if rejected := self.validate_approval_permit(permit):
            raise ValueError(rejected.message)
        del self._approval_permits[permit]

    def discard_approval_permit(self, permit):
        self._approval_permits.pop(permit, None)

    def hold_approval(self, phase_id, step_id, execution_id, detail):
        if rejected := self.validate_approval_owner(phase_id, step_id, execution_id):
            return rejected
        record = self.state.get_step(phase_id, step_id)
        if record.execution["approval"]["submission_started_at"] is None:
            return self._reject("Unattempted approval cannot have an uncertain result.", phase_id, step_id)
        record.status = "blocked"
        record.note = f"Approval ownership retained; reconciliation required: {detail}"
        self.state.set_step(phase_id, step_id, record)
        return self._accept("blocked", record.note, phase_id, step_id)

    def record_approval_receipt(self, phase_id, step_id, execution_id):
        if rejected := self.validate_approval_owner(phase_id, step_id, execution_id):
            return rejected
        record = self.state.get_step(phase_id, step_id)
        approval = record.execution["approval"]
        if approval["submission_started_at"] is None or approval["receipt"] is not None:
            return self._reject("Receipt requires an attempted approval without a saved receipt.", phase_id, step_id)
        approval["receipt"] = {
            "approval_id": approval["request"]["approval_id"], "status": "approved", "observed_at": self.now(),
        }
        record.note = "Exact provider approval confirmed; local gate completion is pending."
        self.state.set_step(phase_id, step_id, record)
        return self._accept("receipt_recorded", record.note, phase_id, step_id)

    def finalize_approval(self, phase_id, step_id, execution_id):
        if rejected := self.validate_approval_owner(phase_id, step_id, execution_id, active=True):
            return rejected
        record = self.state.get_step(phase_id, step_id)
        approval = record.execution["approval"]
        if approval["receipt"] is None:
            return self._reject("Local gate completion requires the saved exact provider receipt.", phase_id, step_id)
        record.data["last_approval"] = {"execution_id": execution_id, **deepcopy(approval)}
        record.execution = None
        record.status, record.by, record.completed_at = "done", "human", self.now()
        request = approval["request"]
        record.note = f"Gate approved: ADO approval {request['approval_id']} on build {request['build_id']}."
        self.state.set_step(phase_id, step_id, record)
        self.state.gate_decisions.append(_gate_dict(GateDecision(
            step=f"{phase_id}.{step_id}", decision="approved", at=self.now(),
            by=approval["approved_by"], comment=request["comment"])))
        return self._accept("ran", record.note, phase_id, step_id)

    def approve_gate(self, comment: str = "") -> TransitionResult:
        if rejected := self._invalid():
            return rejected
        projection = self.projection_factory()
        hold = projection.current_hold()
        if (
            projection.release_status() != "holding_gate"
            or not hold
            or hold.kind != "gate"
            or not hold.step_id
        ):
            return self._reject("No gate is currently holding.")
        step = self.workflow.step(hold.phase_id, hold.step_id)
        if not step or not step.is_gate:
            return self._reject("Current hold is not a configured approval gate.")
        if step.approval_command:
            return self._reject(
                f"External gate requires {step.approval_command} and a durable provider receipt.",
                step.phase_id, step.id)
        self.state.gate_decisions.append(
            _gate_dict(
                GateDecision(
                    step=step.key,
                    decision="approved",
                    at=self.now(),
                    comment=comment,
                )
            )
        )
        self.state.set_step(
            step.phase_id,
            step.id,
            StepState(
                status="done",
                completed_at=self.now(),
                note=f"Gate approved. {comment}".strip(),
                by="human",
            ),
        )
        self._clear_pending(step.phase_id, step.id)
        return self._accept(
            "ran",
            f"Gate approved: {step.phase_id} → {step.id}. {comment}".strip(),
            step.phase_id,
            step.id,
        )

    def deny_gate(self, comment: str = "") -> TransitionResult:
        if rejected := self._invalid():
            return rejected
        projection = self.projection_factory()
        hold = projection.current_hold()
        if (
            projection.release_status() != "holding_gate"
            or not hold
            or hold.kind != "gate"
            or not hold.step_id
        ):
            return self._reject("No gate is currently holding.")
        step = self.workflow.step(hold.phase_id, hold.step_id)
        if not step or not step.is_gate:
            return self._reject("Current hold is not a configured approval gate.")
        self.state.gate_decisions.append(
            _gate_dict(
                GateDecision(
                    step=step.key,
                    decision="denied",
                    at=self.now(),
                    comment=comment,
                )
            )
        )
        self._clear_pending(step.phase_id, step.id)
        return self._accept(
            "gate",
            f"Gate DENIED: {step.phase_id} → {step.id}. "
            f"Release blocked. {comment}".strip(),
            step.phase_id,
            step.id,
        )

    def halt(self, reason: str) -> TransitionResult:
        if not reason or not reason.strip():
            return self._reject("A reason is required to halt the release.")
        if self.state.halt:
            return self._reject("Release is already halted.")
        self.state.halt = {"reason": reason.strip(), "at": self.now()}
        return self._accept(
            "halted", f"Release HALTED — {reason.strip()}"
        )

    def resume(self, reason: str = "") -> TransitionResult:
        if not self.state.halt:
            return self._reject("Release is not halted.")
        self.state.halt = None
        tail = f" — {reason.strip()}" if reason and reason.strip() else ""
        return self._accept("idle", f"Release resumed{tail}.")

    def cancel(self, reason: str) -> TransitionResult:
        if not reason or not reason.strip():
            return self._reject("A reason is required to cancel the release.")
        if self.state.cancellation:
            return TransitionResult(
                "cancelled", "Release is already cancelled.", changed=False)
        self.state.cancellation = {"reason": reason.strip(), "at": self.now()}
        return self._accept("cancelled", f"Release cancelled — {reason.strip()}")

    def reactivate(self, reason: str) -> TransitionResult:
        if not isinstance(reason, str) or not reason.strip():
            return self._reject("A reason is required to reactivate the release.")
        if not self.state.cancellation:
            return TransitionResult(
                "reactivated", "Release cancellation is already clear.", changed=False)
        self.state.cancellation = None
        return self._accept(
            "reactivated",
            f"Release cancellation cleared — {reason.strip()}",
        )

    def activate(self, phase_id: str) -> TransitionResult:
        if rejected := self._invalid():
            return rejected
        phase = self.workflow.phase(phase_id)
        if not phase:
            return self._reject(f"No such phase: {phase_id}")
        if not phase.conditional:
            return self._reject(f"Phase is not conditional: {phase_id}")
        if phase_id in self.state.active_conditionals:
            return self._reject(f"Conditional phase is already active: {phase_id}")
        affected = self.workflow.steps_from_phase(phase_id)
        rejected = self._invalidate_steps(
            affected,
            reason=f"Conditional phase {phase_id} activated.",
        )
        if rejected:
            return rejected
        self.state.active_conditionals.append(phase_id)
        return TransitionResult(
            "ran",
            f"Activated conditional phase: {phase_id}; invalidated "
            f"{len(affected)} step(s) from that phase onward.",
            changed=True,
            affected=tuple(step.key for step in affected),
        )

    def invalidate_dependents(
        self, phase_id: str, step_id: str, reason: str
    ) -> TransitionResult:
        if not self.workflow.step(phase_id, step_id):
            return self._reject(f"No such step: {phase_id}/{step_id}", phase_id, step_id)
        affected = self._dependent_steps(phase_id, step_id)
        if rejected := self._invalidate_steps(affected, reason=reason):
            return rejected
        return TransitionResult(
            "invalidated", f"Invalidated dependents of {phase_id}/{step_id}.",
            phase_id, step_id, bool(affected), tuple(step.key for step in affected))

    def _dependent_steps(self, phase_id: str, step_id: str) -> tuple[StepDefinition, ...]:
        return tuple(
            step
            for step in self.workflow.invalidation_closure(phase_id, step_id)
            if step.key != f"{phase_id}.{step_id}"
        )

    def _invalidation_check(self, steps: tuple[StepDefinition, ...]) -> TransitionResult | None:
        if rejected := self._invalid():
            return rejected
        protected = []
        for step in steps:
            record = self.state.get_step(step.phase_id, step.id)
            if (record.execution or {}).get("approval"):
                protected.append(f"{step.key} (approval execution {record.execution['id']})")
            if (step.kind == StepKind.AUTO and step.effect_mode
                    and step.effect_mode.writes_external_state and record.execution):
                protected.append(f"{step.key} (execution {record.execution['id']})")
        if protected:
            return self._reject(
                "Cannot invalidate active engine-owned effect(s) or approval(s): " + ", ".join(protected)
                + ". Settle their current execute/reconcile operations before changing upstream work; "
                "ownership cannot be force-cleared.")
        return None

    def _refresh_invalidates(self, phase_id: str, step_id: str, *, status: str, refresh: bool = False) -> bool:
        previous = self.state.get_step(phase_id, step_id)
        definition = self.workflow.step(phase_id, step_id)
        if not definition or not (refresh or (previous.execution or {}).get("refresh")):
            return False
        baseline = (previous.execution or {}).get("previous_status") or previous.status
        return (definition.refresh_invalidation == "always"
                or definition.refresh_invalidation == "status" and baseline != status)

    def _outcome_invalidation_check(
        self, phase_id: str, step_id: str, *, status: str, refresh: bool = False,
    ) -> TransitionResult | None:
        previous = self.state.get_step(phase_id, step_id)
        if refresh or (previous.execution or {}).get("refresh"):
            projection = self.projection_factory()
            frontier = projection.frontier_phase()
            if projection.release_status() == "complete" or not frontier or frontier.id != phase_id:
                return self._reject(
                    "Completed work may be refreshed only while its owning phase is the current frontier.",
                    phase_id, step_id)
        if self._refresh_invalidates(phase_id, step_id, status=status, refresh=refresh):
            return self._invalidation_check(self._dependent_steps(phase_id, step_id))
        return None

    def _apply_outcome(
        self,
        phase_id: str,
        step_id: str,
        *,
        status: str,
        note: str,
        by: str,
        links: Optional[list] = None,
        data: Optional[dict] = None,
        poll_in_min: int = 30,
        block_holds: bool = True,
        refresh: bool = False,
        preserve_execution: bool = False,
    ) -> TransitionResult:
        if status not in ("done", "blocked", "in_flight"):
            return self._reject(f"Unsupported step outcome status: {status}")
        links = deepcopy(links)
        data = deepcopy(data)
        previous = self.state.get_step(phase_id, step_id)
        if rejected := self._outcome_invalidation_check(phase_id, step_id, status=status, refresh=refresh):
            return rejected
        if self._refresh_invalidates(phase_id, step_id, status=status, refresh=refresh):
            result = self.invalidate_dependents(
                phase_id, step_id, reason=f"Refreshed result for {phase_id}.{step_id}.")
            if result.kind != "invalidated":
                return result
        stored_data = deepcopy(data if data is not None else previous.data)
        if "last_write_review" in previous.data:
            stored_data["last_write_review"] = deepcopy(previous.data["last_write_review"])
        else:
            stored_data.pop("last_write_review", None)
        reviewed = bool((previous.execution or {}).get("write_review"))
        if reviewed and status != "done":
            preserve_execution = True
            if "in_flight_since" in previous.data:
                stored_data["in_flight_since"] = previous.data["in_flight_since"]
        if status == "done" or (status == "blocked" and not preserve_execution):
            self._close_write_review(stored_data, previous.execution)
        if status == "done":
            self._bind_notification_identity(stored_data, previous.execution)
        if status == "in_flight":
            stored_data.setdefault("in_flight_since", self.now())
            stored_data["poll_in_min"] = poll_in_min
            self._clear_pending(phase_id, step_id)
            self.state.set_step(
                phase_id,
                step_id,
                StepState(
                    status="in_flight",
                    completed_at=previous.completed_at if reviewed else None,
                    invalidated_at=previous.invalidated_at if reviewed else None,
                    invalidation_reason=previous.invalidation_reason if reviewed else None,
                    note=note,
                    by=by,
                    links=list(links or []),
                    execution=deepcopy(previous.execution),
                    data=stored_data,
                ),
            )
            return self._accept(
                "waiting",
                f"WAITING — {self.workflow.step(phase_id, step_id).name}: {note}",
                phase_id,
                step_id,
            )
        self.state.set_step(
            phase_id,
            step_id,
            StepState(
                status=status,
                completed_at=self.now() if status == "done" else (previous.completed_at if reviewed else None),
                invalidated_at=previous.invalidated_at if reviewed and status == "blocked" else None,
                invalidation_reason=previous.invalidation_reason if reviewed and status == "blocked" else None,
                note=note,
                by=by,
                links=list(links or []),
                execution=(
                    deepcopy(previous.execution)
                    if status == "blocked" and preserve_execution
                    else None
                ),
                data=stored_data,
            ),
        )
        if status == "done":
            record = self.state.get_step(phase_id, step_id)
            record.execution = None
            self.state.set_step(phase_id, step_id, record)
            self._clear_pending(phase_id, step_id)
            return self._accept("ran", note, phase_id, step_id)
        self._add_pending(phase_id, step_id)
        if block_holds:
            return self._accept(
                "reminder",
                f"ACTION NEEDED — {self.workflow.step(phase_id, step_id).name}: {note}",
                phase_id,
                step_id,
            )
        return self._accept(
            "ran",
            f"BLOCKED — {self.workflow.step(phase_id, step_id).name}: {note}",
            phase_id,
            step_id,
        )

    def annotate_step(
        self,
        phase_id: str,
        step_id: str,
        *,
        data: Optional[dict] = None,
        links: Optional[list] = None,
        note: Optional[str] = None,
        by: Optional[str] = None,
    ) -> TransitionResult:
        """Update non-lifecycle evidence without changing the step status."""
        step = self.workflow.step(phase_id, step_id)
        if not step:
            return self._reject(f"No such step: {phase_id}/{step_id}")
        if rejected := self._invalid(phase_id, step_id):
            return rejected
        if (data is not None and not isinstance(data, dict)
                or links is not None and not valid_links(links)
                or note is not None and not isinstance(note, str)
                or by is not None and not isinstance(by, str)):
            return self._reject("Evidence requires mapping data, list links and text note/by.", phase_id, step_id)
        record = self.state.get_step(phase_id, step_id)
        before = deepcopy(record)
        if data and "last_write_review" in data and (
                "last_write_review" not in record.data
                or data["last_write_review"] != record.data["last_write_review"]):
            return self._reject("Closed write authorization is engine-owned evidence.", phase_id, step_id)
        if data and "last_approval" in data and (
                "last_approval" not in record.data or data["last_approval"] != record.data["last_approval"]):
            return self._reject("Closed approval receipt is engine-owned evidence.", phase_id, step_id)
        if data:
            record.data.update(deepcopy(data))
        if links is not None:
            record.links = list(links)
        if note is not None:
            record.note = note
        if by is not None:
            record.by = by
        if record == before:
            return self._reject(f"Evidence is unchanged for {phase_id}/{step_id}.", phase_id, step_id)
        self.state.set_step(phase_id, step_id, record)
        return self._accept(
            "annotated",
            f"Updated evidence for {phase_id}/{step_id}.",
            phase_id,
            step_id,
        )

    def _clear_pending(self, phase_id: str, step_id: str) -> None:
        """Pending owner work is derived by StateProjection."""

    def _add_pending(self, phase_id: str, step_id: str) -> None:
        """Pending owner work is derived by StateProjection."""

    def _invalidate_steps(
        self,
        steps: tuple[StepDefinition, ...],
        *,
        reason: str,
        force_clear: Optional[set[str]] = None,
    ) -> TransitionResult | None:
        if rejected := self._invalidation_check(steps):
            return rejected
        force_clear = force_clear or set()
        affected_keys = {step.key for step in steps}
        self._outcome_permits = {
            permit: generation for permit, generation in self._outcome_permits.items()
            if f"{permit.phase}.{permit.step}" not in affected_keys
        }
        for step in steps:
            record = self.state.get_step(step.phase_id, step.id)
            active = (
                record.status in ("running", "in_flight")
                or bool(record.execution)
            )
            record.completed_at = None
            record.invalidated_at = self.now()
            record.invalidation_reason = reason
            if active and step.key not in force_clear:
                record.status = "blocked"
                record.note = (
                    f"Result invalidated while execution "
                    f"{(record.execution or {}).get('id')} may still "
                    f"be active. Owner review required. {reason}"
                )
            else:
                record.status = "pending"
                record.note = f"Invalidated: {reason}"
                record.by = None
                self._close_write_review(record.data, record.execution)
                record.execution = None
            self.state.set_step(step.phase_id, step.id, record)
        self.state.gate_decisions = [
            decision
            for decision in self.state.gate_decisions
            if decision.get("step") not in affected_keys
        ]

    def _close_write_review(self, data: dict, execution: Optional[dict]) -> None:
        if execution and execution.get("write_review"):
            from orchestrator.revision import revision_id
            data["last_write_review"] = {
                "execution_id": execution["id"], **deepcopy(execution["write_review"]),
                "approved_at": execution["started_at"],
                "workflow_revision": revision_id(self.state.workflow_revision),
            }

    @staticmethod
    def _bind_notification_identity(data: dict, execution: Optional[dict]) -> None:
        if not execution or not execution.get("notification_id"):
            return
        current = {
            "notification_id": data.get("notification_id"),
            "notification_execution_id": data.get("notification_execution_id"),
        }
        incoming = {
            "notification_id": execution["notification_id"],
            "notification_execution_id": execution.get("id"),
        }
        if current["notification_id"] and current != incoming:
            history = data.setdefault("previous_notifications", [])
            if current not in history:
                history.append(current)
        data.update(incoming)

    @staticmethod
    def _reject(
        message: str, phase: Optional[str] = None, step: Optional[str] = None
    ) -> TransitionResult:
        return TransitionResult("idle", message, phase, step, False)

    @staticmethod
    def _accept(
        kind: str,
        message: str,
        phase: Optional[str] = None,
        step: Optional[str] = None,
    ) -> TransitionResult:
        return TransitionResult(kind, message, phase, step, True)


def _gate_dict(decision: GateDecision) -> dict:
    return {
        "step": decision.step,
        "decision": decision.decision,
        "at": decision.at,
        "by": decision.by,
        "comment": decision.comment,
    }
