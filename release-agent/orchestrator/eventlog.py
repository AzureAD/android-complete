"""Event log — the per-release record we analyze to debug and improve.

Scope: PER-RELEASE ONLY. Each release keeps its own complete log at
    <runs_root>/<release>/events.jsonl
There is no machine-wide aggregate — a release is a self-contained unit and its
log travels with it.

What it must capture to be useful for debugging: not just the engine commands,
but the actual INTERACTION —
  * what Scout presented to the engineer (prompts, the rendered checklist, options)
  * what the engineer chose / typed (their input)
  * the engine's own events (steps, gate holds, decisions with drivers)

Each line is one JSON object:
  { ts, release_id, actor, source, event, ... }
  source: "engine" (deterministic engine action) | "scout" (agent output) | "user" (engineer input)

Logging is best-effort and MUST never break or alter the interaction.
"""
from __future__ import annotations
import json
import os
import getpass
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.getenv("USERNAME") or os.getenv("USER") or "unknown"


class EventLog:
    def __init__(self, runs_root: str, release_id: str):
        self.runs_root = runs_root
        self.release_id = release_id
        self.path = os.path.join(runs_root, release_id, "events.jsonl")
        self.actor = _actor()

    def log(self, event: str, source: str = "engine", **fields) -> dict:
        rec = {"ts": _now(), "release_id": self.release_id, "actor": self.actor,
               "source": source, "event": event}
        rec.update({k: v for k, v in fields.items() if v is not None})
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass  # logging must never break the flow
        return rec

    # convenience wrappers for the interaction layer (called by the skill via CLI)
    def scout_said(self, text: str, kind: str = "message", options=None) -> dict:
        return self.log("scout_output", source="scout", kind=kind, text=text, options=options)

    def user_said(self, text: str, kind: str = "input", choice=None) -> dict:
        return self.log("user_input", source="user", kind=kind, text=text, choice=choice)

    def read(self, limit: int = None) -> list:
        return _read_jsonl(self.path, limit)


def _read_jsonl(path: str, limit: int = None) -> list:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out[-limit:] if limit else out


def summarize(events: list) -> dict:
    """Roll up a single release's events for quick debugging."""
    by_event, by_source = {}, {}
    gate_decisions, declines, interactions = [], [], 0
    for e in events:
        by_event[e.get("event")] = by_event.get(e.get("event"), 0) + 1
        by_source[e.get("source")] = by_source.get(e.get("source"), 0) + 1
        if e.get("source") in ("scout", "user"):
            interactions += 1
        if e.get("event") in ("gate_approved", "gate_denied"):
            gate_decisions.append({"phase": e.get("phase"), "step": e.get("step"),
                                   "decision": "approved" if e["event"] == "gate_approved" else "denied",
                                   "driver": e.get("driver"), "actor": e.get("actor"), "ts": e.get("ts")})
        if e.get("event") == "readiness_declined":
            declines.append({"items": e.get("items"), "owner_blocked": e.get("owner_blocked"),
                             "driver": e.get("driver"), "ts": e.get("ts")})
    return {
        "release": events[0]["release_id"] if events else None,
        "total_events": len(events),
        "by_source": by_source,
        "by_event": by_event,
        "interactions_logged": interactions,
        "gate_decisions": gate_decisions,
        "declines": declines,
    }
