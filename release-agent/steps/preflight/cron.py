"""Step: `cron` — verify the Calendar Checker pipeline is scheduled (Phase 0, S10).

Confirms pipeline 3038 is scheduled AND firing by finding a recent `schedule`-reason
run within the staleness window. Live ADO evidence is aged against trusted wall-clock
UTC, never a simulated release date. Injected mock evidence intentionally uses the
simulation clock. Passes if fresh; BLOCKS if there's no scheduled run or it's stale.
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
                 "REAL staleness logic runs against the simulation clock — no build-history read."),
    },
}


def _trusted_utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


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
        observed_at = context.clock.utc()
    elif not all(cfg.get(k) for k in ("pipeline_id", "org", "project")):
        return Blocked("cron: incomplete configuration")
    else:
        ok, run_, detail = context.services.pipelines.latest_scheduled_build(cfg["org"], cfg["project"], cfg["pipeline_id"])
        if not ok:
            return Blocked(f"cron: could not read build history — {detail}")
        # Provider freshness is a present-time fact. A test may advance the logical
        # release date with --as-of, but that must not make today's live run look stale.
        observed_at = _trusted_utc_now()
    if not run_:
        return Blocked(
            f"{name}: no scheduled run found in recent history — the cron may be "
            f"disabled. Investigate, then rerun this step (or skip to override).")
    age = _iso_age_days(run_.get("queueTime"), observed_at)
    max_stale = cfg.get("max_staleness_days", 2)
    when = (run_.get("queueTime") or "")[:16]
    if age is not None and age > max_stale:
        return Blocked(
            f"{name}: last scheduled run was {when} ({age}d ago) — stale (> {max_stale}d). "
            f"The schedule may be broken. Fix + rerun this step, or skip to override.")
    return Done(
        f"{name} is scheduled and firing — last scheduled run {when} ({run_.get('result')}).")
