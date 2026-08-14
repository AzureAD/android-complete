"""Lockdown / CCOA overlap check — the recorder seam (Phase-0 step `lockdown`, S1).

The step's logic now lives in `steps/preflight/lockdown.py` (one home): the gather
description (`build`), the deterministic overlap rule (`overlapping_periods`), and
the decision (`decide`). This module is the thin CLI recorder: the skill scrapes
the AAD-gated CCOA page and passes the periods here as JSON; `check-lockdown`
decides overlap DETERMINISTICALLY (not the LLM) and records the step result:
  * no Production-env CCOA overlaps the release window -> step passes.
  * one or more overlap -> step holds for the owner (who shifts CCD via set-ccd).

`overlapping_periods` is re-exported so existing imports keep working.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from steps.preflight.lockdown import overlapping_periods, decide  # noqa: F401 (re-export)


def cmd_check_lockdown(args):
    st = C.load_state(args.runs_root, args.release)
    if not st.ccd:
        print("No CCD set for this release — cannot compute the release window.")
        return 1
    try:
        raw = _json.loads(args.periods_json or "[]")
    except ValueError:
        print("Could not parse --periods-json (expected a JSON array).")
        return 1

    status, detail = decide(st, raw)
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    orch.record_scout_step("preflight", "lockdown", status, detail)
    C.save_state(orch.state, args.runs_root, args.release)
    tag = "ok" if status == "pass" else "attention"
    lead = "Lockdown check" if status == "pass" else "Lockdown overlap"
    C.emit(args.runs_root, args.release, f"[{tag}] {lead}: {detail}", kind="lockdown")
    return 0


def register(sub):
    cl = sub.add_parser("check-lockdown",
                        help="Decide CCOA lockdown overlap from scraped periods and record the step")
    cl.add_argument("--release", required=True)
    cl.add_argument("--periods-json", required=True,
                    help='JSON array of {name, environment, start, end} (dates YYYY-MM-DD, UTC)')
    cl.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    cl.set_defaults(func=cmd_check_lockdown)
