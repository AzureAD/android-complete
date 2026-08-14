"""Step: `cron` — verify the Calendar Checker pipeline is scheduled (Phase 0, S10).

Confirms pipeline 3038 is scheduled AND firing by finding a recent `schedule`-reason
run within the staleness window. Passes if fresh; BLOCKS (fix + rerun, or skip) if
there's no scheduled run or it's stale. Deterministic (az CLI) → an `agent` step the
engine runs in-process; a dry-run simulates.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from orchestrator.phase_config import load_phase_config
from steps.lib.agent import legacy_run

ID = "cron"
KIND = "agent"


def _iso_age_days(iso: str):
    """Whole days between an ISO-8601 timestamp and now (UTC), or None if unparseable."""
    from datetime import datetime, timezone
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def build(state):
    cfg = load_phase_config("preflight").get("cron", {})
    name = cfg.get("name", "Calendar Checker")
    if state.dry_run:
        return Done(f"[dry-run] Would verify '{name}' (pipeline {cfg.get('pipeline_id')}) has a "
                    f"recent scheduled run.")
    if not all(cfg.get(k) for k in ("pipeline_id", "org", "project")):
        return Blocked("cron: incomplete configuration")
    from tools.checks import latest_scheduled_build
    ok, run_, detail = latest_scheduled_build(cfg["org"], cfg["project"], cfg["pipeline_id"])
    if not ok:
        return Blocked(f"cron: could not read build history — {detail}")
    if not run_:
        return Blocked(
            f"{name}: no scheduled run found in recent history — the cron may be "
            f"disabled. Investigate, then rerun this step (or skip to override).")
    age = _iso_age_days(run_.get("queueTime"))
    max_stale = cfg.get("max_staleness_days", 2)
    when = (run_.get("queueTime") or "")[:16]
    if age is not None and age > max_stale:
        return Blocked(
            f"{name}: last scheduled run was {when} ({age}d ago) — stale (> {max_stale}d). "
            f"The schedule may be broken. Fix + rerun this step, or skip to override.")
    return Done(
        f"{name} is scheduled and firing — last scheduled run {when} ({run_.get('result')}).")


run = legacy_run(build)
