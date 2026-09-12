"""Package of co-located step handlers — the single home for a step.

Each step is ONE module under `steps/<phase>/<step>.py` that fully defines it:
its `ID`, `KIND`, how it `build`s its outcome (an agent action, a scout NeedsSkill
payload, or a human prompt), plus its knobs / knowledge / config. There is NO
hand-maintained registry — steps are AUTO-DISCOVERED by scanning the package, so
adding a step can never "forget" to register it.

Contract a step module exposes:
    ID: str                     # step id (matches config/phases.yaml)
    KIND: str                   # 'agent' | 'scout' | 'attest'
    def build(state) -> Outcome # returns one of orchestrator.outcomes.*
    # optional: NAME, MOCKABLE, KNOWLEDGE, CONFIG; agent steps: run = legacy_run(build)

`discover()` returns {"<phase>.<step>": module} for every step module found.
`get_step(phase, step)` returns the module or None (a not-yet-migrated stub step).
"""
from __future__ import annotations

import os
from importlib import import_module

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_NON_PHASE = {"lib"}          # subpackages that are helpers, not phases
_CACHE = None


def discover(force: bool = False) -> dict:
    """Scan steps/<phase>/*.py and return {"<phase>.<ID>": module} for every module
    that declares an `ID` and a `build`. Cached; pass force=True to rescan."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    out = {}
    for phase in sorted(os.listdir(_PKG_DIR)):
        sub = os.path.join(_PKG_DIR, phase)
        if (phase in _NON_PHASE or phase.startswith(("_", "."))
                or not os.path.isdir(sub)
                or not os.path.exists(os.path.join(sub, "__init__.py"))):
            continue
        for fn in sorted(os.listdir(sub)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            mod = import_module(f"{__name__}.{phase}.{fn[:-3]}")
            sid = getattr(mod, "ID", None)
            if sid is None or not hasattr(mod, "build"):
                continue
            key = f"{phase}.{sid}"
            if key in out:
                raise RuntimeError(
                    f"duplicate step module for '{key}': {mod.__name__} collides "
                    f"with {out[key].__name__}")
            out[key] = mod
    _CACHE = out
    return out


def get_step(phase_id: str, step_id: str):
    """Return the co-located step module for <phase>.<step>, or None if that step
    has no module yet (a stub step handled by the engine's generic path)."""
    return discover().get(f"{phase_id}.{step_id}")
