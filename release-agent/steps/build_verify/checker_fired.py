"""Step: `checker_fired` — verify the Code Complete Calendar Checker fired the release
(Phase 2, build_verify).

The checker (def 3038) runs DAILY as a cron. Every run has a "Trigger Monthly Release"
job, but it is SKIPPED on ordinary days and only runs (succeeds) on the actual Code
Complete Day — when it launches the Release Orchestrator. This step scans the month's
checker runs (newest first) and confirms one has a SUCCEEDED "Trigger Monthly Release"
job. Read-only (`az`) → an `agent` step. Blocks if none triggered (expected before Code
Complete Day; investigate after); auth failures block with a `run az login` hint.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from steps.build_verify import _common as K

ID = "checker_fired"
KIND = "agent"

CONFIG = {
    "org": K.ORG, "project": K.PROJECT, "def_id": K.CHECKER_DEF,
    # "Trigger Monthly Release" is a JOB/PHASE (not a stage) — skipped daily, runs on CCD.
    "trigger_job": "Trigger Monthly Release",
    "max_scan": 25,                              # cap timeline reads (daily cron)
}

MOCKABLE = {
    # Inject the resolved trigger outcome to skip the live scan:
    #   {run:{id,queueTime}, result:"succeeded"|"skipped"|...}  or null for "none fired".
    "triggering": {"kind": "input",
                   "desc": "Inject {run:{id,queueTime}, result:<trigger-job result>} or null for 'none fired'."},
}


def build(state):
    cfg = CONFIG
    month = state.release_id
    job = cfg["trigger_job"]
    from tools import pipelines as P

    injected = mock_input("triggering", MISSING)
    if injected is not MISSING:
        if not injected:
            return Blocked(
                f"No Code Complete Checker run triggered the release in {month} "
                f"(no run with a succeeded '{job}' job).")
        return _verdict(state, injected.get("run") or {}, injected.get("result"), job)

    ok, runs, detail = P.find_checker_runs(cfg["org"], cfg["project"], cfg["def_id"], month)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return Blocked(f"checker_fired: could not read checker runs ({detail}){hint}.")
    if not runs:
        return Blocked(
            f"Code Complete Checker (def {cfg['def_id']}) has no run in {month} — the "
            f"release may not have been kicked off. Check the checker's schedule.")

    # Scan newest-first: the trigger job is 'skipped' most days, 'succeeded' on the CCD.
    last = None
    read_err = None
    for run in runs[:cfg["max_scan"]]:
        ok2, recs, detail2 = P.get_timeline(cfg["org"], cfg["project"], run.get("id"))
        if not ok2:
            read_err = detail2            # remember why a read failed
            continue
        rec = P.named_record(recs, job)
        if rec is not None and rec.get("result") == "succeeded":
            return _verdict(state, run, "succeeded", job)
        last = run
    # If we matched nothing AND some timeline read failed, don't misdiagnose as
    # "not triggered" — surface the read failure (with the az-login hint on auth).
    if last is None and read_err is not None:
        hint = " — run `az login`" if str(read_err).startswith("AUTH") else ""
        return Blocked(f"checker_fired: could not read checker run timelines ({read_err}){hint}.")
    ref = (f" (latest checked: run {last.get('id')} "
           f"{(last.get('queueTime') or '')[:16]})") if last else ""
    return Blocked(
        f"No Code Complete Checker run in {month} has a succeeded '{job}' job — the "
        f"release doesn't appear to have been triggered yet{ref}. If Code Complete Day "
        f"hasn't arrived this is expected; otherwise investigate the checker.")


def _verdict(state, run, result, job):
    bid = (run or {}).get("id")
    when = ((run or {}).get("queueTime") or "")[:16]
    links = K.links_for(bid, "Code Complete Checker run")
    if result != "succeeded":
        return Blocked(
            f"Code Complete Checker '{job}' did not succeed (result={result}) in run "
            f"{bid} ({when}) — the orchestrator was not launched.{K.UNBLOCK_HELP}", links=links)
    K.stash_checker(state, bid, when)
    return Done(
        f"Code Complete Checker fired the release — run {bid} ({when}), '{job}' succeeded.",
        links=links)


run = legacy_run(build)
