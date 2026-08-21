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

Storage layout:
  * <runs_root>/<release>/_automations.json — a release's own automations, co-located
    with its release-state.json so ownership is explicit (and they're removed with the
    release folder at close).
  * <runs_root>/_automations.json — SHARED (machine-wide) automations only; these are
    reused across releases and are not tied to any one release folder.
"""
from __future__ import annotations

import glob
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
    def __init__(self, runs_root: str, release: str = None):
        self.runs_root = runs_root
        self.release = release
        # SHARED (machine-wide) automations only; release automations live in <release>/.
        self.shared_path = os.path.join(runs_root, "_automations.json")

    # ---- paths ----
    def _release_path(self, release: str) -> str:
        """A release's own registry file, next to its release-state.json."""
        return os.path.join(self.runs_root, release, "_automations.json")

    def _release_files(self) -> list:
        """Every per-release registry file under runs_root."""
        return sorted(glob.glob(os.path.join(self.runs_root, "*", "_automations.json")))

    # ---- file IO ----
    def _load_file(self, path: str) -> list:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_file(self, path: str, entries: list) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
        os.replace(tmp, path)

    def _path_for(self, entry: dict) -> str:
        """Where an entry is stored: its release folder, else the shared file."""
        if entry.get("scope") == "release" and entry.get("release"):
            return self._release_path(entry["release"])
        return self.shared_path

    # ---- api ----
    def register(self, auto_id: str, name: str, release: str = None,
                 shared: bool = False, purpose: str = "", steps: list = None,
                 kind: str = None, schedule: str = None, slug: str = None) -> dict:
        """Record an automation (upsert by id). Shared automations store release=None and
        live in the machine-wide file; release automations live in <release>/. `steps` is
        the list of '<phase>.<step>' ids this automation drives — the automation<->step
        linkage used for traceability.

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
            "release": None if shared else (release or self.release),
            "purpose": purpose,
            "steps": steps,
            "schedule": schedule or None,
            "registered_at": _now(),
        }
        # Upsert: drop any prior copy of this id wherever it lived, then write to its
        # (possibly new) home file.
        self._remove_everywhere(auto_id)
        path = self._path_for(entry)
        entries = self._load_file(path)
        entries.append(entry)
        self._save_file(path, entries)
        return entry

    def _remove_everywhere(self, auto_id: str) -> bool:
        """Drop `auto_id` from the shared file and every per-release file. Returns True
        if it was found somewhere."""
        removed = False
        for path in [self.shared_path, *self._release_files()]:
            entries = self._load_file(path)
            kept = [e for e in entries if e.get("id") != auto_id]
            if len(kept) != len(entries):
                self._save_file(path, kept)
                removed = True
        return removed

    def deregister(self, auto_id: str) -> bool:
        return self._remove_everywhere(auto_id)

    def list(self, release: str = None, scope: str = None, step: str = None,
             kind: str = None) -> list:
        """List entries. `release` filters to that release's automations (and reads only
        that release's file + the shared file). `scope` filters by 'shared'/'release'.
        `step` filters to automations that DRIVE that '<phase>.<step>' id (reverse
        lookup). `kind` filters by 'step-driving'/'release-level'."""
        entries = self._load_file(self.shared_path)
        if release is not None:
            entries += self._load_file(self._release_path(release))
        else:
            for path in self._release_files():
                entries += self._load_file(path)
        if release is not None:
            entries = [e for e in entries if e.get("release") == release]
        if scope is not None:
            entries = [e for e in entries if e.get("scope") == scope]
        if step is not None:
            entries = [e for e in entries if step in (e.get("steps") or [])]
        if kind is not None:
            entries = [e for e in entries if kind_of(e) == kind]
        return entries
