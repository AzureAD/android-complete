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

from orchestrator.step_context import StepContext, thaw
import hashlib
import json

from orchestrator.outcomes import Done, Blocked
from orchestrator.evidence import StepData
from steps.lib.mockctx import MISSING
from tools import testplans as T
from tools import broker_plans as B

from orchestrator.authority import OwnStepData, BrokerPlanEvidence, WriteOperation

EVIDENCE = (OwnStepData(), BrokerPlanEvidence())
WRITES = (WriteOperation.ENSURE_BROKER_PLAN,)
ID = "clone_plans_broker"
KIND = "agent"
EFFECT_MODE = "transactional"
EFFECT_RECOVERY = "frozen"


def prepare_effect(context):
    execution = context.evidence.step("bug_bash", ID).execution or {}
    if isinstance(execution.get("effect_input"), dict):
        return dict(execution["effect_input"])
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_broker: {fail}")
    dest = context.input("name", MISSING)
    if dest is MISSING:
        dest = T.broker_plan_name(context.release.release_id)
    from steps.build_verify._common import latest_rc
    rc = latest_rc(context)
    record = context.evidence.resources.get(B.RESOURCE) or {}
    injected = (
        context.input("plan_id", MISSING) is not MISSING
        or context.input("clone_id", MISSING) is not MISSING
    )
    source = record.get("source")
    if source is None and not injected:
        ok, source, detail = context.services.testplans.prepare_broker_source(rc=rc)
        if not ok:
            return Blocked(f"clone_plans_broker: {detail}")
    evidence_sha256 = hashlib.sha256(
        json.dumps(rc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "release": context.release.release_id,
        "plan_name": dest,
        "identity": B.identity(context.release.release_id, dest),
        "rc": rc.get("rc"),
        "ecs_run": (rc.get("ecs") or {}).get("run_id"),
        "local_run": (rc.get("local") or {}).get("run_id"),
        "evidence_sha256": evidence_sha256,
        "source": source,
    }


def execute(context: StepContext):
    return _build(context, thaw(context.effect.execution["effect_input"]))


def reconcile(context: StepContext):
    """Recover the durable plan intent; ensure_plan never repeats an unresolved create."""
    return execute(context)

MOCKABLE = {
    "name": {"kind": "input", "desc": "Override the destination plan name (e.g. a 'TEST ...' name for a safe live run)."},
    "plan_id": {"kind": "input", "desc": "Pretend the build already produced this plan id (idempotency test)."},
    "clone_id": {"kind": "input", "desc": "Id the create should return (skip the live plan build)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail (simulate an API failure)."},
}


def _links(plan_id):
    return [{"name": f"Broker test plan {plan_id}", "url": T.plan_web_url(plan_id)}]


def record_plan(context, plan_id, name):
    evidence = context.recovery()
    return plan_binding(evidence.step("bug_bash", ID).data,
                        evidence.resources.get(B.RESOURCE) or {}, plan_id, name)


def plan_binding(existing, resource, plan_id, name):
    data = thaw(existing or {})
    data.update(plan_id=plan_id, plan_name=name)
    data["ui_suite_id"] = resource.get("ui_suite_id")
    return StepData(data)


def build(context: StepContext):
    if context.effect is None:
        raise ValueError("Broker creation requires an authorized effect context")
    return execute(context)


def _build(context, effect_input=None):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_broker: {fail}")

    effect_input = effect_input or {}
    release_id = effect_input.get("release", context.release.release_id)
    dest = effect_input.get("plan_name", MISSING)
    if dest is MISSING:
        dest = context.input("name", MISSING)
    if dest is MISSING:
        dest = T.broker_plan_name(context.release.release_id)
    step = context.evidence.step("bug_bash", ID)

    # Explicit offline mocks do not acquire resources or call external APIs.
    injected = context.input("plan_id", MISSING)
    if injected is not MISSING:
        return Done(f"Broker test plan already built for {context.release.release_id}: "
                    f"'{dest}' (plan {injected}).", links=_links(injected),
                    updates=(record_plan(context, injected, dest),))
    clone_id = context.input("clone_id", MISSING)
    if clone_id is MISSING:
        record = context.recovery().resources.get(B.RESOURCE, {})
        if not isinstance(record, dict):
            return Blocked("Invalid Broker resource record; owner recovery required")
        try:
            from steps.build_verify._common import latest_rc
            ok, clone_id, detail = context.effect.services.ensure_broker_plan(
                release_id, dest, record=record,
                stored_id=(step.data or {}).get("plan_id", B.MISSING_ID),
                rc=latest_rc(context),
                prepared_source=effect_input.get("source"),
                expected_identity=effect_input.get("identity"),
            )
        except ValueError as exc:
            return Blocked(f"clone_plans_broker: {exc}")
        if not ok:
            return Blocked(
                f"clone_plans_broker: {detail}", links=_links(clone_id) if clone_id else [])
    return Done(
        f"Broker test plan ready: '{dest}' (plan {clone_id}) — three flat suites "
        f"('{T.BROKER_MANUAL_SUITE_NAME}', '{T.BROKER_NATIVE_AUTH_SUITE_NAME}', "
        f"'{T.BROKER_UI_SUITE_NAME}'), referencing existing test cases.",
        links=_links(clone_id), updates=(record_plan(context, clone_id, dest),))
