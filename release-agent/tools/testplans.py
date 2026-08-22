"""ADO Test-Plan operations for Phase 3 (bug_bash) — the two `clone_plans_*` steps.

Two DIFFERENT release procedures, per the team docs:

  * BROKER  — build a FLAT monthly plan. Instead of ADO's "Copy Test Plan" (which
      reproduces the master's whole 45-suite tree), create a fresh plan "Android Monthly
      Release - <Mon YYYY>" with ONE static suite "Manual Tests (Android Broker)" holding
      just the manual-broker cases resolved from the master's subtree (2007357 / suite
      2008656), each pinned to the two flight configs (293 ECS + 330 LocalFlights). Cases are
      REFERENCED (shared, not duplicated). Flat = trackable; downstream steps find the suite
      by name so it's a drop-in.
      Doc: eng.ms/.../internal-release-checklist/test-plans

  * AUTHENTICATOR — CREATE a new query-based (dynamic) test suite under the standing
      "MSAuthenticator Test Passes" plan (714514 / rootSuite 714515), named after the
      release "Android release/MM/DD/YYYY", whose WIQL selects the Android bug-bash test
      cases (tag 'Android' + 'ReleaseBugBash', not Closed — matches the current prod suite
      3728419 "Android release/08/13/2026"). We STOP after creating the suite — assigning
      testers is a later, manual step.
      Doc: IdentityWiki page 33580 (How to make test suite for bug bash).

Everything shells out to `az` (bearer token) via tools.pipelines helpers and returns an
(ok, value, detail) triple. Reads are cheap; the two creates are the only writes.
"""
from __future__ import annotations

from tools import pipelines as P

ORG = P.ENGINEERING_ORG          # https://identitydivision.visualstudio.com
PROJECT = P.ENGINEERING_PROJECT  # Engineering
_API = "api-version=7.1"

# ---- Broker: master template + the flat monthly copy ----
BROKER_MASTER_PLAN = 2007357
BROKER_MASTER_ROOT_SUITE = 2007358
# The "Manual Tests (Android Broker)" subtree of the master — the ONLY tests the release
# bug bash runs. The monthly copy flattens this subtree into a single static suite.
BROKER_MANUAL_ROOT_SUITE = 2008656
BROKER_MANUAL_SUITE_NAME = "Manual Tests (Android Broker)"
# Test configurations the bug bash runs each case under (the two flight pipelines):
#   293 = "RC MSAL - RC Broker"              (ECS flights)
#   330 = "RC MSAL - RC Broker (LocalFlights)" (Local flights)
# Assigned explicitly so the flat plan gets exactly 2 points/case (matches the master's
# matrix) instead of inheriting the project's ~190 default configurations.
BROKER_CONFIGS = [293, 330]
BROKER_AREA_PATH = "Engineering\\Auth Client\\Broker\\Android"
BROKER_ITERATION = "Engineering"

# ---- Authenticator: standing plan the query-suite hangs under ----
AUTH_PLAN = 714514
AUTH_ROOT_SUITE = 714515
AUTH_AREA_PATH = "Engineering\\ISP\\Identity Apps"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _split_release(release_id: str):
    """'2026-08' -> (2026, 8). Raises ValueError on a malformed id."""
    y, m = str(release_id).split("-")[:2]
    return int(y), int(m)


def broker_plan_name(release_id: str) -> str:
    """The Broker clone's destination plan name, e.g. 'Android Monthly Release - Aug 2026'."""
    y, m = _split_release(release_id)
    return f"Android Monthly Release - {_MONTHS[m - 1]} {y}"


def auth_suite_name(ccd: str) -> str:
    """The Authenticator query-suite name from the release's Code Complete Date, e.g.
    CCD '2026-08-13' -> 'Android release/08/13/2026' — matches the prod convention
    (suite 3728419 'Android release/08/13/2026') and the IDWiki doc's
    'Android release/MM/DD/YYYY'. `ccd` is 'YYYY-MM-DD'."""
    y, m, d = str(ccd).split("-")[:3]
    return f"Android release/{int(m):02d}/{int(d):02d}/{int(y)}"


def auth_bugbash_query() -> str:
    """The WIQL for the Authenticator bug-bash query-suite — the Android test cases
    curated for the release bug bash (tag 'ReleaseBugBash'), excluding Closed. Matches
    the current prod suite (e.g. 'Android release/08/13/2026', suite 3728419).

    Per the IDWiki doc, the always-included cases carry tag 'ReleaseBugBash'; month-
    specific cases carry 'ReleaseBugBash<Month>' (e.g. 'ReleaseBugBashAug'). Because ADO's
    `[System.Tags] contains 'X'` is a substring match, the single 'ReleaseBugBash' clause
    captures BOTH — no separate month clause needed."""
    return (
        "select [System.Id], [System.WorkItemType], [System.Title], "
        "[Microsoft.VSTS.Common.Priority], [System.AssignedTo], [System.AreaPath] "
        "from WorkItems where [System.TeamProject] = @project and "
        "[System.WorkItemType] in group 'Microsoft.TestCaseCategory' and "
        f"[System.AreaPath] under '{AUTH_AREA_PATH}' and "
        "[System.Tags] contains 'Android' and [System.State] <> 'Closed' and "
        "[System.Tags] contains 'ReleaseBugBash'")


def _plan_url(plan_id, extra=""):
    return f"{ORG}/{PROJECT}/_apis/testplan/plans/{plan_id}?{_API}{extra}"


# ---------------------------------------------------------------- reads

def get_plan(plan_id, timeout=60):
    """(ok, {id,name,areaPath,iteration,rootSuiteId}, detail) for a test plan, or block
    detail. Used to confirm an already-recorded clone still exists (idempotency)."""
    ok, j, d = P._ado_rest_get(_plan_url(plan_id), timeout)
    if not ok:
        return (False, None, d)
    root = (j or {}).get("rootSuite") or {}
    return (True, {"id": j.get("id"), "name": j.get("name"),
                   "areaPath": j.get("areaPath"), "iteration": j.get("iteration"),
                   "rootSuiteId": root.get("id")}, "")


def get_suite(plan_id, suite_id, timeout=60):
    """(ok, {id,name,suiteType}, detail) for a suite under a plan."""
    url = f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites/{suite_id}?{_API}"
    ok, j, d = P._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, d)
    return (True, {"id": j.get("id"), "name": j.get("name"),
                   "suiteType": j.get("suiteType")}, "")


def find_child_suite_by_name(plan_id, parent_suite_id, name, timeout=90):
    """Find a DIRECT child suite of `parent_suite_id` named `name` (case-insensitive).
    Returns (ok, suite_id_or_None, detail). Pages through ALL of the plan's suites
    (following the ADO continuation-token header). A duplicate guard for the create."""
    want = (name or "").strip().lower()
    url = f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?{_API}"
    ok, suites, detail = P._ado_rest_get_all(url, timeout)
    if not ok:
        return (False, None, detail)
    for s in suites:
        if (s.get("name") or "").strip().lower() == want:
            parent = s.get("parentSuite") or {}
            if str(parent.get("id")) == str(parent_suite_id):
                return (True, s.get("id"), "")
    return (True, None, "")


# ---------------------------------------------------------------- writes

def create_broker_flat_plan(dest_name, timeout=120):
    """Build the release's FLAT Broker test plan and return (ok, new_plan_id, detail).

    Instead of ADO's "Copy Test Plan" (which reproduces the master's whole 45-suite
    hierarchy), this creates a fresh plan with a SINGLE static suite
    "Manual Tests (Android Broker)" holding just the relevant manual-broker cases — the
    only tests the bug bash runs. Steps:

      1. create an empty plan `dest_name` (Broker area/iteration);
      2. create one static child suite named "Manual Tests (Android Broker)" pinned to the
         two flight configurations (293 ECS + 330 LocalFlights) — inheritDefaultConfigurations
         is False so the plan's ~190 project-default configs are NOT applied;
      3. resolve the master's "Manual Tests (Android Broker)" subtree (flattening its dynamic
         sub-suites) to the current set of cases, and add them all to the flat suite,
         referencing the existing work items (shared, not duplicated), each assigned to both
         configs → 2 points/case (matches the master's matrix).

    Downstream (distribute_tests, gather_progress) find the suite by name, so a flat plan is
    a drop-in. On a partial failure the half-built plan is best-effort deleted so a re-run
    starts clean.
    """
    from tools import distribution as D          # local import avoids a circular import

    # 1) empty plan
    ok, j, d = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/plans?{_API}", "POST",
        {"name": dest_name, "area": {"name": BROKER_AREA_PATH}, "iteration": BROKER_ITERATION},
        timeout)
    if not ok:
        return (False, None, d)
    pid = (j or {}).get("id")
    root = ((j or {}).get("rootSuite") or {}).get("id")
    if not pid or not root:
        return (False, None, "plan create returned no id/rootSuite")

    def _cleanup(reason):
        P._ado_rest_send(f"{ORG}/{PROJECT}/_apis/testplan/plans/{pid}?{_API}", "DELETE", None, 60)
        return (False, None, reason)

    # 2) single flat static suite, pinned to the two flight configs
    ok2, sj, d2 = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/suites?api-version=7.1-preview.1", "POST",
        {"suiteType": "staticTestSuite", "name": BROKER_MANUAL_SUITE_NAME,
         "parentSuite": {"id": root}, "inheritDefaultConfigurations": False,
         "defaultConfigurations": [{"id": c} for c in BROKER_CONFIGS]},
        timeout)
    if not ok2:
        return _cleanup(f"suite create failed: {d2}")
    suite_id = (sj or {}).get("id")
    if not suite_id:
        return _cleanup("suite create returned no id")

    # 3) resolve the master's manual-broker cases and add them to the flat suite
    okc, cases, dc = D.broker_manual_cases(BROKER_MASTER_PLAN, BROKER_MANUAL_ROOT_SUITE, timeout)
    if not okc:
        return _cleanup(f"could not resolve master manual cases: {dc}")
    if not cases:
        return _cleanup("master 'Manual Tests (Android Broker)' subtree resolved to 0 cases")
    body = [{"workItem": {"id": int(c["id"])},
             "pointAssignments": [{"configurationId": cid} for cid in BROKER_CONFIGS]}
            for c in cases]
    oka, _aj, da = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/Suites/{suite_id}/TestCase"
        f"?api-version=7.1-preview.3", "POST", body, max(timeout, 120))
    if not oka:
        return _cleanup(f"adding {len(cases)} cases failed: {da}")
    return (True, pid, "")


def create_auth_query_suite(name, query, timeout=90):
    """CREATE a query-based (dynamic) test suite `name` under the Authenticator plan's
    root suite, selecting the given WIQL. Returns (ok, new_suite_id, detail)."""
    url = f"{ORG}/{PROJECT}/_apis/testplan/Plans/{AUTH_PLAN}/suites?api-version=7.1-preview.1"
    body = {"suiteType": "dynamicTestSuite", "name": name,
            "parentSuite": {"id": AUTH_ROOT_SUITE}, "queryString": query}
    ok, j, d = P._ado_rest_send(url, "POST", body, timeout)
    if not ok:
        return (False, None, d)
    sid = (j or {}).get("id")
    if not sid:
        return (False, None, "suite create returned no id")
    return (True, sid, "")


# ---------------------------------------------------------------- links

def plan_web_url(plan_id, suite_id=None):
    """A human 'define' URL for a plan (optionally a suite)."""
    u = f"{ORG}/{PROJECT}/_testPlans/define?planId={plan_id}"
    if suite_id:
        u += f"&suiteId={suite_id}"
    return u
