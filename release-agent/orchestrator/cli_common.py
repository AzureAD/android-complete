"""Shared CLI plumbing for the Release Orchestrator command modules.

The CLI is split into a thin assembler (`cli.py`) plus one module per command
domain under `commands/`. This module holds the pieces those command modules
share: path resolution, state/orchestrator loading, the event log, user-facing
emit (print + auto-log), and the small render helpers used when advancing.

Everything here takes explicit parameters (runs_root / release / config) rather
than the argparse namespace, so the helpers are decoupled from the parser and
easy to reuse and test.
"""
from __future__ import annotations

import os

from orchestrator.state import ReleaseState
from orchestrator.engine import Orchestrator
from orchestrator import discovery, render, schedule
from orchestrator.eventlog import EventLog
from tools import checks
import yaml as _yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                                  # release-agent/
DEFAULT_CONFIG = os.path.join(ROOT, "config", "phases.yaml")
SCHEDULE_CONFIG = os.path.join(ROOT, "config", "schedule.yaml")
REQUIREMENTS_CONFIG = os.path.join(ROOT, "config", "requirements.yaml")
# runs live OUTSIDE release-agent/, in android-complete/.release-runs (gitignored)
DEFAULT_RUNS_ROOT = os.path.join(os.path.dirname(ROOT), ".release-runs")


# ---- paths / state ----
def state_path(runs_root: str, release: str) -> str:
    return os.path.join(runs_root, release, "release-state.json")


def load_state(runs_root: str, release: str) -> ReleaseState:
    return ReleaseState.load(state_path(runs_root, release))


def save_state(st: ReleaseState, runs_root: str, release: str) -> None:
    st.save(state_path(runs_root, release))


def parse_as_of(args):
    """The simulated clock from --as-of (None ⇒ engine uses today)."""
    s = getattr(args, "as_of", None)
    return schedule.parse_date(s) if s else None


def load_orch(runs_root: str, release: str, config: str, as_of=None):
    """Load state + build an Orchestrator wired to the --as-of clock."""
    st = load_state(runs_root, release)
    return st, Orchestrator(config, st, as_of=as_of)


# ---- config ----
def ccd_source() -> dict:
    """Where CCD comes from (pipeline coords) — from config/schedule.yaml."""
    try:
        with open(SCHEDULE_CONFIG, "r", encoding="utf-8") as fh:
            return (_yaml.safe_load(fh) or {}).get("ccd_source", {}) or {}
    except OSError:
        return {}


# ---- event log / emit ----
def elog(runs_root: str, release: str) -> EventLog:
    return EventLog(runs_root, release)


def emit(runs_root: str, release: str, text: str, kind: str = "message", options=None):
    """Print a user-facing block AND auto-log it as scout output, so the log
    always captures 'what was shown' without relying on the skill/LLM to journal."""
    print(text)
    try:
        elog(runs_root, release).scout_said(text, kind=kind, options=options)
    except Exception:
        pass


# ---- advancing the loop (shared by next / approve / done) ----
TAGS = {"ran": "[ok]", "gate": "[gate]", "reminder": "[action]", "scheduled": "[scheduled]",
        "complete": "[done]", "idle": "[--]", "readiness": "[entry-gate]",
        "blocked": "[BLOCKED]", "halted": "[HALTED]"}


def log_actions(el: EventLog, actions):
    """Record engine actions as events (step_ran / gate_hold / complete / blocked)."""
    events = {
        "ran": "step_ran", "gate": "gate_hold", "reminder": "reminder_hold",
        "scheduled": "scheduled_hold", "readiness": "readiness_hold",
        "blocked": "blocked_hold", "halted": "halted_hold", "complete": "release_complete",
    }
    for a in actions:
        name = events.get(a.kind)
        if not name:
            continue
        if a.kind in ("ran", "gate", "reminder", "scheduled"):
            el.log(name, phase=a.phase, step=a.step, name=a.name)
        else:
            el.log(name)


def advance_block(actions, orch, lead=None) -> str:
    """The canonical 'what happened + new status' block for advance commands."""
    out = list(lead or [])
    for a in actions:
        out.append(f"  {TAGS.get(a.kind, '-')} {a.message}")
    out.append("\n" + render.status_view(orch.status_report()))
    return "\n".join(out)


# ---- CCD / pipeline helpers ----
def refresh_conflict(st: ReleaseState) -> bool:
    """Best-effort: re-read the pipeline override and refresh st.ccd_conflict
    (a pipeline date that differs from our stored CCD). Returns True if the state
    changed (caller should save). Silent on any read failure — never blocks."""
    if not st.ccd:
        return False
    src = ccd_source()
    if not src.get("pipeline_id"):
        return False
    ok, val, _ = checks.read_pipeline_variable(
        src["org"], src["project"], src["pipeline_id"], src["override_variable"])
    if not ok:
        return False
    conflict = schedule.pipeline_conflict(st.release_id, val, st.ccd)
    new = conflict.isoformat() if conflict else None
    if new != st.ccd_conflict:
        st.ccd_conflict = new
        return True
    return False


def write_ccd_var(src: dict, value: str):
    """Write the CCD override variable on the pipeline. Returns CheckResult."""
    return checks.set_pipeline_variable(
        src["org"], src["project"], src["pipeline_id"], src["override_variable"], value)


def resolve_release_id(runs_root: str, release):
    """Return an explicit release id or discover the active one (or None)."""
    if release:
        return release
    rel = discovery.resolve(runs_root, None).get("release")
    return rel["release_id"] if rel else None
