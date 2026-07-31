"""Lockdown / CCOA overlap check (Phase-0 step `lockdown`, S1).

The CCOA "No-Fly Zones" source is an AAD-gated web app, so the SKILL scrapes it
via the authenticated browser and passes the periods here as JSON. This command
decides overlap DETERMINISTICALLY (not the LLM) and records the step result:
  * no Production-env CCOA overlaps the release window -> step passes.
  * one or more overlap -> step holds for the owner (who shifts CCD via set-ccd).
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from orchestrator import schedule


def _load_lockdown_cfg() -> dict:
    from orchestrator.phase_config import load_phase_config
    return load_phase_config("preflight", "lockdown")


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


def cmd_check_lockdown(args):
    st = C.load_state(args.runs_root, args.release)
    if not st.ccd:
        print("No CCD set for this release — cannot compute the release window.")
        return 1
    cfg = _load_lockdown_cfg()
    blocking_env = cfg.get("blocking_environment", "Production")
    ccd = schedule.parse_date(st.ccd)
    win_start = schedule.anchor_date(ccd, cfg.get("window_start_anchor", "CCD-7"))
    win_end = schedule.anchor_date(ccd, cfg.get("window_end_anchor", "CCD+14"))

    try:
        raw = _json.loads(args.periods_json or "[]")
    except ValueError:
        print("Could not parse --periods-json (expected a JSON array).")
        return 1
    periods = []
    for p in raw if isinstance(raw, list) else []:
        s, e = schedule.parse_date(p.get("start")), schedule.parse_date(p.get("end"))
        periods.append({"name": p.get("name", "?"),
                        "environment": p.get("environment", ""), "start": s, "end": e})

    hits = overlapping_periods(win_start, win_end, periods, blocking_env)
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    window = f"{win_start.isoformat()}..{win_end.isoformat()}"
    if not hits:
        detail = (f"No {blocking_env} CCOA lockdown overlaps the release window "
                  f"({window}). Checked {len(periods)} period(s).")
        orch.record_scout_step("preflight", "lockdown", "pass", detail)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[ok] Lockdown check: {detail}", kind="lockdown")
        return 0

    listed = "; ".join(
        f"{h['name']} ({h['start'].isoformat()}..{h['end'].isoformat()})" for h in hits)
    detail = (f"{len(hits)} {blocking_env} CCOA lockdown(s) overlap the release window "
              f"({window}): {listed}. Shift CCD past the lockdown (set-ccd) if needed.")
    orch.record_scout_step("preflight", "lockdown", "attention", detail)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[attention] Lockdown overlap — {detail}", kind="lockdown")
    return 0


def register(sub):
    cl = sub.add_parser("check-lockdown",
                        help="Decide CCOA lockdown overlap from scraped periods and record the step")
    cl.add_argument("--release", required=True)
    cl.add_argument("--periods-json", required=True,
                    help='JSON array of {name, environment, start, end} (dates YYYY-MM-DD, UTC)')
    cl.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    cl.set_defaults(func=cmd_check_lockdown)
