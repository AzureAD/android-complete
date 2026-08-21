"""ADO Test-Plan operations for Phase 3 (bug_bash) — the two `clone_plans_*` steps.

Two DIFFERENT release procedures, per the team docs:

  * BROKER  — COPY the master test plan (a real ADO "Copy Test Plan" / CloneOperation).
      Master: plan 2007357 / rootSuite 2007358 ("Android Monthly Release Master Test
      Plan", area 'Engineering\\Auth Client\\Broker\\Android'). Each release clones it to
      a new plan "Android Monthly Release - <Mon YYYY>", REFERENCING the existing test
      cases (the ADO clone default — it shares test cases, doesn't duplicate them).
      Doc: eng.ms/.../internal-release-checklist/test-plans

  * AUTHENTICATOR — CREATE a new query-based (dynamic) test suite under the standing
      "MSAuthenticator Test Passes" plan (714514 / rootSuite 714515), named after the
      release "Android/release/MM/YYYY", whose WIQL selects the Android bug-bash test
      cases (tag 'Android', not Closed, not 'IgnoreOnPrem' — mirrors the live suite
      3016608 "Android/release/08/2024"). We STOP after creating the suite — assigning
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

# ---- Broker: master template to clone ----
BROKER_MASTER_PLAN = 2007357
BROKER_MASTER_ROOT_SUITE = 2007358
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


def auth_suite_name(release_id: str) -> str:
    """The Authenticator query-suite name, e.g. 'Android/release/08/2026' — mirrors the
    live convention (newest suite 'Android/release/08/2024')."""
    y, m = _split_release(release_id)
    return f"Android/release/{m:02d}/{y}"


def auth_bugbash_query() -> str:
    """The WIQL for the Authenticator bug-bash query-suite — the Android test cases,
    excluding Closed and on-prem-only. Verbatim shape of the live suite 3016608
    ('Android/release/08/2024')."""
    return (
        "select [System.Id], [System.WorkItemType], [System.Title], "
        "[Microsoft.VSTS.Common.Priority], [System.AssignedTo], [System.AreaPath] "
        "from WorkItems where [System.TeamProject] = @project and "
        "[System.WorkItemType] in group 'Microsoft.TestCaseCategory' and "
        f"[System.AreaPath] under '{AUTH_AREA_PATH}' and "
        "[System.Tags] contains 'Android' and [System.State] <> 'Closed' and "
        "not [System.Tags] contains 'IgnoreOnPrem'")


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
    Returns (ok, suite_id_or_None, detail). Pages through the plan's suites. A best-effort
    duplicate guard for the Authenticator create."""
    want = (name or "").strip().lower()
    url = (f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?{_API}"
           f"&continuationToken=")
    token = ""
    for _ in range(50):  # hard page cap
        ok, j, d = P._ado_rest_get(url + token, timeout)
        if not ok:
            return (False, None, d)
        for s in (j or {}).get("value") or []:
            if (s.get("name") or "").strip().lower() == want:
                parent = s.get("parentSuite") or {}
                if str(parent.get("id")) == str(parent_suite_id):
                    return (True, s.get("id"), "")
        token = (j or {}).get("continuationToken") or ""
        if not token:
            break
    return (True, None, "")


# ---------------------------------------------------------------- writes

def clone_broker_plan(dest_name, timeout=120):
    """COPY the Broker master plan to a new plan `dest_name`, referencing existing test
    cases (ADO clone default). Returns (ok, new_plan_id, detail).

    Mirrors the doc's "Copy Test Plan → Reference existing test cases → Create".
    """
    url = f"{ORG}/{PROJECT}/_apis/testplan/Plans/CloneOperation?api-version=7.1-preview.2"
    body = {
        # copy every suite + hierarchy; do NOT clone requirements. Not setting a
        # test-case duplication flag => ADO REFERENCES the existing test cases.
        "cloneOptions": {"copyAllSuites": True, "copyAncestorHierarchy": False,
                         "cloneRequirements": False, "copyComments": False},
        "destinationTestPlan": {"name": dest_name, "project": PROJECT,
                                "areaPath": BROKER_AREA_PATH, "iteration": BROKER_ITERATION},
        "sourceTestPlan": {"id": BROKER_MASTER_PLAN, "suiteIds": [BROKER_MASTER_ROOT_SUITE]},
    }
    ok, j, d = P._ado_rest_send(url, "POST", body, timeout)
    if not ok:
        return (False, None, d)
    dest = (j or {}).get("destinationTestPlan") or {}
    pid = dest.get("id")
    if not pid:
        return (False, None, f"clone returned no destination plan id (state={j.get('state')})")
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
