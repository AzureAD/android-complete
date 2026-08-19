"""Automation registry — tracks the Scout automations the orchestrator provisions
for a release, so they can be cleanly torn down at release close.

The engine/CLI never call Scout's automation API (creating/deleting automations is
the skill's job via m_create_automation / m_delete_automation). This module only
RECORDS which automations exist, tagged by release + scope + the STEPS each drives,
so the skill knows exactly what to remove at the end (nothing gets orphaned) and so
automation<->step linkage is queryable both ways (`list(release=…)` shows an
automation's steps; `list(step=…)` shows which automation owns a step).

Two scopes:
  * shared  — machine-wide, reused across releases (e.g. "Release push reminders").
              NOT torn down per release.
  * release — provisioned for one release; removed when that release closes.

Two kinds (what an automation acts on):
  * step-driving — runs one or more named steps at a fire time (owns steps[]).
  * release-level — operates on the whole release, not a step (e.g. the hourly
                    `tick` push-reminder). Owns NO steps.

Stored machine-wide at <runs_root>/_automations.json (gitignored runtime state).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

KINDS = ("release-level", "step-driving")


def kind_of(entry: dict) -> str:
    """The automation's kind, deriving it for older entries that predate the field:
    an entry that drives steps is step-driving, otherwise release-level."""
    k = entry.get("kind")
    if k in KINDS:
        return k
    return "step-driving" if (entry.get("steps") or []) else "release-level"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationRegistry:
    def __init__(self, runs_root: str):
        self.runs_root = runs_root
        self.path = os.path.join(runs_root, "_automations.json")

    def _load(self) -> list:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, entries: list) -> None:
        os.makedirs(self.runs_root, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
        os.replace(tmp, self.path)

    def register(self, auto_id: str, name: str, release: str = None,
                 shared: bool = False, purpose: str = "", steps: list = None,
                 kind: str = None, schedule: str = None, slug: str = None) -> dict:
        """Record an automation (upsert by id). Shared automations store release=None.
        `steps` is the list of '<phase>.<step>' ids this automation drives — the
        automation<->step linkage used for traceability.

        `slug` is the stable identity from config/automations.yaml (e.g. 'ccd-noon').
        It's the reliable key for `automation sync` — matching by steps alone is
        ambiguous when two automations share a step (the noon trigger and the poller
        both drive ccd.localization). `schedule` is the Scout schedule it was created
        with, stored so sync can detect when a CCD change made a cron schedule stale.

        `kind` is 'step-driving' (owns steps) or 'release-level' (whole-release, no
        steps). Omit to auto-derive from `steps`. The two can't contradict:
        step-driving requires steps; release-level forbids them."""
        steps = list(steps or [])
        derived = "step-driving" if steps else "release-level"
        if kind is None:
            kind = derived
        elif kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        elif kind == "step-driving" and not steps:
            raise ValueError("a 'step-driving' automation must declare at least one step")
        elif kind == "release-level" and steps:
            raise ValueError("a 'release-level' automation must not own steps")
        entry = {
            "id": auto_id,
            "name": name,
            "slug": slug or None,
            "kind": kind,
            "scope": "shared" if shared else "release",
            "release": None if shared else release,
            "purpose": purpose,
            "steps": steps,
            "schedule": schedule or None,
            "registered_at": _now(),
        }
        entries = [e for e in self._load() if e.get("id") != auto_id]  # upsert
        entries.append(entry)
        self._save(entries)
        return entry

    def deregister(self, auto_id: str) -> bool:
        entries = self._load()
        kept = [e for e in entries if e.get("id") != auto_id]
        self._save(kept)
        return len(kept) != len(entries)

    def list(self, release: str = None, scope: str = None, step: str = None,
             kind: str = None) -> list:
        """List entries. `release` filters to that release's automations (scope
        'release' whose release matches). `scope` filters by 'shared'/'release'.
        `step` filters to automations that DRIVE that '<phase>.<step>' id (reverse
        lookup). `kind` filters by 'step-driving'/'release-level'."""
        entries = self._load()
        if release is not None:
            entries = [e for e in entries if e.get("release") == release]
        if scope is not None:
            entries = [e for e in entries if e.get("scope") == scope]
        if step is not None:
            entries = [e for e in entries if step in (e.get("steps") or [])]
        if kind is not None:
            entries = [e for e in entries if kind_of(e) == kind]
        return entries
