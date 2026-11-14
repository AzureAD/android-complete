"""Manual-only outcome for unimplemented steps; no provider work or approval occurs."""
from __future__ import annotations

from orchestrator.outcomes import Blocked


def build(step: dict) -> Blocked:
    return Blocked(
        f"Manual-only: {step.get('name', step['id'])}. No automatic implementation is available. "
        "Complete the work outside the tool and record an owner-attested result with record-fact."
    )
