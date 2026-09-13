"""Pure projections over release facts and a compiled workflow."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Callable, Iterable, Optional

from orchestrator import schedule
from orchestrator.state import ReleaseState
from orchestrator.workflow import PhaseDefinition, StepDefinition, StepKind, WorkflowDefinition


@dataclass(frozen=True)
class HoldProjection:
    kind: str
    phase_id: str
    step_id: Optional[str] = None
    owner: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class PhaseReadiness:
    definition: PhaseDefinition
    included: bool
    complete: bool
    due: bool
    opens: Optional[date]


@dataclass(frozen=True)
class StepReadiness:
    definition: StepDefinition
    complete: bool
    status: str
    prerequisites_met: bool
    time_ready: bool
    reserved: bool
    recovery: bool
    mocked: bool


@dataclass(frozen=True)
class RunnableCandidate:
    step: StepDefinition
    recovery: bool = False


@dataclass(frozen=True)
class SchedulingResult:
    """One immutable selection; presentation focus need not block parallel work.

    Readiness covers every phase/step, including completed observations whose
    poll/refresh intents deliberately have different rules from new work.
    Recovery candidates retain ownership; they never request a new reservation.
    """

    frontier: Optional[PhaseDefinition]
    suspension: Optional[str]
    status: str
    phases: tuple[PhaseReadiness, ...]
    steps: tuple[StepReadiness, ...]
    runnable: tuple[RunnableCandidate, ...]
    action_holds: tuple[HoldProjection, ...]
    focus_hold: Optional[HoldProjection]
    attempted: frozenset[str]

    def phase(self, phase_id: str) -> PhaseReadiness:
        return next(item for item in self.phases if item.definition.id == phase_id)

    def step(self, phase_id: str, step_id: str) -> StepReadiness:
        return next(
            item for item in self.steps
            if item.definition.phase_id == phase_id and item.definition.id == step_id
        )

    @property
    def pending_human(self) -> tuple[str, ...]:
        return tuple(
            f"{hold.phase_id}.{hold.step_id}"
            for hold in self.action_holds if hold.owner == "human" and hold.step_id
        )

    @property
    def scout_pending(self) -> tuple[str, ...]:
        return tuple(
            hold.step_id for hold in self.action_holds
            if hold.kind == "scout" and hold.step_id
            and self.step(hold.phase_id, hold.step_id).status == "pending"
        )


class StateProjection:
    """Derive lifecycle and cursor views without mutating persisted state."""

    def __init__(
        self,
        state: ReleaseState,
        workflow: WorkflowDefinition,
        as_of: date,
        now_local: datetime,
        readiness_signed: bool = True,
        readiness_blocked: bool = False,
        is_mocked: Optional[Callable[[StepDefinition], bool]] = None,
        fire_at: Optional[Callable[[StepDefinition], Optional[str]]] = None,
        revision_problem: Optional[str] = None,
    ):
        self.state = state
        self.workflow = workflow
        self.as_of = as_of
        self.now_local = now_local
        self.readiness_signed = readiness_signed
        self.readiness_blocked = readiness_blocked
        self.is_mocked = is_mocked or (lambda _step: False)
        self.fire_at = fire_at or (lambda _step: None)
        self.revision_problem = revision_problem or (
            "Workflow revision is unbound" if state.workflow_revision is None else "")

    def phase_included(self, phase: PhaseDefinition) -> bool:
        return not phase.conditional or phase.id in self.activated_conditionals()

    def activated_conditionals(self) -> set[str]:
        return set(self.state.active_conditionals)

    def latest_gate_decision(self, step: StepDefinition) -> Optional[dict]:
        for decision in reversed(self.state.gate_decisions):
            if decision.get("step") == step.key:
                return decision
        return None

    def gate_approved(self, step: StepDefinition) -> bool:
        decision = self.latest_gate_decision(step)
        return bool(decision and decision.get("decision") == "approved")

    def step_complete(self, step: StepDefinition) -> bool:
        if self.revision_problem:
            return False
        record = self.state.get_step(step.phase_id, step.id)
        if step.is_gate:
            return record.status == "done" and self.gate_approved(step)
        return record.status in ("done", "skipped")

    def phase_complete(self, phase: PhaseDefinition) -> bool:
        return all(self.step_complete(step) for step in phase.steps)

    def frontier_phase(self) -> Optional[PhaseDefinition]:
        return self.scheduling().frontier

    def phase_anchor_date(self, phase: PhaseDefinition) -> Optional[date]:
        ccd = schedule.parse_date(self.state.ccd)
        if not phase.anchor or ccd is None:
            return None
        return schedule.anchor_date(ccd, phase.anchor)

    def phase_due(self, phase: PhaseDefinition) -> bool:
        anchor = self.phase_anchor_date(phase)
        return anchor is None or self.as_of >= anchor

    def step_time_ready(self, step: StepDefinition) -> bool:
        fire = self.fire_at(step)
        if not fire:
            return True
        hour, minute = (int(value) for value in fire.split(":"))
        fire_time = time(hour, minute)
        phase = self.workflow.phase(step.phase_id)
        anchor = self.phase_anchor_date(phase) if phase else None
        if anchor is not None:
            if self.as_of > anchor:
                return True
            if self.as_of < anchor:
                return False
        return self.now_local.time() >= fire_time

    def prerequisites_met(self, phase: PhaseDefinition, step: StepDefinition) -> bool:
        if any(
            not self.step_complete(self.workflow.step(phase.id, dependency))
            for dependency in step.depends_on
        ):
            return False
        if phase.execution == "parallel":
            return True
        for predecessor in phase.steps:
            if predecessor.id == step.id:
                return True
            if not self.step_complete(predecessor):
                return False
        return False

    def scheduling(self, attempted: Iterable[str] = ()) -> SchedulingResult:
        """Select without invoking handlers, reserving work, or changing facts."""
        attempted = frozenset(attempted)
        phases = tuple(
            PhaseReadiness(
                phase, self.phase_included(phase), self.phase_complete(phase),
                self.phase_due(phase), self.phase_anchor_date(phase),
            )
            for phase in self.workflow.phases
        )
        frontier = next(
            (item.definition for item in phases if item.included and not item.complete),
            None,
        )
        readiness = []
        for phase in self.workflow.phases:
            for step in phase.steps:
                record = self.state.get_step(phase.id, step.id)
                execution = self._execution(step)
                recovery = bool(
                    step.kind == StepKind.AUTO
                    and step.effect_mode and step.effect_mode.writes_external_state
                    and record.status in ("running", "in_flight", "blocked")
                    and execution.get("effect_mode") == step.effect_mode.value
                )
                readiness.append(StepReadiness(
                    step, self.step_complete(step), record.status,
                    self.prerequisites_met(phase, step), self.step_time_ready(step),
                    bool(execution), recovery, self.is_mocked(step),
                ))
        steps = tuple(readiness)
        suspension = (
            "workflow_revision" if self.revision_problem else
            "cancelled" if self.state.cancellation else
            "halted" if self.state.halt else
            "blocked" if self.readiness_blocked else
            "readiness_gate" if not self.readiness_signed else None
        )
        runnable: list[RunnableCandidate] = []
        actions: list[HoldProjection] = []
        focus = None
        if frontier:
            outstanding = [
                item for item in steps
                if item.definition.phase_id == frontier.id and not item.complete
            ]
            phase_due = next(item.due for item in phases if item.definition == frontier)
            if not phase_due:
                focus = HoldProjection("scheduled", frontier.id, outstanding[0].definition.id)
            else:
                eligible = [item for item in outstanding if item.prerequisites_met]
                ready = [item for item in eligible if item.time_ready and not item.reserved]
                # Recovery is not a new write. It retains its declared recovery
                # contract and ignores the new-work fire time, not dependencies.
                runnable.extend(
                    RunnableCandidate(item.definition, recovery=True)
                    for item in eligible if item.recovery and item.definition.key not in attempted
                )
                runnable.extend(
                    RunnableCandidate(item.definition)
                    for item in ready
                    if item.definition.key not in attempted
                    and (item.definition.kind == StepKind.AUTO or item.mocked)
                )
                actions = [
                    hold for item in ready
                    if (hold := self._step_hold(item.definition)) is not None
                ]
                # Focus is for presentation, NOT a dispatch veto: pending gates
                # and human holds must not starve independent automatic work.
                focus = (
                    next((hold for hold in actions if hold.kind == "gate"), None)
                    or next((hold for hold in actions if hold.owner == "human"), None)
                    or next(iter(actions), None)
                )
                if focus is None and not ready:
                    in_flight = next((item for item in eligible if item.status == "in_flight"), None)
                    reserved = next((item for item in eligible if item.reserved), None)
                    timed = next((item for item in eligible if not item.time_ready), None)
                    if in_flight:
                        focus = self._step_hold(in_flight.definition)
                    elif reserved:
                        focus = HoldProjection(
                            "waiting", frontier.id, reserved.definition.id, reason="reservation"
                        )
                    elif timed:
                        focus = HoldProjection("scheduled", frontier.id, timed.definition.id)
                    else:
                        focus = HoldProjection("waiting", frontier.id, reason="prerequisite")
                if focus is None and not runnable:
                    focus = HoldProjection("waiting", frontier.id, reason="attempted")
            # A persisted denial is a release stop, not a new-work time window.
            # Moving the clock or leaving stale predecessors must not hide it.
            denied = next(
                (
                    self._step_hold(item.definition) for item in outstanding
                    if item.definition.is_gate
                    and (self.latest_gate_decision(item.definition) or {}).get("decision") == "denied"
                ),
                None,
            )
            if denied:
                focus = denied
                suspension = suspension or "denied"
        if suspension:
            runnable = []
            actions = []
            if suspension == "workflow_revision":
                focus = None
        if suspension:
            status = "blocked" if suspension in ("denied", "workflow_revision") else suspension
        elif frontier is None:
            status = "complete"
        elif focus:
            status = {
                "scheduled": "scheduled", "gate": "holding_gate", "denied": "blocked",
                "in_flight": "running",
            }.get(focus.kind, "awaiting_action")
        else:
            status = "running"
        return SchedulingResult(
            frontier, suspension, status, phases, steps, tuple(runnable),
            tuple(actions), focus, attempted,
        )

    def current_hold(self) -> Optional[HoldProjection]:
        return self.scheduling().focus_hold

    def pending_actions(self) -> tuple[HoldProjection, ...]:
        return self.scheduling().action_holds

    def pending_human(self) -> tuple[str, ...]:
        return self.scheduling().pending_human

    def release_status(self) -> str:
        return self.scheduling().status

    def _step_hold(self, step: StepDefinition) -> Optional[HoldProjection]:
        record = self.state.get_step(step.phase_id, step.id)
        if record.status == "in_flight":
            return HoldProjection(
                "in_flight", step.phase_id, step.id, "scout",
                "underlying work running")
        if step.is_gate:
            decision = self.latest_gate_decision(step)
            if decision and decision.get("decision") == "denied":
                return HoldProjection(
                    "denied",
                    step.phase_id,
                    step.id,
                    "human",
                    decision.get("comment") or "Gate denied.",
                )
            return HoldProjection("gate", step.phase_id, step.id, "human")
        if record.status == "blocked":
            return HoldProjection("action", step.phase_id, step.id, "human", "blocked")
        if self.is_mocked(step):
            return None
        if step.kind in (StepKind.HUMAN_ACTION, StepKind.ATTESTATION):
            return HoldProjection("action", step.phase_id, step.id, "human")
        if step.kind == StepKind.EXTERNAL:
            return HoldProjection("scout", step.phase_id, step.id, "scout")
        return None

    def _execution(self, step: StepDefinition) -> dict:
        value = self.state.get_step(step.phase_id, step.id).execution
        return dict(value) if isinstance(value, dict) else {}
