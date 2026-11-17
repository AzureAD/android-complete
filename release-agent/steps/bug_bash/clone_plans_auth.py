"""Create a CCD-named static Authenticator release parent with two query children.

Manual Tests and Automated Tests partition curated Android cases using the frozen,
verified current-RC mapping. Every create owns durable intent and acknowledgement;
no shared cases or configuration pins change. Historical single-dynamic-suite
records and frozen executions retain their original recovery path without migration.

Mock knobs (mocks.local.yaml / tests):
  suite_id  : pretend the suite already exists (this id) — verifies + reports done.
  create_id : the id the create should "return" (skip the live create).
  existing  : id of an already-present same-named suite the name-scan should "find".
  fail      : a detail string → force a Blocked (simulate an API/auth failure).
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked
from orchestrator.evidence import RetryDecision, StepData
from steps.lib.mockctx import MISSING
from tools import testplans as T
from orchestrator import schedule
from steps.build_verify._common import valid_id

from orchestrator.authority import OwnStepData, WriteOperation

EVIDENCE = (OwnStepData(),)
WRITES = (WriteOperation.CREATE_AUTH_QUERY_SUITE, WriteOperation.CREATE_AUTH_SUITE,
          WriteOperation.ENSURE_AUTH_MONTHLY)
ID = "clone_plans_auth"
KIND = "agent"
NAME = 'Create Authenticator bug-bash test suite'
REQUIRED_TOOLS = ('az',)
EFFECT_RETRY = True
EFFECT_MODE = "transactional"
EFFECT_RECOVERY = "frozen"


def prepare_effect(context):
    from tools import auth_partition as A
    layout = getattr(context.parameters, "layout", A.LAYOUT)
    test_layout = context.input("layout", MISSING)
    if test_layout is not MISSING:
        if layout != A.LAYOUT and layout != test_layout:
            return Blocked("Conflicting Authenticator layout selections.")
        layout = test_layout
    if layout not in (A.LAYOUT, A.MONTHLY_LAYOUT):
        return Blocked("Unknown Authenticator layout.")
    execution = context.evidence.step("bug_bash", ID).execution or {}
    if isinstance(execution.get("effect_input"), dict):
        if (layout == A.MONTHLY_LAYOUT
                and execution["effect_input"].get("layout") != "auth-manual-automated-monthly-v2"):
            return Blocked("Historical frozen layout cannot be amended to Monthly v2 by replay.")
        return dict(execution["effect_input"])
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"clone_plans_auth: {fail}")
    name = getattr(context.parameters, "name", None)
    if name is None:
        name = context.input("name", MISSING)
    if name is MISSING:
        if not context.release.ccd:
            return Blocked(
                "clone_plans_auth: no Code Complete Date on record — can't name the suite "
                "'Android release/MM/DD/YYYY'. Set the CCD first (`set-ccd`).")
        ccd = schedule.parse_date(context.release.ccd)
        if ccd is None or context.release.ccd != ccd.isoformat():
            return Blocked("clone_plans_auth: Code Complete Date must be a valid YYYY-MM-DD date.")
        name = T.auth_suite_name(ccd.isoformat())
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        return Blocked("clone_plans_auth: suite name must be nonblank text without surrounding whitespace.")
    identity = {
        "release": context.release.release_id,
        "org": T.ORG,
        "project": T.PROJECT,
        "plan_id": T.AUTH_PLAN,
        "parent_suite_id": T.AUTH_ROOT_SUITE,
        "name": name,
    }
    data = context.evidence.step("bug_bash", ID).data or {}
    from . import auth_categories
    try:
        categories = A.is_partition(data)
    except ValueError as exc:
        return Blocked(f"clone_plans_auth: {exc}")
    if not categories and ("suite_id" in data or "creation" in data):
        if layout != A.LAYOUT:
            return Blocked("Historical Authenticator target cannot be amended to Monthly v2 by replay.")
        return {**identity, "query": T.auth_bugbash_query()}
    return auth_categories.prepare(context, identity, layout=layout)


def execute(context: StepContext):
    return _dispatch(context, allow_create=True)


def reconcile(context: StepContext):
    return _dispatch(context, allow_create=False)


def _effect_input(execution):
    frozen = thaw(execution.get("effect_input"))
    if isinstance(frozen, dict):
        # The engine hashes its input overrides with the operation, not the domain schema.
        frozen.pop("__effect_mocks__", None)
    return frozen


def _dispatch(context, *, allow_create):
    from . import auth_categories
    from tools import auth_partition as A
    frozen = _effect_input(context.effect.execution)
    if not isinstance(frozen, dict):
        return Blocked("clone_plans_auth: malformed frozen execution")
    if "layout" in frozen:
        return auth_categories.build(context, frozen)
    if (set(frozen) != {"release", "org", "project", "plan_id", "parent_suite_id", "name", "query"}
            or not isinstance(frozen.get("query"), str) or not frozen["query"].strip()):
        return Blocked("clone_plans_auth: unknown/malformed single-suite execution")
    try:
        if A.is_partition(context.recovery().step("bug_bash", ID).data):
            return Blocked("clone_plans_auth: category data cannot use a single-suite execution")
    except ValueError as exc:
        return Blocked(f"clone_plans_auth: {exc}")
    return _build(context, allow_create=allow_create, frozen=frozen)


@dataclass(frozen=True)
class BuildParameters:
    layout: str = "auth-manual-automated-v1"
    name: str | None = None

    def __post_init__(self):
        if self.name is not None and (not self.name.strip() or self.name != self.name.strip()):
            raise ValueError("Suite name must be nonblank text without surrounding whitespace")


@dataclass(frozen=True)
class RetryParameters:
    reason: str


PARAMETERS = {"build": BuildParameters, "prepare_effect": BuildParameters, "authorize_retry": RetryParameters}


def authorize_retry(context: StepContext[RetryParameters]):
    """Authorize only after exhaustive discovery proves the frozen suite is absent."""
    execution = context.evidence.step("bug_bash", ID).execution
    reason = context.parameters.reason
    step = context.evidence.step("bug_bash", ID)
    if execution and "layout" in (execution.get("effect_input") or {}):
        from . import auth_categories
        return auth_categories.authorize_retry(context, _effect_input(execution), reason)
    if not execution or "suite_id" in step.data:
        return RetryDecision(False, "A recorded suite must be reconciled, never recreated.")
    if not isinstance(reason, str) or not reason.strip():
        return RetryDecision(False, "Suite retry requires explicit owner-reviewed evidence.")
    frozen = execution.get("effect_input") or {}
    ok, existing, detail = context.services.testplans.find_auth_query_suite(
        frozen.get("name"),
        frozen.get("query"),
        org=frozen.get("org"),
        project=frozen.get("project"),
        plan_id=frozen.get("plan_id"),
        parent_suite_id=frozen.get("parent_suite_id"),
    )
    if not ok:
        return RetryDecision(False, f"Could not verify suite absence: {detail}")
    if existing is not None and (
            not isinstance(existing, dict) or not valid_id(existing.get("id"))):
        return RetryDecision(False, "Suite discovery was malformed; absence is not established.")
    if existing:
        return RetryDecision(False, f"Suite {existing.get('id')} exists and must be reconciled, not recreated")
    creation = dict((step.data or {}).get("creation") or {})
    history = list(creation.get("attempt_history") or [])
    history.append({
        "operation_key": execution.get("operation_key"),
        "status": creation.get("status"),
        "reason": reason,
    })
    creation.update(
        status="retry_authorized",
        retry_reason=reason,
        attempt_history=history,
    )
    data = thaw(step.data or {})
    data["creation"] = creation
    return RetryDecision(True, "No matching suite exists; retry authorized", (StepData(data),))

MOCKABLE = {
    "name": {"kind": "input", "desc": "Override the suite name (e.g. a 'TEST ...' name for a safe live run)."},
    "layout": {"kind": "input", "desc": "Select an explicitly reviewed fresh Authenticator layout; never migrates a frozen clone."},
    "suite_id": {"kind": "input", "desc": "Pretend the query-suite already exists (this id)."},
    "create_id": {"kind": "input", "desc": "Id the create should return (skip the live create)."},
    "existing": {"kind": "input", "desc": "Id an existing same-named suite the name-scan finds."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail (simulate an API failure)."},
}


def _links(suite_id, frozen=None):
    frozen = frozen or {}
    return [{"name": f"Authenticator bug-bash suite {suite_id}",
             "url": T.plan_web_url(
                 frozen.get("plan_id", T.AUTH_PLAN),
                 suite_id,
                 org=frozen.get("org", T.ORG),
                 project=frozen.get("project", T.PROJECT),
             )}]


def build(context: StepContext):
    if context.effect is None:
        raise ValueError("Authenticator creation requires an authorized effect context")
    return execute(context)


def _build(context, *, allow_create, frozen=None):
    frozen = frozen or {}
    org = frozen.get("org", T.ORG)
    project = frozen.get("project", T.PROJECT)
    plan_id = frozen.get("plan_id", T.AUTH_PLAN)
    parent_suite_id = frozen.get("parent_suite_id", T.AUTH_ROOT_SUITE)
    query = frozen.get("query", T.auth_bugbash_query())
    name = frozen.get("name", MISSING)
    if name is MISSING:
        name = context.input("name", MISSING)
    if name is MISSING:
        if not context.release.ccd:
            return Blocked(
                "clone_plans_auth: no Code Complete Date on record — can't name the suite "
                "'Android release/MM/DD/YYYY'. Set the CCD first (`set-ccd`).")
        name = T.auth_suite_name(context.release.ccd)
    step = context.recovery().step("bug_bash", ID)
    data = thaw(step.data or {})

    # Already created? (test-injected id, or a stored id from a prior run) → done.
    injected = context.input("suite_id", MISSING)
    if injected is not MISSING:
        return Done(f"Authenticator bug-bash suite already exists for {context.release.release_id}: "
                    f"'{name}' (suite {injected}).", links=_links(injected, frozen))
    creation = data.get("creation", {})
    if ("creation" in data and (not isinstance(creation, dict) or creation.get("status") not in
            ("creating", "created", "ready", "retry_authorized"))):
        return Blocked("clone_plans_auth: invalid creation receipt; owner recovery required.")
    stored = data.get("suite_id")
    if "suite_id" in data:
        if not valid_id(stored):
            return Blocked("clone_plans_auth: recorded suite id is invalid; owner recovery required.")
        ok, info, detail = context.services.testplans.validate_auth_query_suite(
            stored,
            name,
            query,
            org=org,
            project=project,
            plan_id=plan_id,
            parent_suite_id=parent_suite_id,
        )
        if ok:
            if not isinstance(info, dict):
                return Blocked("clone_plans_auth: malformed suite validation; owner recovery required.")
            data.setdefault("suite_name", name)
            data["creation"] = {**creation, "status": "ready", "suite_id": stored,
                                "operation_key": (step.execution or {}).get("operation_key")}
            context.effect.commit.commit(StepData(data))
            return Done(f"Authenticator bug-bash suite already exists for {context.release.release_id}: "
                        f"'{info.get('name') or name}' (suite {stored}).",
                        links=_links(stored, frozen))
        return Blocked(
            f"clone_plans_auth: recorded suite {stored} failed identity validation "
            f"({detail}). Owner recovery is required; do not create another suite."
        )

    # Duplicate guard: is a same-named suite already under the root? (offline-injectable)
    existing = context.input("existing", MISSING)
    if existing is MISSING:
        ok, info, detail = context.services.testplans.find_auth_query_suite(
            name,
            query,
            org=org,
            project=project,
            plan_id=plan_id,
            parent_suite_id=parent_suite_id,
        )
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"clone_plans_auth: could not list suites under the "
                           f"MSAuthenticator plan (#{plan_id}) ({detail}){hint}.")
        if info is not None and (not isinstance(info, dict) or not valid_id(info.get("id"))):
            return Blocked("clone_plans_auth: malformed suite discovery; cannot establish absence.")
        existing = (info or {}).get("id")
    if existing:
        data.update(
            suite_id=existing,
            suite_name=name,
            creation={**creation,
                "operation_key": (step.execution or {}).get("operation_key"),
                "status": "ready",
                "suite_id": existing,
            },
        )
        context.effect.commit.commit(StepData(data))
        return Done(f"Authenticator bug-bash suite already exists for {context.release.release_id}: "
                    f"'{name}' (suite {existing}).", links=_links(existing, frozen))

    if creation and creation.get("status") in ("creating", "created", "ready"):
        return Blocked(
            f"clone_plans_auth: creation of '{name}' may have started, but no "
            "same-named suite is visible. Do not create another suite; owner "
            "recovery is required."
        )
    if not allow_create:
        # The pre-POST checkpoint proves whether an attempt could have started.
        # An absent/retry-authorized intent is safe; "creating" was rejected above.
        allow_create = True

    # Create the query-based suite.
    create_id = context.input("create_id", MISSING)
    live_create = create_id is MISSING
    if live_create:
        # Persist permission-to-create before the non-idempotent POST.
        data["creation"] = {**creation,
            "operation_key": (step.execution or {}).get("operation_key"),
            "status": "creating",
        }
        context.effect.commit.commit(StepData(data))
        ok, create_id, detail = context.effect.services.create_auth_query_suite(
            name,
            query,
            org=org,
            project=project,
            plan_id=plan_id,
            parent_suite_id=parent_suite_id,
        )
        if not ok:
            if T.auth_suite_create_definitely_absent(detail):
                data["creation"].update(
                    status="retry_authorized",
                    last_failure=detail,
                )
                context.effect.commit.commit(StepData(data))
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(
                f"clone_plans_auth: could not create the query-based suite '{name}' under "
                f"the MSAuthenticator plan (#{plan_id}) ({detail}){hint}.")
        if not valid_id(create_id):
            return Blocked("clone_plans_auth: suite creation returned no valid id; owner recovery required.")
        # Retain the acknowledged identity before a later read can fail or the suite can move.
        data.update(suite_id=create_id, suite_name=name,
                    creation={**data["creation"], "status": "created", "suite_id": create_id})
        context.effect.commit.commit(StepData(data))
        valid, _, validation = context.services.testplans.validate_auth_query_suite(
            create_id,
            name,
            query,
            org=org,
            project=project,
            plan_id=plan_id,
            parent_suite_id=parent_suite_id,
        )
        if not valid:
            return Blocked(
                f"clone_plans_auth: created suite {create_id} failed identity "
                f"validation ({validation}). Owner recovery is required."
            )

    data["suite_id"] = create_id
    data["suite_name"] = name
    data["creation"] = {**data.get("creation", {}),
        "operation_key": (step.execution or {}).get("operation_key"),
        "status": "ready",
        "suite_id": create_id,
    }
    context.effect.commit.commit(StepData(data))
    return Done(
        f"Created the Authenticator bug-bash query-suite '{name}' (suite {create_id}) "
        f"under 'MSAuthenticator Test Passes'. Next: assign testers (later step).",
        links=_links(create_id, frozen))

KNOWLEDGE = {'summary': 'Create the Authenticator release folder with Manual Tests and Automated Tests.',
 'what': "NEW releases use a static parent under 'MSAuthenticator Test Passes' (plan714514, "
         "root714515), named 'Android release/MM/DD/YYYY' from the CCD. Its two direct dynamic "
         'children are Manual Tests and Automated Tests. Both preserve the curated Android + '
         'ReleaseBugBash, Authenticator-area and non-Closed query filters. Automated Tests '
         'includes the verified case IDs mapped from the captured current-RC Phase 2 Android '
         'results; Manual Tests excludes exactly those IDs. The queries are disjoint and cover '
         'the same curated set. This is NOT ADO Automation Status or the generic Automated '
         'tag. Both children remain even when one is empty; existing cases are referenced, '
         'never copied or edited. Monthly Firebase canonical coverage remains report-only with '
         'no forced mapping. Explicit fresh clone preparation with --param '
         'layout=auth-manual-automated-monthly-v2 adds a STATIC Monthly UI Failure Triage '
         'sibling only for distinct unresolved failures after exact-title retries, with no '
         'empty folder or initial passing/NA/recovered cases. Cases use '
         'ReleaseMonthlyInvestigation (never ReleaseBugBash), exact source provenance, and '
         'durable create/membership receipts. Completed layouts are not mutated in-place; '
         'switching layouts requires an explicit fresh rerun. P3S3/downstream require '
         'completed current fill/live points. This step stops after creating and validating '
         'the hierarchy; no outcomes or Testers.\n',
 'who': 'Scout runs it automatically during `next` (an agent step, via the ADO test-suite '
        'create API).',
 'how': 'Freeze layout auth-manual-automated-v1, RC/APK/test-build identities, sorted mapped '
        'case IDs and both exact queries before writes. Every parent/child POST has its own '
        'durable creation intent and acknowledged ID saved before readback. Validate exact '
        'direct parent/name/type/query and configuration inheritance, plus unique exhaustive '
        'discovery. A partial or uncertain create retains ownership; never clear IDs or '
        'blindly create again. Explicit retry needs an owner reason and verified absence for '
        'that exact unacknowledged category. Changed RC/build/mapped IDs hold for owner '
        'recovery; outcome-only refresh does not reclassify. Old single-dynamic-suite records '
        'and frozen executions keep their original recovery path, never migrate. A same-named '
        'legacy suite is not a new static parent; use a separately reviewed TEST ONLY name for '
        'testing. Saved suite_id links to the parent; manual_suite_id and automated_suite_id '
        'identify the children. Only the automated child is filled by ui_test_status later.\n',
 'links': [{'name': 'MSAuthenticator Test Passes plan (#714514)',
            'url': 'https://identitydivision.visualstudio.com/Engineering/_testManagement?_a=tests&planId=714514&suiteId=714515'},
           {'name': 'How to make a test suite for bug bash (IDWiki 33580)',
            'url': 'https://identitydivision.visualstudio.com/IdentityWiki/_wiki/wikis/IdentityWiki.wiki/33580/How-to-make-test-suite-for-bug-bash-'}],
 'faqs': [{'q': 'Does it assign testers?',
           'a': 'No. It creates and validates the parent and two query children only. Results '
                'and release-point Testers are handled by later steps; shared case AssignedTo '
                'is never changed.'},
          {'q': 'What names the suite?',
           'a': "From the CCD: 'Android release/MM/DD/YYYY' (e.g. CCD 2026-08-13 → 'Android "
                "release/08/13/2026'), matching prod."},
          {'q': 'Which test cases does it include?',
           'a': 'The existing curated Android ReleaseBugBash set, including matching '
                'month-specific tags, under the Authenticator area and not Closed. Phase 2 '
                'verified mapped Android IDs go into Automated Tests; the remainder go into '
                'Manual Tests. It does not broaden to all Android cases or invent Monthly '
                'mappings.'},
          {'q': 'Does this change an existing release suite?',
           'a': 'No. Historical single-query-suite identities and their frozen recovery inputs '
                'remain unchanged. New layouts are explicit and malformed or conflicting '
                'identities hold; no automatic migration or replacement.'}]}
