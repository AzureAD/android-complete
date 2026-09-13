"""No-op shell for unfinished auto steps; no provider work or approval occurs."""
from __future__ import annotations

from orchestrator.outcomes import Done


def build(step: dict) -> Done:
    return Done(
        f"[DUMMY] {step.get('name', step['id'])}: no operation performed; "
        "implementation deferred."
    )
