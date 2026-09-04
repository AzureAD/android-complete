"""Step: `distribute_tests` — evenly distribute the manual bug-bash tests across the
eligible team (Phase 3, bug_bash).

READ-ONLY PREVIEW. This step computes the distribution (combined Broker + Authenticator
test sets, even split preserving default assignments) and STORES the resulting plan on the
step (`data.plan`), reporting a summary. It does NOT write `System.AssignedTo` — applying
the plan (which mutates the shared test-case work items) is a separate, explicit action:
`distribute-tests --release <id> --apply`.

Eligible = roster DL minus the always-excluded people, the release owner, and the current
on-call engineer (OCE). The OCE's team id comes from readiness.yaml (single source with the
entry gate); the OCE identity is resolved via ICM by the skill and passed in as the `oce`
input. Owner = state.owner_email.

The Authenticator set EXCLUDES cases already automated by this release's auth ECS UI-test run
— resolved EMPIRICALLY from the run (by test-case id, via `pipelines.auth_ui_case_outcomes`
off `state.rcs[-1].auth.test`), NOT from the cross-platform 'Automated' tag (iOS owns that
tag, so a case automated only on Android must not carry it). Both passed and failed automated
cases are dropped from the manual split — failures are triaged by the owner via
`ui_test_status`, not by a manual tester.

Depends on the two clone steps: the Broker plan id is read from clone_plans_broker's
stashed data (falls back to the master plan). If the Broker plan hasn't been cloned yet,
the step blocks.

Mock knobs (mocks.local.yaml / tests):
  oce            : the on-call engineer's identifier to exclude (skill resolves via ICM).
  roster         : inject the team roster [{name, upn}] (skip Graph).
  broker_cases   : inject the Broker cases [{id, assignee}] (skip ADO).
  auth_cases     : inject the Authenticator cases [{id, assignee}] (skip ADO).
  auth_automated : inject the automated auth case ids to exclude [..] (skip the auth run read).
  fail           : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import distribution as D
from tools import pipelines as P

ID = "distribute_tests"
KIND = "agent"

MOCKABLE = {
    "oce": {"kind": "input", "desc": "On-call engineer identifier to exclude (skill resolves via ICM)."},
    "roster": {"kind": "input", "desc": "Inject the team roster [{name, upn}] (skip Graph)."},
    "broker_cases": {"kind": "input", "desc": "Inject Broker cases [{id, assignee}] (skip ADO)."},
    "auth_cases": {"kind": "input", "desc": "Inject Authenticator cases [{id, assignee}] (skip ADO)."},
    "auth_automated": {"kind": "input", "desc": "Inject the automated auth case ids to exclude [..] (skip the auth ECS run read)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_test_build_id(state):
    """The auth ECS post-build UI-test build id captured by Phase-2 auth_ecs
    (state.pipeline_runs.rcs[-1].auth.test.run_id), or None."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    if not rcs:
        return None
    return (((rcs[-1].get("auth") or {}).get("test") or {}) or {}).get("run_id")


def _auth_automated_ids(state):
    """Case ids already automated by this release's auth ECS UI-test run — excluded from the
    MANUAL distribution. Read EMPIRICALLY from the run (via pipelines.auth_ui_case_outcomes),
    NOT from the shared 'Automated' tag (that tag is cross-platform; iOS owns it). Passed and
    Failed automated cases are both excluded — failures are triaged by the owner via
    ui_test_status, not by a manual tester. Offline: inject via the `auth_automated` knob.
    Returns a set (empty when no auth run has been captured yet)."""
    injected = mock_input("auth_automated", MISSING)
    if injected is not MISSING:
        return {int(i) for i in (injected or [])}
    bid = _auth_test_build_id(state)
    if not bid:
        return set()
    ok, outcomes, _ = P.auth_ui_case_outcomes(bid)
    return {int(k) for k in outcomes} if ok else set()


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"distribute_tests: {fail}")

    cfg = D.load_config()

    # 1) the two test sets (combined)
    bcases = mock_input("broker_cases", MISSING)
    if bcases is MISSING:
        plan_id = _broker_plan_id(state)
        if not plan_id:
            return Blocked("distribute_tests: the Broker test plan hasn't been cloned yet "
                           "(clone_plans_broker) — run that first so the manual tests exist.")
        ok, sid, d = D.find_suite_id_by_name(plan_id, cfg["broker"]["suite_name"])
        if not ok or not sid:
            return Blocked(f"distribute_tests: couldn't find the '{cfg['broker']['suite_name']}' "
                           f"suite in Broker plan {plan_id} ({d or 'not found'}).")
        ok, bcases, d = D.broker_manual_cases(plan_id, sid)
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"distribute_tests: couldn't read Broker manual tests ({d}){hint}.")

    acases = mock_input("auth_cases", MISSING)
    if acases is MISSING:
        ok, acases, d = D.auth_bugbash_cases(cfg["authenticator"]["exclude_tags"])
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"distribute_tests: couldn't read Authenticator bug-bash tests ({d}){hint}.")

    # Exclude auth cases already automated by this release's auth ECS UI-test run — they don't
    # need a manual tester (passes are done; failures are triaged by the owner via
    # ui_test_status). Empirical (from the run), so no reliance on the cross-platform
    # 'Automated' tag that iOS also uses.
    auto_ids = _auth_automated_ids(state)
    auth_before = len(acases)
    if auto_ids:
        acases = [c for c in acases if int(c.get("id")) not in auto_ids]
    auth_excluded = auth_before - len(acases)

    tests = [{"id": f"B:{c['id']}", "assignee": c.get("assignee")} for c in bcases] + \
            [{"id": f"A:{c['id']}", "assignee": c.get("assignee")} for c in acases]

    # 2) eligible testers = roster - always_excluded - owner - OCE
    roster = mock_input("roster", MISSING)
    if roster is MISSING:
        ok, roster, d = D.resolve_roster(cfg["roster_group"])
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"distribute_tests: couldn't resolve the roster "
                           f"'{cfg['roster_group']}' ({d}){hint}.")
    upns = [m.get("upn") for m in roster if m.get("upn")]
    owner = state.owner_email
    oce = mock_input("oce", MISSING)
    oce = None if oce is MISSING else oce
    eligible = D.eligible_testers(upns, cfg.get("always_excluded", []), owner=owner, oce=oce)
    if not eligible:
        return Blocked("distribute_tests: no eligible testers after exclusions — check the "
                       "roster, owner, and on-call exclusions.")

    # 3) distribute (combined, even, preference-preserving)
    result = D.distribute(tests, eligible)

    # 4) store the plan for the apply step; report a preview summary
    name_by_upn = {m.get("upn"): m.get("name") for m in roster if m.get("upn")}
    counts = result["counts"]
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["plan"] = {
        "assignments": result["assignments"],      # {"B:<id>"/"A:<id>": upn}
        "counts": counts,
        "eligible": eligible,
        "owner_excluded": owner,
        "oce_excluded": oce,
        "broker_total": len(bcases),
        "auth_total": len(acases),
        "auth_excluded_automated": auth_excluded,
        "applied": False,
    }
    state.set_step("bug_bash", ID, step)

    lo, hi = (min(counts.values()), max(counts.values())) if counts else (0, 0)
    top = ", ".join(f"{name_by_upn.get(u, u)} {counts[u]}"
                    for u in sorted(eligible, key=lambda e: -counts[e])[:3])
    oce_note = f", OCE {oce}" if oce else " (OCE not resolved — pass --oce to exclude)"
    auto_note = (f" Excluded {auth_excluded} already-automated auth case(s)."
                 if auth_excluded else "")
    return Done(
        f"Distribution PREVIEW ready: {len(tests)} tests (Broker {len(bcases)} + Auth "
        f"{len(acases)}) across {len(eligible)} testers — {lo}–{hi} each "
        f"({result['kept']} kept, {result['reassigned']} reassigned).{auto_note} Excluded owner "
        f"{owner}{oce_note}. e.g. {top}. Review, then apply with "
        f"`distribute-tests --release {state.release_id} --apply`.")


run = legacy_run(build)
