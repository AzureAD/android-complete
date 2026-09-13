"""Step: `cron` — verify the Calendar Checker pipeline is scheduled (Phase 0, S10).

Confirms pipeline 3038 is scheduled AND firing by finding a recent `schedule`-reason
run within the staleness window. Passes if fresh; BLOCKS (fix + rerun, or skip) if
there's no scheduled run or it's stale. Deterministic (az CLI) → an `agent` step the
engine runs in-process.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked
from steps.lib.mockctx import MISSING
from tools.pipelines import ENGINEERING_ORG, ENGINEERING_PROJECT
from tools.coordinates import coords

ID = "cron"
KIND = "agent"
EFFECT_MODE = "read_only"

# Step config (co-located). Pipeline 3038's cron proves it's FIRING via a recent
# schedule-reason run in its build history. It's the Engineering Calendar Checker.
CONFIG = {
    "pipeline_id": coords.pipeline_def("checker"),
    "org": ENGINEERING_ORG,
    "project": ENGINEERING_PROJECT,
    "name": "Code Complete Calendar Checker",
    "max_staleness_days": 2,     # a daily cron should never be older than this
}

# Properties this step exposes to mocks.local.yaml (see `mock-spec`).
MOCKABLE = {
    "run": {
        "kind": "input",
        "desc": ("Inject the latest scheduled-build dict (or null for 'none'); the "
                 "REAL staleness logic runs on it — no build-history read."),
    },
}


def _iso_age_days(iso: str, now):
    """Whole days between an ISO-8601 timestamp and now (UTC), or None if unparseable."""
    from datetime import datetime, timezone
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).days
    except ValueError:
        return None


def build(context: StepContext):
    cfg = CONFIG
    name = cfg.get("name", "Calendar Checker")
    # Injected run (mocks.local.yaml) → run the REAL staleness logic on your data.
    injected = context.input("run", MISSING)
    if injected is not MISSING:
        run_ = injected
    elif not all(cfg.get(k) for k in ("pipeline_id", "org", "project")):
        return Blocked("cron: incomplete configuration")
    else:
        ok, run_, detail = context.services.pipelines.latest_scheduled_build(cfg["org"], cfg["project"], cfg["pipeline_id"])
        if not ok:
            return Blocked(f"cron: could not read build history — {detail}")
    if not run_:
        return Blocked(
            f"{name}: no scheduled run found in recent history — the cron may be "
            f"disabled. Investigate, then rerun this step (or skip to override).")
    age = _iso_age_days(run_.get("queueTime"), context.clock.utc())
    max_stale = cfg.get("max_staleness_days", 2)
    when = (run_.get("queueTime") or "")[:16]
    if age is not None and age > max_stale:
        return Blocked(
            f"{name}: last scheduled run was {when} ({age}d ago) — stale (> {max_stale}d). "
            f"The schedule may be broken. Fix + rerun this step, or skip to override.")
    return Done(
        f"{name} is scheduled and firing — last scheduled run {when} ({run_.get('result')}).")
