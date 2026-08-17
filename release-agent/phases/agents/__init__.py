"""Phase-agent registry — aggregates every phase's agents into one lookup.

Each phase's real agents live in `phases/agents/<phase>.py`, which exposes a
module-level `REGISTRY = {agent_id: run(phase_id, step, state) -> StepResult}`.
This package merges them all into a single `REGISTRY` so the engine does ONE
lookup, and guards against two phases claiming the same agent id.

Adding a phase's agents is a one-line change: create `phases/agents/<phase>.py`
with a `REGISTRY`, then add its name to `_PHASE_MODULES` below. The engine never
changes — it already looks up the merged registry.
"""
from __future__ import annotations

from importlib import import_module

from phases.stub_runner import get_runner as _stub_get_runner

# Phase agent modules, in phase order. Add a new phase's module name here.
_PHASE_MODULES = [
    "preflight",
]

REGISTRY: dict = {}
for _name in _PHASE_MODULES:
    _mod = import_module(f"{__name__}.{_name}")
    for _agent_id, _runner in getattr(_mod, "REGISTRY", {}).items():
        if _agent_id in REGISTRY:
            raise RuntimeError(
                f"duplicate phase-agent id '{_agent_id}': phases.agents.{_name} "
                f"collides with an agent already registered by an earlier phase")
        REGISTRY[_agent_id] = _runner


def get_runner(agent_id: str):
    """The ONE dispatch seam the engine uses: return the real phase agent for
    `agent_id` if one is registered, otherwise the stub runner (which handles
    unbuilt steps). Always returns a callable with the run(...) contract."""
    return REGISTRY.get(agent_id) or _stub_get_runner(agent_id)
