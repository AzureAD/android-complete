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

def _clone_op(op_field, *keys):
    """Read a field from a CloneOperation response, which nests the live status under
    `cloneOperationResponse` (state/opId/completionDate/cloneStatistics) but the ids at
    the top level. Tries the nested object first, then the top level."""
    nested = (op_field or {}).get("cloneOperationResponse") or {}
    for k in keys:
        if nested.get(k) is not None:
            return nested.get(k)
        if (op_field or {}).get(k) is not None:
            return op_field.get(k)
    return None


def clone_broker_plan(dest_name, timeout=120, poll_secs=3, max_polls=40):
    """COPY the Broker master plan to a new plan `dest_name`, referencing existing test
    cases (ADO clone default), and WAIT for the async clone to finish. Returns
    (ok, new_plan_id, detail).

    ADO's CloneOperation is asynchronous: the POST returns a destination plan id while the
    suite/test-case copy runs in the background. We poll the operation until its state is
    'succeeded' before returning, so the plan is fully populated when the step reports done
    (a too-early read would otherwise show an empty/partial plan). Mirrors the doc's
    "Copy Test Plan → Reference existing test cases → Create".
    """
    import time
    url = f"{ORG}/{PROJECT}/_apis/testplan/Plans/CloneOperation?api-version=7.1-preview.2"
    body = {
        # copy every suite + the hierarchy; do NOT clone requirements. Not setting a
        # test-case duplication flag => ADO REFERENCES the existing test cases
        # (cloneStatistics.clonedTestCasesCount stays 0 — shared, not duplicated).
        # (copyAncestorHierarchy must be true when source suiteIds are given, else ADO 400.)
        "cloneOptions": {"copyAllSuites": True, "copyAncestorHierarchy": True,
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
        return (False, None, f"clone returned no destination plan id (state={_clone_op(j, 'state')})")
    op_id = _clone_op(j, "opId")

    # Poll the operation to completion so the plan is populated before we report done.
    if op_id:
        op_url = (f"{ORG}/{PROJECT}/_apis/testplan/Plans/CloneOperation/{op_id}"
                  f"?api-version=7.1-preview.2")
        for _ in range(max_polls):
            ok_o, op, _h, d_o = P._ado_rest_get_h(op_url, 60)
            if not ok_o:
                break                                   # can't read op → best-effort, return pid
            state = str(_clone_op(op, "state") or "").lower()
            if state in ("succeeded", "completed"):
                break
            if state == "failed":
                return (False, None, f"clone operation failed (op {op_id})")
            time.sleep(poll_secs)
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
