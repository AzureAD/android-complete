"""Package of co-located step handlers — the NEW uniform home for a step.

Each step is ONE module that fully defines it: its `KIND`, how it `build`s its
outcome (an agent action, a scout NeedsSkill payload, or a human prompt), plus
any content/template it uses. This replaces the old split where a scout step was
scattered across phases.yaml + stub_runner + commands/*.py + templates + skill md.

Contract a step module exposes:
    ID: str                     # step id (matches config/phases.yaml)
    KIND: str                   # 'agent' | 'scout' | 'attest'
    def build(state) -> Outcome # returns one of orchestrator.outcomes.*

`get_step(phase_id, step_id)` returns the module or None (not every step is
migrated yet — the engine falls back to the legacy path when None).
"""
from __future__ import annotations

from importlib import import_module

# Migrated steps, keyed by "<phase>.<step>". Add a line when a step moves here.
_STEPS = {
    "preflight.notice": "steps.preflight.notice",
    "preflight.flight_reminder": "steps.preflight.flight_reminder",
    "preflight.lockdown": "steps.preflight.lockdown",
    "preflight.confirm_reminders": "steps.preflight.confirm_reminders",
    "preflight.vitals": "steps.preflight.vitals",
    "preflight.breaking": "steps.preflight.breaking",
    "preflight.cg": "steps.preflight.cg",
    "preflight.cron": "steps.preflight.cron",
    "preflight.wiki": "steps.preflight.wiki",
}


def get_step(phase_id: str, step_id: str):
    """Return the co-located step handler module for <phase>.<step>, or None if
    that step hasn't been migrated to the uniform contract yet."""
    mod_path = _STEPS.get(f"{phase_id}.{step_id}")
    if not mod_path:
        return None
    return import_module(mod_path)
