"""Release discovery — the none / one / many logic.

Scans the runs root for release folders and reports what's there so the
/release-agent skill can:
  * 0 releases  -> tell the user none is active, offer to start one
  * 1 release   -> use it
  * many        -> present the assumed one (most recently updated) + ask to confirm

Deterministic; the skill only presents what this returns.
"""
from __future__ import annotations
import os
import json
from typing import Optional


def _summarize(state_file: str) -> Optional[dict]:
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):          # missing/unreadable file or bad JSON → skip it
        return None
    return {
        "release_id": data.get("release_id"),
        "status": data.get("status"),
        "current_phase": data.get("current_phase"),
        "current_step": data.get("current_step"),
        "updated_at": data.get("updated_at"),
        "state_file": state_file,
    }


def list_releases(runs_root: str) -> list:
    """Return summaries of all releases found, newest-updated first."""
    out = []
    if not os.path.isdir(runs_root):
        return out
    for name in os.listdir(runs_root):
        sf = os.path.join(runs_root, name, "release-state.json")
        if os.path.isfile(sf):
            s = _summarize(sf)
            if s:
                out.append(s)
    out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return out


def resolve(runs_root: str, requested: Optional[str] = None) -> dict:
    """Decide which release to act on.

    Returns a dict:
      { "resolution": "none" | "one" | "explicit" | "ambiguous",
        "release": <summary or None>,        # the chosen/assumed release
        "all": [<summaries>] }               # everything found

    - none       : no releases exist -> caller should offer to start one
    - one        : exactly one exists -> use it
    - explicit   : caller named one and it exists -> use it
    - ambiguous  : several exist and none named -> 'release' is the assumed
                   (most recently updated); caller should confirm.
    """
    all_ = list_releases(runs_root)
    if requested:
        match = next((r for r in all_ if r["release_id"] == requested), None)
        return {"resolution": "explicit" if match else "none",
                "release": match, "all": all_}
    if not all_:
        return {"resolution": "none", "release": None, "all": all_}
    if len(all_) == 1:
        return {"resolution": "one", "release": all_[0], "all": all_}
    return {"resolution": "ambiguous", "release": all_[0], "all": all_}
