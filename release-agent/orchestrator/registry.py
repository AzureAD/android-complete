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

Stored machine-wide at <runs_root>/_automations.json (gitignored runtime state).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


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
                 shared: bool = False, purpose: str = "", steps: list = None) -> dict:
        """Record an automation (upsert by id). Shared automations store release=None.
        `steps` is the list of '<phase>.<step>' ids this automation drives — the
        automation<->step linkage used for traceability."""
        entry = {
            "id": auto_id,
            "name": name,
            "scope": "shared" if shared else "release",
            "release": None if shared else release,
            "purpose": purpose,
            "steps": list(steps or []),
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

    def list(self, release: str = None, scope: str = None, step: str = None) -> list:
        """List entries. `release` filters to that release's automations (scope
        'release' whose release matches). `scope` filters by 'shared'/'release'.
        `step` filters to automations that DRIVE that '<phase>.<step>' id (reverse
        lookup — which automation owns a step)."""
        entries = self._load()
        if release is not None:
            entries = [e for e in entries if e.get("release") == release]
        if scope is not None:
            entries = [e for e in entries if e.get("scope") == scope]
        if step is not None:
            entries = [e for e in entries if step in (e.get("steps") or [])]
        return entries
