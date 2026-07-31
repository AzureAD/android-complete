"""Stub phase runner — the fallback for steps that don't yet have a real agent.

Phase 0 has real agents (phases/agents/preflight.py); every other step still
maps to `agent: stub`. The stub does NOT perform the real action — it returns a
mock result telling the conductor what a human would do, so the end-to-end flow
can be driven and tested before each real agent exists.

When a real phase agent is built, it implements the same contract:
    run(phase_id, step, dry_run, state) -> StepResult
and replaces the stub for that step's `agent` id (registered in phases/agents/).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class StepResult:
    ok: bool
    action: str          # human-readable description of what happened / should happen
    by: str              # 'agent' (stub did it) or 'human' (needs a person)


def run_stub(phase_id: str, step: dict, dry_run: bool, state=None) -> StepResult:
    """Mock action for a step. Agent-owned steps are 'auto-completed' (mock);
    human-owned steps return a reminder that a person must act."""
    owner = step.get("owner", "agent")
    name = step.get("name", step["id"])
    if owner == "human":
        return StepResult(
            ok=True,
            action=f"[STUB] Reminder — a human must: {name}",
            by="human",
        )
    prefix = "[STUB/dry-run]" if dry_run else "[STUB]"
    return StepResult(
        ok=True,
        action=f"{prefix} Would run agent for: {name} (mock success)",
        by="agent",
    )


# Registry: maps an agent id -> runner. For now everything is the stub.
REGISTRY = {"stub": run_stub}


def get_runner(agent_id: str):
    return REGISTRY.get(agent_id, run_stub)
