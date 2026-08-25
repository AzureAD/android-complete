"""Step: `ui_test_status` — fill the release plan's UI Automation results from the RC pipelines
(Phase 3, bug_bash; runs right after `distribute_tests`).

Phase 2 verification records one or more RC iterations (`state.pipeline_runs.rcs[]`), each with
an ECS and a Local MRWP build. Phase-3 step 1 (`clone_plans_broker`) built the release plan with
a flat "UI Automation (Android Broker)" suite. This step reads the UI-automation test results
across ALL those RC builds and fills that suite's outcomes.

A UI test can run many times — across RC iterations, retries, flight providers (ECS / Local),
and MSAL variants (PROD-MSAL / RC-MSAL) — with different outcomes each time. Each MRWP build is
one flight (ECS or Local, from templateParameters.flightProvider); its runs split into a
PROD-MSAL and an RC-MSAL variant (from the run name). Those (flight, variant) pairs map 1:1 to
the suite's four flight-configs, so every (case, config) test point takes the outcome of its
MATCHING run(s): PASSED if it passed there in >=1 run; FAILED if it really ran but never passed;
NotApplicable if it was only skipped / never ran for that flight+variant.

The join from a pipeline result to a plan test case is the case id embedded in the automated
test name (`test_<caseId>_...`) — see `pipelines.ui_automation_verdicts`.

Depends on `clone_plans_broker` (the plan / UI suite) and the Phase-2 RC runs. Blocks if the
plan hasn't been cloned or no RC runs are recorded. Idempotent — a re-run re-fills (overwrites)
the outcomes from the current RC runs.

Mock knobs (mocks.local.yaml / tests):
  build_ids : inject the RC MRWP build ids [..] (skip reading state.pipeline_runs).
  verdicts  : inject the per-case verdicts {case_id: 'Passed'|'Failed'} (skip ADO pipelines).
  fail      : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import testplans as T
from tools import pipelines as P

ID = "ui_test_status"
KIND = "agent"

MOCKABLE = {
    "build_ids": {"kind": "input", "desc": "Inject the RC MRWP build ids [..] (skip state.pipeline_runs)."},
    "verdicts": {"kind": "input", "desc": "Inject per-config verdicts {case_id: {(flight,variant): outcome}} (skip ADO)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _rc_build_ids(state):
    """Every MRWP build id recorded by Phase-2 verification — the ecs + local run of each RC
    iteration in state.pipeline_runs.rcs[] — de-duplicated, order preserved."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    out, seen = [], set()
    for rc in rcs:
        for prov in ("ecs", "local"):
            rid = (rc.get(prov) or {}).get("run_id")
            if rid and rid not in seen:
                seen.add(rid)
                out.append(rid)
    return out


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"ui_test_status: {fail}")

    plan_id = _broker_plan_id(state)
    if not plan_id:
        return Blocked("ui_test_status: the Broker test plan hasn't been cloned yet "
                       "(clone_plans_broker) — run that first so the UI Automation suite exists.")

    verdicts = mock_input("verdicts", MISSING)
    if verdicts is MISSING:
        build_ids = mock_input("build_ids", MISSING)
        if build_ids is MISSING:
            build_ids = _rc_build_ids(state)
        if not build_ids:
            return Blocked("ui_test_status: no RC pipeline runs recorded yet (Phase-2 build "
                           "verification) — nothing to fill the UI Automation results from.")
        ok, verdicts, d = P.ui_automation_verdicts(
            P.ENGINEERING_ORG, P.ENGINEERING_PROJECT, build_ids)
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"ui_test_status: couldn't read UI results from the RC pipelines ({d}){hint}.")

    ok, summ, d = T.fill_ui_automation_results(plan_id, verdicts)
    if not ok:
        return Blocked(f"ui_test_status: couldn't fill the UI Automation results ({d}).")

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["plan_id"] = plan_id
    step.data["summary"] = summ
    state.set_step("bug_bash", ID, step)

    p, f, na = summ.get("set_passed", 0), summ.get("set_failed", 0), summ.get("set_not_applicable", 0)
    return Done(
        f"Filled UI Automation results in plan {plan_id}: {p + f + na} test points "
        f"({p} Passed, {f} Failed, {na} N/A) across {summ.get('cases_touched', 0)} cases. "
        f"Each config-point takes its matching flight+variant run's result (pass if it "
        f"succeeded in \u22651 run; skipped \u2192 N/A).",
        links=[{"name": f"UI Automation results (plan {plan_id})", "url": T.plan_web_url(plan_id)}])


run = legacy_run(build)
