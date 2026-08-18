"""Release Orchestrator — run-state model (X5).

Two kinds of state, per the architecture:
  * DERIVED  : recomputed from systems of record (ADO/Git/Play Console/ADX). Never stored here.
  * PERSISTED: decisions/intent, step completion, pending human actions, notes.
               Stored in release-state.json (the Release State Record).

This module owns ONLY the persisted state. Reconcile-on-resume (deriving live
state) is a separate concern handled by tools/reconcile.py (stubbed for now).
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from typing import Optional


SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepState:
    """Persisted state for a single step."""
    status: str = "pending"          # pending | done | skipped | blocked
    completed_at: Optional[str] = None
    note: Optional[str] = None
    by: Optional[str] = None         # 'agent' (stub) or 'human'
    links: list = field(default_factory=list)   # [{name, url}] — durable refs (wiki page, CG alerts)
    data: dict = field(default_factory=dict)    # step-private scratch (e.g. localization build id/start)


@dataclass
class GateDecision:
    """A recorded human decision at a gate (audit trail)."""
    step: str
    decision: str                    # approved | denied | held
    at: str
    by: str = "human"
    comment: Optional[str] = None


@dataclass
class ReleaseState:
    """The Release State Record — one per monthly release."""
    schema_version: int = SCHEMA_VERSION
    release_id: str = ""             # e.g. 2026-07
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Release owner — the engineer running this release (release metadata). The
    # push reminders email this address; resolved from the signed-in user at init.
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    # Code Complete Date — the anchor the phases hang off of (orchestrator's truth,
    # seeded from / written back to pipeline 3038). ccd is 'YYYY-MM-DD'.
    ccd: Optional[str] = None
    ccd_source: Optional[str] = None      # 'default' (2nd Wed) | 'override' | 'manual'
    ccd_conflict: Optional[str] = None    # a pipeline override date that DIFFERS from ccd (unresolved)
    skip_release: bool = False            # mirrors the pipeline 'skipRelease' switch (display)
    # readiness entry gate (must be signed before Phase 0)
    readiness_signed: bool = False
    readiness_signed_at: Optional[str] = None
    readiness_items: dict = field(default_factory=dict)   # item_id -> {status,...}
    blocked: bool = False                                 # an item was declared unsatisfiable
    blocked_items: list = field(default_factory=list)
    # manual overrides (human-driven transitions, §7.1)
    halted: bool = False                                  # emergency hold
    halt_reason: Optional[str] = None
    # cursor
    current_phase: Optional[str] = None
    current_step: Optional[str] = None
    status: str = "not_started"      # not_started | running | scheduled | awaiting_action | holding_gate | halted | blocked | complete
    # persisted detail
    steps: dict = field(default_factory=dict)         # "phase.step" -> StepState (as dict)
    gate_decisions: list = field(default_factory=list)
    pending_human: list = field(default_factory=list) # outstanding human actions
    last_notified: Optional[str] = None               # last push message (legacy; kept for load compat)
    last_notified_date: Optional[str] = None          # YYYY-MM-DD of the last daily digest sent
    notes: list = field(default_factory=list)

    # ---- persistence ----
    @classmethod
    def load(cls, path: str) -> "ReleaseState":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Tolerate unknown/legacy keys: a persisted state file may predate a
        # field rename/removal (or be hand-edited), and this loader runs in an
        # unattended automation — an unexpected key must never hard-crash it.
        # Only keys matching a declared field are applied; the rest are dropped.
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str) -> None:
        self.updated_at = _now()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)

    # ---- step helpers ----
    @staticmethod
    def key(phase: str, step: str) -> str:
        return f"{phase}.{step}"

    def get_step(self, phase: str, step: str) -> StepState:
        raw = self.steps.get(self.key(phase, step))
        return StepState(**raw) if raw else StepState()

    def set_step(self, phase: str, step: str, state: StepState) -> None:
        self.steps[self.key(phase, step)] = asdict(state)

    def is_done(self, phase: str, step: str) -> bool:
        return self.get_step(phase, step).status in ("done", "skipped")
