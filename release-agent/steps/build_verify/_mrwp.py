"""Shared verification body for the ECS and Local MRWP steps."""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked, InProgress
from steps.build_verify._common import (
    ORG, PROJECT, UNBLOCK_HELP, links_for, stash_mrwp, valid_counts,
)
from steps.lib.mockctx import mock_input, MISSING
from tools import pipelines as P
from tools.pipelines import ORCHESTRATOR_DEF


def verify_mrwp(state, provider):
    """Shared body for the mrwp_ecs / mrwp_local steps. `provider` is 'ECS' or 'Local'.

    Resolves this release's MRWP (def 2519) run for the provider — from the orchestrator
    run's RC-<provider>=<id> tag, or a log-parse fallback — then applies the release
    stage-completion rule (every stage must have executed; skipped/canceled/pending =
    block) and attaches the Test-tab summary. Uses the step's mock knobs when present:
      mrwp_id : inject the MRWP build id (skip the orchestrator lookup)
      stages  : inject the stage list [{name,state,result}]
      tests   : inject the test summary {total,passed,failed[,runs]}
    Returns a Done/Blocked outcome.
    """
    label = f"MRWP {provider}"
    # 1) resolve the MRWP build id for this provider
    rc_num = mock_input("rc", MISSING)
    rc_num = rc_num if rc_num is not MISSING else None
    mid = mock_input("mrwp_id", MISSING)
    if mid is MISSING:
        ok, run, detail = P.find_orchestrator_run(ORG, PROJECT, ORCHESTRATOR_DEF, state.release_id)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read orchestrator run ({detail}){hint}.")
        if not run:
            return Blocked(
                f"{label}: no orchestrator run found for {state.release_id} — can't locate "
                f"the RC-testing runs. Verify the orchestrator first.")
        ok2, ids, detail2, source = P.mrwp_run_ids(ORG, PROJECT, run)
        if not ok2:
            hint = " — run `az login`" if str(detail2).startswith("AUTH") else ""
            return Blocked(
                f"{label}: could not resolve the MRWP run id ({detail2}){hint}.",
                links=links_for(run.get("id"), "Release Orchestrator run"))
        mid = ids.get(provider)
        rc_num = ids.get("rc")                    # authoritative RC iteration from the tag
        if not mid:
            return Blocked(f"{label}: orchestrator didn't record a {provider} RC-testing run.")

    links = links_for(mid, f"{label} run")

    # 1.5) overall run status — an in-flight run is NOT a failure. If the MRWP run is
    # still notStarted/inProgress, its un-run stages just haven't run YET; hold the step
    # as in-flight and let the 30-min poller re-evaluate when it completes, instead of
    # false-blocking it as an aborted release. `build_status` mock drives this in sim/tests;
    # when stages are injected (no live call) we assume the run is complete.
    bstatus = mock_input("build_status", MISSING)
    if bstatus is MISSING and mock_input("stages", MISSING) is MISSING:
        ok_s, bstatus, _bres, _bdetail = P.get_build_status(ORG, PROJECT, mid)
        if not ok_s:
            return Blocked(f"{label}: could not read build status ({_bdetail}).", links=links)
    if bstatus is None:
        return Blocked(f"{label}: build status is unknown; retry verification.", links=links)
    if bstatus not in (MISSING, None) and bstatus != "completed":
        return InProgress(
            f"{label} run {mid} is still running (status: {bstatus}) — Scout is polling "
            f"every 30 min and will re-evaluate the RC when it completes.", links=links)

    # 2) stage-completion rule
    stages = mock_input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = P.get_stages(ORG, PROJECT, mid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read stages for run {mid} ({detail}){hint}.", links=links)
    comp = P.stage_completion(stages)
    if not comp["complete"]:
        never = ", ".join(n for n in comp["never_ran"] if n) or "(unknown)"
        return Blocked(
            f"{label} run {mid} did NOT run to completion — {len(comp['never_ran'])} stage(s) "
            f"never ran (pending/skipped/canceled): {never}. A stage that never ran means the "
            f"pipeline aborted partway.{UNBLOCK_HELP}", links=links)

    # 3) test summary (missing coverage holds collection; evaluated failures do not)
    tests = mock_input("tests", MISSING)
    tests_injected = tests is not MISSING
    tests_error = None
    if not tests_injected:
        ok, tests, detail = P.get_test_summary(ORG, PROJECT, mid)
        if not ok:
            tests = None
            tests_error = detail or "could not fetch complete test summary"
    tnote = ""
    if tests:
        tnote = f" Tests: {tests['passed']}/{tests['total']} passed, {tests['failed']} failed."
    stage_note = f"{comp['ran']}/{comp['total']} stages ran"
    extras = []
    if comp["failed"]:
        extras.append(f"{len(comp['failed'])} red")
    if comp["yellow"]:
        extras.append(f"{len(comp['yellow'])} yellow")
    extra = f" ({', '.join(extras)} — triaged later)" if extras else ""

    # Full details come from the same read as the counts; never race a second fetch.
    suites = mock_input("suites", MISSING)
    if suites is MISSING:
        suites = (tests or {}).get("failed_suites")

    # 5) stash the FULL per-provider snapshot into the RC iteration (authoritative rc from tag).
    stash_mrwp(state, provider, {
        "run_id": mid, "complete": comp["complete"], "ran": comp["ran"],
        "total": comp["total"], "failed_stages": comp["failed"],
        "yellow_stages": comp["yellow"], "never_ran": comp["never_ran"],
        "tests": tests, "tests_error": tests_error,
        "failed_suites": suites,
    }, rc=rc_num)
    if tests_error:
        return Blocked(f"{label}: test summary unavailable ({tests_error}); retry verification.",
                       links=links)
    ui = ((tests or {}).get("categories") or {}).get("ui")
    if not valid_counts(ui):
        return Blocked(f"{label}: missing or invalid non-zero UI results; retry this verification "
                       "after the Test tab is populated. The report cannot evaluate absent data.",
                       links=links)
    return Done(
        f"{label} run {mid} ran to completion — {stage_note}{extra}.{tnote}", links=links)
