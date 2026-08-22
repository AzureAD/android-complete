"""Step: `clone_plans_broker` — build this release's Broker test plan (Phase 3, bug_bash).

Instead of ADO's "Copy Test Plan" (which reproduces the master's whole 45-suite tree), this
creates a fresh plan "Android Monthly Release - <Mon YYYY>" with exactly TWO top-level
folders:
  • "Manual Tests (Android Broker)" — one FLAT static suite of the manual-broker cases (the
    only broker tests the bug bash runs), pinned to the two flight configs (ECS + LocalFlights);
  • "Manual Tests (Native Auth)" — copied AS A FOLDER (its dynamic query suite preserved),
    pinned to ECS only.
Cases are referenced (shared, not duplicated). Flat Broker = easy to track; Native Auth stays
a dynamic folder so it's current. Downstream steps find the Broker suite by name → drop-in.

Idempotent: the created plan id is stashed on the step (data.plan_id). On a re-run the step
re-confirms that plan still exists and reports done WITHOUT rebuilding — so a `next` that
re-enters Phase 3 never spawns a duplicate plan.

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


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_broker: {fail}")

    dest = mock_input("name", MISSING)
    if dest is MISSING:
        dest = T.broker_plan_name(state.release_id)
    step = state.get_step("bug_bash", ID)

    # Already built? A test injects `plan_id` to assert idempotency; otherwise the stored id
    # from a prior run is re-confirmed against ADO. Either way → done, no rebuild.
    injected = mock_input("plan_id", MISSING)
    if injected is not MISSING:
        return Done(f"Broker test plan already built for {state.release_id}: "
                    f"'{dest}' (plan {injected}).", links=_links(injected))
    stored = (step.data or {}).get("plan_id")
    if stored:
        ok, info, _ = T.get_plan(stored)
        if ok and info:
            return Done(f"Broker test plan already built for {state.release_id}: "
                        f"'{info.get('name') or dest}' (plan {stored}).", links=_links(stored))
        # recorded id no longer resolves — fall through and rebuild

    # Build the plan (or take the injected clone_id offline).
    clone_id = mock_input("clone_id", MISSING)
    if clone_id is MISSING:
        ok, clone_id, detail = T.build_broker_plan(dest)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(
                f"clone_plans_broker: could not build the Broker test plan '{dest}' "
                f"from the master (#{T.BROKER_MASTER_PLAN}) ({detail}){hint}.")

    step.data = dict(step.data or {})
    step.data["plan_id"] = clone_id
    step.data["plan_name"] = dest
    state.set_step("bug_bash", ID, step)
    return Done(
        f"Built the Broker test plan '{dest}' (plan {clone_id}) — a flat "
        f"'{T.BROKER_MANUAL_SUITE_NAME}' suite plus the '{T.BROKER_NATIVE_AUTH_SUITE_NAME}' "
        f"folder, referencing existing test cases.", links=_links(clone_id))


run = legacy_run(build)
