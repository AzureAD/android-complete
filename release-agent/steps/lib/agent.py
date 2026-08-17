"""Adapter between the uniform step contract and the engine's agent seam.

Agent steps are deterministic and run IN-PROCESS inside the engine's `next`
(unlike scout steps, which hold for the skill). The engine's execution seam is the
legacy `run(phase_id, step, state) -> StepResult` callable. This module lets an
agent step author the ONE uniform `build(state) -> Outcome` and get that legacy
callable for free:

    from steps.lib.agent import legacy_run
    run = legacy_run(build)          # what phases/agents registers for the engine

`build(state)` is the single home for the logic; `legacy_run` bridges its
`Done`/`Blocked` outcome back to `StepResult` so the engine + existing tests are
unchanged.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from phases.stub_runner import StepResult


def to_step_result(outcome) -> StepResult:
    """Map a uniform Outcome to the engine's StepResult (agent steps only ever
    return Done/Blocked — never NeedsSkill/NeedsHuman)."""
    if isinstance(outcome, Done):
        return StepResult(ok=True, action=outcome.note, by=outcome.by,
                          links=list(outcome.links or []))
    if isinstance(outcome, Blocked):
        return StepResult(ok=False, action=outcome.reason, by="agent",
                          links=list(outcome.links or []))
    raise TypeError(
        f"agent step returned {type(outcome).__name__}; expected Done or Blocked")


def legacy_run(build):
    """Wrap a uniform `build(state)` as the engine's `run(phase_id, step, state) ->
    StepResult` agent callable."""
    def run(phase_id, step, state=None):
        return to_step_result(build(state))
    return run
