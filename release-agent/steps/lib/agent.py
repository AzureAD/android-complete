"""Adapter between the uniform step contract and the engine's agent seam.

Agent steps are deterministic and run IN-PROCESS inside the engine's `next`
(unlike scout steps, which hold for the skill). The engine's execution seam is
the legacy `run(phase_id, step, dry_run, state) -> StepResult` callable. This
module lets an agent step author the ONE uniform `build(state) -> Outcome` and get
that legacy callable for free:

    from steps.lib.agent import legacy_run
    run = legacy_run(build)          # what phases/agents registers for the engine

`build(state)` is the single home for the logic; `legacy_run` bridges its
`Done`/`Blocked` outcome back to `StepResult` so the engine + existing tests are
unchanged. It also reconciles the engine's separate `dry_run` argument onto the
state (the engine always passes `state.dry_run`; unit tests may pass a different
value or a `None` state) via a read-only `StateView`.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from phases.stub_runner import StepResult


class StateView:
    """Read-only overlay presenting `dry_run` while delegating everything else to
    the wrapped state (or returning None when there's no state)."""
    def __init__(self, state, dry_run):
        self._s = state
        self.dry_run = dry_run

    def __getattr__(self, name):          # only for attrs not set on the instance
        return getattr(self._s, name, None)


def to_step_result(outcome) -> StepResult:
    """Map a uniform Outcome to the engine's StepResult (agent steps only ever
    return Done/Blocked — never NeedsSkill/NeedsHuman)."""
    if isinstance(outcome, Done):
        return StepResult(ok=True, action=outcome.note, by=outcome.by)
    if isinstance(outcome, Blocked):
        return StepResult(ok=False, action=outcome.reason, by="agent")
    raise TypeError(
        f"agent step returned {type(outcome).__name__}; expected Done or Blocked")


def legacy_run(build):
    """Wrap a uniform `build(state)` as the engine's `run(phase_id, step, dry_run,
    state) -> StepResult` agent callable."""
    def run(phase_id, step, dry_run, state=None):
        return to_step_result(build(StateView(state, dry_run)))
    return run
