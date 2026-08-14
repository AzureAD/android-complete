"""Step: `lockdown` — detect CCOA lockdown / holiday overlap (Phase 0, S1).

This is the "gather-then-decide" scout step. The CCOA "No-Fly Zones" source is an
AAD-gated web app the engine can't read, so the flow is two hops:

  1. GATHER (skill): `build(state)` returns a NeedsSkill describing the browser
     scrape — the URL, the release window, and what rows to extract. The skill
     scrapes the authenticated page and produces the periods JSON.
  2. DECIDE (engine): the skill hands the periods to `check-lockdown`, which calls
     `decide()` here — a DETERMINISTIC overlap rule (not the LLM) — and records the
     step: no Production overlap → pass; one or more → hold for the owner (attention).

Both the gather description and the decision live here, so `commands/lockdown.py`
is just the thin recorder seam (like `record-step` is for `notice`).
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.phase_config import load_phase_config

ID = "lockdown"
KIND = "scout"


def _cfg() -> dict:
    return load_phase_config("preflight", "lockdown")


def release_window(state, cfg: dict | None = None):
    """(win_start, win_end) dates for the release window (CCD-7 .. CCD+14 by default)."""
    cfg = cfg or _cfg()
    ccd = schedule.parse_date(state.ccd)
    ws = schedule.anchor_date(ccd, cfg.get("window_start_anchor", "CCD-7"))
    we = schedule.anchor_date(ccd, cfg.get("window_end_anchor", "CCD+14"))
    return ws, we


def overlapping_periods(win_start, win_end, periods, blocking_env="Production"):
    """Pure overlap rule. `periods` is a list of dicts with keys name, environment,
    start (date), end (date). Returns those whose Environment contains
    `blocking_env` (case-insensitive) AND whose [start,end] intersects the window
    [win_start,win_end]. Banner-only advisories (no Production) are excluded."""
    hits = []
    for p in periods:
        if blocking_env.lower() not in (p.get("environment") or "").lower():
            continue
        s, e = p.get("start"), p.get("end")
        if s is None or e is None:
            continue
        if s <= win_end and e >= win_start:      # ranges intersect
            hits.append(p)
    return hits


def decide(state, raw_periods, cfg: dict | None = None):
    """Deterministic overlap decision from scraped periods. Returns (status, detail)
    where status is 'pass' (no Production overlap) or 'attention' (holds for owner)."""
    cfg = cfg or _cfg()
    blocking_env = cfg.get("blocking_environment", "Production")
    win_start, win_end = release_window(state, cfg)

    periods = []
    for p in (raw_periods if isinstance(raw_periods, list) else []):
        s, e = schedule.parse_date(p.get("start")), schedule.parse_date(p.get("end"))
        periods.append({"name": p.get("name", "?"),
                        "environment": p.get("environment", ""), "start": s, "end": e})

    hits = overlapping_periods(win_start, win_end, periods, blocking_env)
    window = f"{win_start.isoformat()}..{win_end.isoformat()}"
    if not hits:
        return "pass", (f"No {blocking_env} CCOA lockdown overlaps the release window "
                        f"({window}). Checked {len(periods)} period(s).")
    listed = "; ".join(
        f"{h['name']} ({h['start'].isoformat()}..{h['end'].isoformat()})" for h in hits)
    return "attention", (f"{len(hits)} {blocking_env} CCOA lockdown(s) overlap the release "
                         f"window ({window}): {listed}. Shift CCD past the lockdown "
                         f"(set-ccd) if needed.")


def build(state):
    """Round 1 (GATHER): return a NeedsSkill describing the CCOA scrape + the
    follow-up decider. The skill scrapes the page, then runs the `check-lockdown`
    command in `payload.followup_command` with the scraped periods, which calls
    `decide()` and records the step. Blocked if the release has no CCD."""
    if not state.ccd:
        return Blocked("no CCD set for this release — cannot compute the release window")

    cfg = _cfg()
    win_start, win_end = release_window(state, cfg)
    window = f"{win_start.isoformat()}..{win_end.isoformat()}"
    blocking_env = cfg.get("blocking_environment", "Production")

    return NeedsSkill(
        tool="check-lockdown",                 # follow-up engine command (the decider/recorder)
        payload={
            "release": state.release_id,
            # The skill fills periods_json from the scrape, then runs followup_command.
            "periods_json": None,
            "followup_command": (f"check-lockdown --release {state.release_id} "
                                 f"--periods-json '<scraped-json>'"),
            "_gather": {
                "url": cfg.get("url"),
                "window": window,
                "blocking_environment": blocking_env,
                "instructions": (
                    "Browser-scrape the 'Upcoming CCOA periods' and current-year "
                    "'Past NoFly Zones' tables (AAD SSO, no password). Build a JSON "
                    "array of {name, environment, start:YYYY-MM-DD, end:YYYY-MM-DD} "
                    "(UTC) — the engine keeps only Production-env periods and decides "
                    "overlap deterministically."),
            },
        },
        record_as=ID,
        summary=f"Scrape CCOA No-Fly Zones, then check-lockdown decides overlap vs {window}",
        dry_run=state.dry_run,
        note="awaiting CCOA scrape + deterministic overlap decision",
    )
