"""Per-phase config loader — one convention: config/<phase>.yaml.

Each phase's config lives beside the phase and mirrors its code
(phases/agents/<phase>.py ↔ config/<phase>.yaml), so adding a phase is a
predictable recipe with no hard-coded filenames scattered across modules.

Cross-cutting config (requirements.yaml, schedule.yaml, readiness.yaml) is
release-wide, NOT per-phase, and is loaded elsewhere — it does not go through here.

Import-light (os + yaml only) so both the phase agents (phases/) and the CLI
command modules (orchestrator/commands/) can use it without a cycle.
"""
from __future__ import annotations

import os

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def phase_config_path(phase_id: str) -> str:
    """Absolute path to a phase's config file: config/<phase>.yaml."""
    return os.path.join(_ROOT, "config", f"{phase_id}.yaml")


def load_phase_config(phase_id: str, section: str | None = None) -> dict:
    """Load config/<phase>.yaml. With `section`, return just that top-level key
    (e.g. load_phase_config('preflight', 'cg')); without it, the whole document.
    Missing file or key → empty dict (agents fall back to their own defaults)."""
    try:
        with open(phase_config_path(phase_id), "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        data = {}
    if section is not None:
        return data.get(section, {}) or {}
    return data
