"""Step: `clone_plans_broker` — build this release's Broker test plan (Phase 3, bug_bash).

Creates a source-versioned plan that preserves both manual suites:
  • "Manual Tests (Android Broker)" — a static suite of the manual-broker cases (the manual
    bug-bash set), pinned to the two flight configs (ECS + LocalFlights);
  • "Manual Tests (Native Auth)" — a single dynamic (tag-query) suite, so its cases show
    directly (no extra folder level);
  • "Automation Failure Triage" — current-RC failures only, grouped by ECS/Local,
    then nonempty categories. Stress Tests is separate from UI Automation.
Verified singleton UI case/configs reference originals; collisions, Stress and no-ID
scenarios create release-owned cases. No full UI inventory or empty categories.
Shared cases are never edited.
Historical sources remain readable without automatically migrating an existing plan.

Identity lives in the release resource registry, separately from step completion.
Re-entry validates/reuses the bound plan; lost metadata triggers discovery, not a
blind create. Ambiguous/partial/uncertain work blocks for owner recovery.

Step defaults (`step_defaults` / offline scenarios):
  plan_id     : pretend the build already ran (this plan id) — verifies + reports done.
  clone_id    : the id the create should "return" (skip the live plan build).
  fail        : a detail string → force a Blocked (simulate an API/auth failure).
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext, thaw
import hashlib
import json

from orchestrator.outcomes import Done, Blocked
from orchestrator.evidence import StepData
from orchestrator.step_context import MISSING
from tools import testplans as T
from tools import broker_plans as B, broker_scenarios as S

from orchestrator.authority import OwnStepData, BrokerPlanEvidence, WriteOperation

EVIDENCE = (OwnStepData(), BrokerPlanEvidence())
WRITES = (WriteOperation.ENSURE_BROKER_PLAN,)
ID = "clone_plans_broker"
KIND = "agent"
NAME = 'Build Broker test plan (manual suites + failure triage by flight/category)'
REQUIRED_TOOLS = ('az',)
EFFECT_MODE = "transactional"
EFFECT_RECOVERY = "frozen"

@dataclass(frozen=True)
class PlanParameters:
    name: str | None = None

    def __post_init__(self):
        if self.name is not None and (not self.name.strip() or self.name != self.name.strip()):
            raise ValueError("Plan name must be nonblank text without surrounding whitespace")

PARAMETERS = {"prepare_effect": PlanParameters}

def prepare_effect(context):
    execution = context.evidence.step("bug_bash", ID).execution or {}
    if isinstance(execution.get("effect_input"), dict):
        return dict(execution["effect_input"])
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_broker: {fail}")
    dest = getattr(context.parameters, "name", None)
    if dest is None:
        dest = context.input("name", MISSING)
    if dest is MISSING:
        dest = T.broker_plan_name(context.release.release_id)
    if not isinstance(dest, str) or not dest.strip() or dest != dest.strip():
        return Blocked("clone_plans_broker: destination plan name must be nonblank text without surrounding whitespace")
    from steps.build_verify._common import latest_rc
    rc = latest_rc(context)
    record = context.evidence.resources.get(B.RESOURCE, {})
    if not isinstance(record, dict):
        return Blocked("clone_plans_broker: invalid Broker resource record; owner recovery required")
    injected = (
        context.input("plan_id", MISSING) is not MISSING
        or context.input("clone_id", MISSING) is not MISSING
    )
    source = record.get("source")
    if not injected:
        if "source" not in record:
            ok, source, detail = context.services.testplans.prepare_broker_source(rc=rc)
            if not ok:
                return Blocked(f"clone_plans_broker: {detail}")
        try:
            B._validate_source(source)
        except ValueError as exc:
            return Blocked(f"clone_plans_broker: {exc}")
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

def _links(plan_id):
    return [{"name": f"Broker test plan {plan_id}", "url": T.plan_web_url(plan_id)}]

def record_plan(context, plan_id, name):
    evidence = context.recovery()
    return plan_binding(evidence.step("bug_bash", ID).data,
                        evidence.resources.get(B.RESOURCE) or {}, plan_id, name)

def plan_binding(existing, resource, plan_id, name):
    data = thaw(existing or {})
    resource = thaw(resource)
    data.update(plan_id=plan_id, plan_name=name)
    if resource.get("source", {}).get("schema_version") == 4:
        from tools import broker_failure_triage as F
        F.target(resource)
        data.pop("ui_suite_id", None)
        data.update(source_version=4, scenario_source_sha256=S.digest(resource["source"]),
                    automation=thaw(resource["automation"]))
        return StepData(data)
    data["ui_suite_id"] = resource.get("ui_suite_id")
    if resource.get("source", {}).get("schema_version") == 3:
        data["source_version"] = 3
        data["scenario_source_sha256"] = S.digest(resource["source"])
        data["triage_suite_ids"] = {k: v for k, v in resource.get("suites", {}).items()
                                   if k not in ("broker", "native", "ui", "triage")}
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

    # Explicit offline `step_defaults` do not acquire resources or call external APIs.
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
    binding = record_plan(context, clone_id, dest)
    if binding.values.get("source_version") == 4:
        return Done(
            f"Broker test plan ready: '{dest}' (plan {clone_id}) — manual Broker and Native Auth "
            f"suites preserved; automation failures only under Automation Failure Triage -> "
            f"ECS/Local -> nonempty categories. UI Automation and Stress Tests stay separate; "
            f"no full UI inventory or empty folders. Shared originals are never edited.",
            links=_links(clone_id), updates=(binding,))
    return Done(
        f"Broker test plan ready: '{dest}' (plan {clone_id}) — manual Broker, Native Auth "
        f"and UI suites, plus source-owned failure categories when required "
        f"('{T.BROKER_MANUAL_SUITE_NAME}', '{T.BROKER_NATIVE_AUTH_SUITE_NAME}', "
        f"'{T.BROKER_UI_SUITE_NAME}'). Shared originals are never edited.",
        links=_links(clone_id), updates=(binding,))

KNOWLEDGE = {'summary': "Build this release's Broker test plan: manual suites plus failure-only triage by "
            'flight/category.',
 'what': "Each release gets a Broker test plan named 'Android Monthly Release - <Mon YYYY>' "
         "(e.g. 'Android Monthly Release - Aug 2026', area 'Engineering\\\\Auth "
         'Client\\\\Broker\\\\Android\'). Instead of ADO\'s "Copy Test Plan" (which reproduces '
         "the master's whole 45-suite tree), Scout creates a fresh plan preserving (1) 'Manual "
         "Tests (Android Broker)' — a static suite of the manual-broker cases resolved from "
         'the master template (#2007357, subtree suite 2008656), each pinned to the two flight '
         "configurations (293 'RC MSAL - RC Broker' / ECS + 330 '… (LocalFlights)') → 2 "
         "points/case; (2) 'Manual Tests (Native Auth)' — a single dynamic (tag-query) suite "
         "carrying the master's Native-Auth query (#2864589 flattened), so it stays current. "
         "Automation contains ONLY reconciled current-RC MRWP failures under 'Automation "
         "Failure Triage' -> ECS/Local -> nonempty categories. 'Stress Tests' is separate from "
         "'UI Automation'; Instrumented, Unit, Lab API, Native Auth E2E and Other UI retain "
         'their own categories. No full UI inventory, passing/NA/recovered cases or empty '
         'folders; zero failures means manual-only. Verified singleton UI case/configuration '
         'pairs reference originals. Collisions, Stress and no-ID failures get release-owned '
         'investigation cases with exact source links. Shared case fields are never edited; '
         "Testers belong to this release's test points.\n",
 'who': 'Scout runs it automatically during `next` (an agent step, via the ADO test-plan API).',
 'how': 'Find/reuse before create. The release-owned resources.broker_test_plan record '
        'survives step reopen; step.data.plan_id remains the downstream reference. A recorded '
        'ID is verified, never replaced after an API/auth/404 error. Without an ID, complete '
        'paged ADO discovery includes inactive and same-name plans. One matching plan must '
        'pass identity, source-versioned hierarchy, query, configuration and point checks; '
        'multiple matches block for owner selection. Tell Scout which existing plan to retain. '
        'Scout uses broker-plan --release <id> to inspect candidates, then --plan-id <id> '
        '--reason "<owner selection>" to bind without ADO writes or marking the step done. '
        '--area-path <observed area> can explicitly bind an existing plan whose area differs '
        'from configuration; new creates use areaPath. A new build checkpoints its source '
        'snapshot and creating intent under the existing release lock BEFORE POST, then '
        'checkpoints the returned ID before building suites. Partial plans are retained, not '
        'deleted. Retries verify the SAME plan against the frozen source and durable '
        'create/fill receipts. New failure-only sources use classification_policy 2, creating '
        "Stress Tests leaves from the start; they do not need an old plan's label correction. "
        'Historical matrix/v3/v4-policy-1 resources retain their frozen layout and '
        'classification until separately reviewed recovery. Never clear a held plan or reuse '
        'its identity to force a fresh plan. An uncertain create remains blocked even if no '
        'candidate is yet visible. Only after the owner confirms the original runner is '
        'stopped and creation did not occur may Scout use broker-plan --confirm-not-created '
        '--reason "<evidence>"; this also verifies absence and retains the previous attempt. '
        'Do not clear IDs, reopen to recreate, or infer absence from a failed read. One '
        'authoritative runs directory is required; the local lock is not cross-machine '
        'uniqueness.\n',
 'links': [{'name': 'Broker master test plan (#2007357, Manual Tests subtree)',
            'url': 'https://identitydivision.visualstudio.com/Engineering/_testPlans/define?planId=2007357&suiteId=2008656'},
           {'name': 'Broker test-plans doc',
            'url': 'https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/internal-release-checklist/test-plans'}],
 'faqs': [{'q': 'How are automation failures grouped?',
           'a': 'Automation Failure Triage -> ECS/Local -> nonempty category leaves, including '
                'separate UI Automation and Stress Tests. Both manual suites remain unchanged. '
                'Passing/NA/recovered evidence stays in Phase 2 reports, not new plan cases; '
                'zero failures means manual-only.'},
          {'q': 'Does it duplicate the test cases?',
           'a': 'Manual cases and verified singleton UI case/configuration pairs reference '
                'originals. Collisions, Stress and no-ID failures get owned investigation '
                'cases, not edits to shared originals. Every investigation retains its exact '
                'build/run/result provenance.'},
          {'q': 'Does it auto-update if the master changes later?',
           'a': 'Manual membership and failure-source identity are frozen at build time; '
                'Native Auth re-resolves its dynamic query. Retrying/reopening reuses the same '
                'plan and receipts, retaining resolved failure history. New builds, RCs or '
                'failures require explicit new-source review; never delete its ID to request '
                'another plan.'},
          {'q': 'How do I recover missing metadata or duplicate plans?',
           'a': 'Tell Scout which existing plan to retain after reviewing candidate IDs, '
                'areas, suites and results. Scout binds the verified plan with broker-plan, '
                'then resumes next. A bound plan cannot be silently switched because '
                'downstream results/assignments may already refer to it. Duplicate-plan '
                'deletion is a separate owner-approved action; never delete the shared case '
                'work items.'}]}
