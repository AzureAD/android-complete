"""Step: `clone_plans_broker` — build this release's Broker test plan (Phase 3, bug_bash).

Instead of ADO's "Copy Test Plan" (which reproduces the master's whole 45-suite tree), this
creates a fresh plan "Android Monthly Release - <Mon YYYY>" with exactly THREE FLAT top-level
suites:
  • "Manual Tests (Android Broker)" — a static suite of the manual-broker cases (the manual
    bug-bash set), pinned to the two flight configs (ECS + LocalFlights);
  • "Manual Tests (Native Auth)" — a single dynamic (tag-query) suite, so its cases show
    directly (no extra folder level);
  • "UI Automation (Android Broker)" — a static suite of all distinct UI-automation cases.
All cases are REFERENCED (shared, not duplicated) — the classic Test Suite Clone is
deliberately avoided because it COPIES the case work items. Flat = easy to track; downstream
steps find the Broker suite by name → drop-in.

Identity lives in the release resource registry, separately from step completion.
Re-entry validates/reuses the bound plan; lost metadata triggers discovery, not a
blind create. Ambiguous/partial/uncertain work blocks for owner recovery.

Mock knobs (mocks.local.yaml / tests):
  plan_id     : pretend the build already ran (this plan id) — verifies + reports done.
  clone_id    : the id the create should "return" (skip the live plan build).
  fail        : a detail string → force a Blocked (simulate an API/auth failure).
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import testplans as T
from tools import broker_plans as B

ID = "clone_plans_broker"
KIND = "agent"

MOCKABLE = {
    "name": {"kind": "input", "desc": "Override the destination plan name (e.g. a 'TEST ...' name for a safe live run)."},
    "plan_id": {"kind": "input", "desc": "Pretend the build already produced this plan id (idempotency test)."},
    "clone_id": {"kind": "input", "desc": "Id the create should return (skip the live plan build)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail (simulate an API failure)."},
}


def _links(plan_id):
    return [{"name": f"Broker test plan {plan_id}", "url": T.plan_web_url(plan_id)}]


def record_plan(state, plan_id, name):
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data.update(plan_id=plan_id, plan_name=name)
    resource = state.resources.get(B.RESOURCE) or {}
    step.data["ui_suite_id"] = resource.get("ui_suite_id")
    state.set_step("bug_bash", ID, step)


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_broker: {fail}")

    dest = mock_input("name", MISSING)
    if dest is MISSING:
        dest = T.broker_plan_name(state.release_id)
    step = state.get_step("bug_bash", ID)

    # Explicit offline mocks do not acquire resources or call external APIs.
    injected = mock_input("plan_id", MISSING)
    if injected is not MISSING:
        record_plan(state, injected, dest)
        return Done(f"Broker test plan already built for {state.release_id}: "
                    f"'{dest}' (plan {injected}).", links=_links(injected))
    clone_id = mock_input("clone_id", MISSING)
    if clone_id is MISSING:
        record = state.resources.setdefault(B.RESOURCE, {})
        if not isinstance(record, dict):
            return Blocked("Invalid Broker resource record; owner recovery required")
        try:
            from steps.build_verify._common import latest_rc
            ok, clone_id, detail = B.ensure_plan(
                state.release_id, dest, record, state.checkpoint,
                stored_id=(step.data or {}).get("plan_id", B.MISSING_ID), rc=latest_rc(state))
        except ValueError as exc:
            return Blocked(f"clone_plans_broker: {exc}")
        if not ok:
            return Blocked(
                f"clone_plans_broker: {detail}", links=_links(clone_id) if clone_id else [])
    record_plan(state, clone_id, dest)
    return Done(
        f"Broker test plan ready: '{dest}' (plan {clone_id}) — three flat suites "
        f"('{T.BROKER_MANUAL_SUITE_NAME}', '{T.BROKER_NATIVE_AUTH_SUITE_NAME}', "
        f"'{T.BROKER_UI_SUITE_NAME}'), referencing existing test cases.",
        links=_links(clone_id))


run = legacy_run(build)
