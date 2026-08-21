"""Step: `clone_plans_auth` — create the Authenticator bug-bash test suite for this
release (Phase 3, bug_bash).

Per the Authenticator "How to make a test suite for bug bash" doc, each release creates
a NEW query-based (dynamic) test suite under the standing "MSAuthenticator Test Passes"
plan (714514 / rootSuite 714515), named after the release ("Android/release/MM/YYYY"),
whose WIQL selects the Android bug-bash test cases. This is the Authenticator half of the
old `clone_plans` stub.

We CREATE the suite and STOP — assigning testers is a later, manual step (out of scope
here, per the doc's "Assign Testers" cut line).

Idempotent: the created suite id is stashed on the step (data.suite_id); a re-run
re-confirms it and reports done without creating a duplicate. As a second guard the step
also looks for an existing same-named child suite before creating.

Mock knobs (mocks.local.yaml / tests):
  suite_id  : pretend the suite already exists (this id) — verifies + reports done.
  create_id : the id the create should "return" (skip the live create).
  existing  : id of an already-present same-named suite the name-scan should "find".
  fail      : a detail string → force a Blocked (simulate an API/auth failure).
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import testplans as T

ID = "clone_plans_auth"
KIND = "agent"

MOCKABLE = {
    "suite_id": {"kind": "input", "desc": "Pretend the query-suite already exists (this id)."},
    "create_id": {"kind": "input", "desc": "Id the create should return (skip the live create)."},
    "existing": {"kind": "input", "desc": "Id an existing same-named suite the name-scan finds."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail (simulate an API failure)."},
}


def _links(suite_id):
    return [{"name": f"Authenticator bug-bash suite {suite_id}",
             "url": T.plan_web_url(T.AUTH_PLAN, suite_id)}]


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_auth: {fail}")

    name = T.auth_suite_name(state.release_id)
    step = state.get_step("bug_bash", ID)

    # Already created? (test-injected id, or a stored id from a prior run) → done.
    injected = mock_input("suite_id", MISSING)
    if injected is not MISSING:
        return Done(f"Authenticator bug-bash suite already exists for {state.release_id}: "
                    f"'{name}' (suite {injected}).", links=_links(injected))
    stored = (step.data or {}).get("suite_id")
    if stored:
        ok, info, _ = T.get_suite(T.AUTH_PLAN, stored)
        if ok and info:
            return Done(f"Authenticator bug-bash suite already exists for {state.release_id}: "
                        f"'{info.get('name') or name}' (suite {stored}).", links=_links(stored))
        # recorded id no longer resolves — fall through and re-create

    # Duplicate guard: is a same-named suite already under the root? (offline-injectable)
    existing = mock_input("existing", MISSING)
    if existing is MISSING:
        ok, existing, detail = T.find_child_suite_by_name(T.AUTH_PLAN, T.AUTH_ROOT_SUITE, name)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"clone_plans_auth: could not list suites under the "
                           f"MSAuthenticator plan (#{T.AUTH_PLAN}) ({detail}){hint}.")
    if existing:
        step.data = dict(step.data or {}); step.data["suite_id"] = existing
        state.set_step("bug_bash", ID, step)
        return Done(f"Authenticator bug-bash suite already exists for {state.release_id}: "
                    f"'{name}' (suite {existing}).", links=_links(existing))

    # Create the query-based suite.
    create_id = mock_input("create_id", MISSING)
    if create_id is MISSING:
        ok, create_id, detail = T.create_auth_query_suite(name, T.auth_bugbash_query())
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(
                f"clone_plans_auth: could not create the query-based suite '{name}' under "
                f"the MSAuthenticator plan (#{T.AUTH_PLAN}) ({detail}){hint}.")

    step.data = dict(step.data or {})
    step.data["suite_id"] = create_id
    step.data["suite_name"] = name
    state.set_step("bug_bash", ID, step)
    return Done(
        f"Created the Authenticator bug-bash query-suite '{name}' (suite {create_id}) "
        f"under 'MSAuthenticator Test Passes'. Next: assign testers (later step).",
        links=_links(create_id))


run = legacy_run(build)
