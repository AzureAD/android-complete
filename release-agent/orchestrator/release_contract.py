"""Shared release lifecycle and dispatch contract values."""
from __future__ import annotations

from enum import Enum


class HoldKind(str, Enum):
    ACTION = "action"
    DENIED = "denied"
    GATE = "gate"
    IN_FLIGHT = "in_flight"
    SCHEDULED = "scheduled"
    SCOUT = "scout"
    WAITING = "waiting"


class LifecycleStatus(str, Enum):
    AWAITING_ACTION = "awaiting_action"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    COMPLETE = "complete"
    HALTED = "halted"
    HOLDING_GATE = "holding_gate"
    READINESS_GATE = "readiness_gate"
    RUNNING = "running"
    SCHEDULED = "scheduled"


class DispatchAction(str, Enum):
    COMPLETE = "complete"
    OWNER_OR_WAIT = "owner_or_wait"
    READINESS = "readiness"
    RESOLVE_HOST_INPUT = "resolve_host_input"
    RESOLVE_PHASE_HOLD = "resolve_phase_hold"
    RESOLVE_RELEASE_HOLD = "resolve_release_hold"
    RUN_PHASE_WORK = "run_phase_work"
    SCHEDULED = "scheduled"


_SUSPENSION_TO_LIFECYCLE: dict[str, LifecycleStatus] = {
    "denied": LifecycleStatus.BLOCKED,
    "halted": LifecycleStatus.HALTED,
    "cancelled": LifecycleStatus.CANCELLED,
    "readiness_gate": LifecycleStatus.READINESS_GATE,
    "blocked": LifecycleStatus.BLOCKED,
}

_FOCUS_TO_LIFECYCLE: dict[str, LifecycleStatus] = {
    HoldKind.SCHEDULED.value: LifecycleStatus.SCHEDULED,
    HoldKind.GATE.value: LifecycleStatus.HOLDING_GATE,
    HoldKind.DENIED.value: LifecycleStatus.BLOCKED,
    HoldKind.IN_FLIGHT.value: LifecycleStatus.RUNNING,
}


def lifecycle_status(
    *,
    suspension: str | None,
    frontier_exists: bool,
    focus_kind: str | None,
) -> str:
    """Canonical release lifecycle status derived from projection facts."""
    if suspension:
        return _SUSPENSION_TO_LIFECYCLE.get(suspension, LifecycleStatus.BLOCKED).value
    if not frontier_exists:
        return LifecycleStatus.COMPLETE.value
    if focus_kind:
        return _FOCUS_TO_LIFECYCLE.get(
            focus_kind, LifecycleStatus.AWAITING_ACTION
        ).value
    return LifecycleStatus.RUNNING.value
