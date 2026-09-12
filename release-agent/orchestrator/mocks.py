"""Local step mocks — a personal, gitignored testing hook.

Drop a `mocks.local.yaml` in the release-agent/ root and the engine will REPLACE
the listed steps with the behavior you declare, on every `next` / `step-action`,
for a real release. Everything not listed runs for real, so your
normal Scout interaction is unchanged — you just run the release and the mocked
steps resolve themselves.

File format (top-level map of "<phase>.<step>" → behavior):

    # mocks.local.yaml  (gitignored — yours, never pushed)
    preflight.cg:
      outcome: done               # done | blocked
      note: "mocked: CG clean"
    finalize.wiki_payload:
      outcome: blocked
      reason: "mocked: pretend the payload-wiki write failed"

A `mocks:` wrapper key is also accepted. `outcome: done` marks the step complete;
`outcome: blocked` holds it for the owner (with `reason`/`note`). Gate steps are
never mockable (a gate needs a real decision). Point at a different file with the
RELEASE_AGENT_MOCKS env var.
"""
from __future__ import annotations
import os

import yaml

from phases.stub_runner import StepResult

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # release-agent/
_DONE = {"done", "pass", "ok", "complete"}
_BLOCK = {"blocked", "block", "attention", "hold", "fail"}


def mocks_path() -> str:
    """Path to the local mocks file (env override, else release-agent/mocks.local.yaml)."""
    return os.environ.get("RELEASE_AGENT_MOCKS") or os.path.join(_ROOT, "mocks.local.yaml")


def load_mocks(path: str | None = None) -> dict:
    """Load the local mocks map keyed '<phase>.<step>'. Missing file → {} (the
    normal case for CI and any run without a personal mocks file)."""
    p = path or mocks_path()
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    raw = doc.get("mocks", doc) if isinstance(doc, dict) else {}
    return {k: v for k, v in raw.items()
            if isinstance(v, dict) and k != "version"}


def stepresult_for(mocks: dict, phase_id: str, step_id: str):
    """Return the mocked StepResult for a step, or None if it isn't mocked.

    done  → StepResult(ok=True,  ...)  → the engine records it complete.
    blocked → StepResult(ok=False, ...) → the engine holds it for the owner.
    """
    spec = mocks.get(f"{phase_id}.{step_id}")
    if not isinstance(spec, dict):
        return None
    outcome = str(spec.get("outcome", "")).strip().lower()
    if outcome in _DONE:
        note = spec.get("note") or f"[MOCK] {phase_id}.{step_id} forced pass"
        return StepResult(ok=True, action=str(note), by="mock")
    if outcome in _BLOCK:
        reason = spec.get("reason") or spec.get("note") or f"[MOCK] {phase_id}.{step_id} forced block"
        return StepResult(ok=False, action=str(reason), by="mock")
    return None


def readiness_result(mocks: dict, item_id: str):
    """Return (status, message) for a mocked readiness AUTO item, or None. Lets a
    local test clear/fail the entry gate without the real ADO / config checks —
    key `readiness.<item>` with `outcome: pass|fail` (+ optional `detail`)."""
    spec = mocks.get(f"readiness.{item_id}")
    if not isinstance(spec, dict):
        return None
    outcome = str(spec.get("outcome", "")).strip().lower()
    detail = spec.get("detail") or spec.get("note") or spec.get("message")
    if outcome in _DONE:
        return "pass", detail or f"[MOCK] readiness.{item_id} forced pass"
    if outcome in _BLOCK or outcome == "fail":
        return "fail", detail or f"[MOCK] readiness.{item_id} forced fail"
    return None
