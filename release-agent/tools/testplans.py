"""ADO Test-Plan operations for Phase 3 (bug_bash) — the two `clone_plans_*` steps.

Two DIFFERENT release procedures, per the team docs:

  * BROKER  — build a three-folder monthly plan. Instead of ADO's "Copy Test Plan" (which
      reproduces the master's whole 45-suite tree), create a fresh plan "Android Monthly
      Release - <Mon YYYY>" with exactly three FLAT top-level suites: (1) "Manual Tests (Android
      Broker)" — a static suite of the manual-broker cases (master 2007357 / subtree 2008656
      resolved), pinned to the two flight configs (293 ECS + 330 LocalFlights); (2) "Manual
      Tests (Native Auth)" — a single dynamic (query) suite carrying the master's Native-Auth
      tag query (its 2864589 subtree, flattened); and (3) "UI Automation (Android Broker)" — a
      static suite of all distinct UI-automation cases (master's 2007399 subtree resolved and
      flattened), pinned to the UI folder's flight configs. All three are FLAT (no nested
      folders); cases are REFERENCED (shared, not duplicated) — the classic Test Suite Clone is
      avoided because it COPIES cases (see `_native_auth_query`).
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
from tools.coordinates import coords

ORG = P.ENGINEERING_ORG          # https://identitydivision.visualstudio.com
PROJECT = P.ENGINEERING_PROJECT  # Engineering
_API = "api-version=7.1"

# Test-plan coordinates come from config/coordinates.yaml (constant NAMES unchanged).
_BROKER = coords.testplan("broker")
_AUTH = coords.testplan("authenticator")

# ---- Broker: master template + the monthly copy ----
BROKER_MASTER_PLAN = _BROKER["plan"]
BROKER_MASTER_ROOT_SUITE = _BROKER["root_suite"]
# The "Manual Tests (Android Broker)" subtree of the master — the manual bug-bash tests.
# The monthly copy FLATTENS this subtree into a single static suite.
BROKER_MANUAL_ROOT_SUITE = _BROKER["manual_root_suite"]
BROKER_MANUAL_SUITE_NAME = "Manual Tests (Android Broker)"
# Test configurations the manual bug bash runs each case under (the two flight pipelines):
#   293 = "RC MSAL - RC Broker"              (ECS flights)
#   330 = "RC MSAL - RC Broker (LocalFlights)" (Local flights)
# Assigned explicitly so the flat suite gets exactly 2 points/case (matches the master's
# matrix) instead of inheriting the project's ~190 default configurations.
BROKER_CONFIGS = list(_BROKER["configs"])
# The "Manual Tests (Native Auth)" and "UI Automation (Android Broker)" subtrees of the master
# are FLATTENED into single suites: Native Auth -> one dynamic (tag-query) suite; UI Automation
# -> one static suite of all its distinct cases. So the monthly plan has THREE FLAT top-level
# suites: Manual Broker (static), Native Auth (dynamic), UI Automation (static). Cases are
# referenced, never copied.
BROKER_NATIVE_AUTH_ROOT_SUITE = _BROKER["native_auth_root_suite"]
BROKER_NATIVE_AUTH_SUITE_NAME = "Manual Tests (Native Auth)"
BROKER_UI_ROOT_SUITE = _BROKER["ui_root_suite"]
BROKER_UI_SUITE_NAME = "UI Automation (Android Broker)"
# Test configurations the flat UI-automation suite runs each case under — the ECS + LocalFlight
# matrix (the UI root itself only carries the two ECS configs, so we pin explicitly to also cover
# LocalFlight). 4 points/case:
#   292 = "PROD MSAL - RC Broker (ECS)"          294 = "RC MSAL - PROD Broker (ECS)"
#   328 = "PROD MSAL - RC Broker (LocalFlights)" 344 = "RC MSAL - PROD Broker (LocalFlight)"
BROKER_UI_CONFIGS = list(_BROKER["ui_configs"])
BROKER_AREA_PATH = _BROKER["area"]
BROKER_ITERATION = _BROKER["iteration"]

# ---- Authenticator: standing plan the query-suite hangs under ----
AUTH_PLAN = _AUTH["plan"]
AUTH_ROOT_SUITE = _AUTH["root_suite"]
AUTH_AREA_PATH = _AUTH["area"]

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

def _set_suite_configs(pid, suite_id, config_ids, timeout=60):
    """Pin a suite to explicit test configurations. IMPORTANT: the testplan (7.1) suite
    POST/PATCH silently IGNORE `defaultConfigurations` (return 200, configs stay empty) — the
    only surface that actually persists them is the CLASSIC test API PATCH. Returns (ok, detail)."""
    if not config_ids:
        return (True, "")
    url = f"{ORG}/{PROJECT}/_apis/test/Plans/{pid}/suites/{suite_id}?api-version=5.0"
    body = {"inheritDefaultConfigurations": False,
            "defaultConfigurations": [{"id": c} for c in config_ids]}
    ok, _j, d = P._ado_rest_send(url, "PATCH", body, timeout)
    return (ok, d)


def _create_suite(pid, parent_id, name, configs, suite_type="staticTestSuite",
                  query=None, inherit=False, timeout=120):
    """Create one suite under `parent_id`. When `inherit` is False and `configs` are given, the
    suite is pinned to exactly those configs (via the classic-API PATCH, since the create POST
    won't persist them); when `inherit` is True the suite inherits its parent's configs.
    Returns (ok, suite_id, detail)."""
    body = {"suiteType": suite_type, "name": name, "parentSuite": {"id": parent_id},
            "inheritDefaultConfigurations": bool(inherit)}
    if query is not None:
        body["queryString"] = query
    ok, sj, d = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/suites?api-version=7.1-preview.1",
        "POST", body, timeout)
    if not ok:
        return (False, None, d)
    sid = (sj or {}).get("id")
    if not sid:
        return (False, None, f"suite '{name}' create returned no id")
    if not inherit and configs:
        okc, dc = _set_suite_configs(pid, sid, configs, timeout)
        if not okc:
            return (False, None, f"suite '{name}' config-pin failed: {dc}")
    return (True, sid, "")


def _add_cases(pid, suite_id, case_ids, configs, timeout=120):
    """Add `case_ids` to `suite_id`, each with a point assignment per config. (ok, detail)."""
    if not case_ids:
        return (True, "")
    body = [{"workItem": {"id": int(cid)},
             "pointAssignments": [{"configurationId": c} for c in configs]}
            for cid in case_ids]
    ok, _j, d = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/Suites/{suite_id}/TestCase"
        f"?api-version=7.1-preview.3", "POST", body, max(timeout, 120))
    return (ok, d)


def _fetch_source_suites(timeout=120):
    """All suites of the master plan as ({id: suite}, {parent_id: [child_ids]}). One paged read;
    used to walk a subtree's hierarchy without re-querying per node. Returns (ok, by_id, children, detail)."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{BROKER_MASTER_PLAN}/suites?{_API}", timeout)
    if not ok:
        return (False, None, None, d)
    by_id = {s["id"]: s for s in suites}
    children = {}
    for s in suites:
        p = (s.get("parentSuite") or {}).get("id")
        children.setdefault(p, []).append(s["id"])
    return (True, by_id, children, "")


def _suite_full(suite_id, timeout=60):
    """Full properties of a master suite (name, suiteType, inheritDefaultConfigurations,
    defaultConfigurations, queryString). Returns (ok, suite_json, detail)."""
    return P._ado_rest_get(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{BROKER_MASTER_PLAN}/suites/{suite_id}?{_API}", timeout)


def _add_case_refs(pid, suite_id, case_ids, timeout=120):
    """REFERENCE `case_ids` into `suite_id` (shared work items — no copies). Points are created
    for the suite's own configs (pinned/inherited before this call). (ok, detail)."""
    if not case_ids:
        return (True, "")
    body = [{"workItem": {"id": int(cid)}} for cid in case_ids]
    ok, _j, d = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/Suites/{suite_id}/TestCase"
        f"?api-version=7.1-preview.3", "POST", body, max(timeout, 120))
    return (ok, d)


def _native_auth_query(timeout=120):
    """The tag-driven WIQL of the master's Native-Auth dynamic suite (the query suite somewhere
    under BROKER_NATIVE_AUTH_ROOT_SUITE). We flatten the Native-Auth folder into a single dynamic
    suite carrying this query. Returns (ok, query, detail).

    NOTE: this deliberately AVOIDS ADO's classic Test Suite Clone
    (`_apis/test/.../cloneoperation`) — that API COPIES the test-case work items (verified: it
    duplicated 81 tagged Native-Auth cases in a controlled run), and because Native Auth is a
    tag-driven dynamic suite those copies re-match the tag query and the count explodes (the
    81→162→324→648 corruption). Referencing the shared cases (static case-refs / a re-used
    query) never creates new work items."""
    oks, _by_id, children, d = _fetch_source_suites(timeout)
    if not oks:
        return (False, None, d)
    stack = [BROKER_NATIVE_AUTH_ROOT_SUITE]
    while stack:
        sid = stack.pop()
        okf, s, _ds = _suite_full(sid, timeout)
        if okf and s and s.get("suiteType") == "dynamicTestSuite" and s.get("queryString"):
            return (True, s["queryString"], "")
        stack.extend(children.get(sid, []))
    return (False, None, "no dynamic query suite found under the Native-Auth root")


def build_broker_plan(dest_name, timeout=120):
    """Build the release's Broker test plan and return (ok, new_plan_id, detail).

    Instead of ADO's "Copy Test Plan" (which reproduces the master's whole 45-suite
    hierarchy), this creates a fresh plan with exactly THREE FLAT top-level suites:

      • "Manual Tests (Android Broker)" — a static suite of the manual-broker cases (the
        master's 2008656 subtree resolved/flattened), pinned to the two flight configs
        (293 ECS + 330 LocalFlights) → 2 points/case. Easy to track for the bug bash.
      • "Manual Tests (Native Auth)" — a single dynamic (query) suite carrying the master's
        Native-Auth tag query, so its cases show directly (no extra folder level). Referenced.
      • "UI Automation (Android Broker)" — a static suite of all distinct UI-automation cases
        (the master's 2007399 subtree resolved/flattened), pinned to the ECS + LocalFlight
        matrix (BROKER_UI_CONFIGS) → 4 points/case.

    All three are FLAT (no nested folders) and all cases are REFERENCED (shared, not duplicated)
    — see `_native_auth_query` for why the classic Test Suite Clone is deliberately avoided.
    Downstream (distribute_tests, gather_progress) find the flat Broker suite by name, so this is
    a drop-in. On any partial failure the half-built plan is best-effort deleted so a re-run
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

    # 1b) mirror the master root's default configs onto the new plan's root, so the replicated
    # inherit=True suites (e.g. the Native-Auth dynamic suite) resolve to the SAME test-point
    # matrix as the master. Best-effort: if the master root inherits, nothing to pin.
    okr, rsrc, _dr = _suite_full(BROKER_MASTER_ROOT_SUITE, timeout)
    if okr and rsrc and not rsrc.get("inheritDefaultConfigurations"):
        rcfg = [c.get("id") for c in (rsrc.get("defaultConfigurations") or [])]
        okrc, drc = _set_suite_configs(pid, root, rcfg, timeout)
        if not okrc:
            return _cleanup(f"could not pin new-plan root configs {rcfg}: {drc}")

    # 2) FLAT Broker suite (all manual-broker cases resolved from the master subtree)
    okb, broker_suite, db = _create_suite(pid, root, BROKER_MANUAL_SUITE_NAME,
                                          BROKER_CONFIGS, timeout=timeout)
    if not okb:
        return _cleanup(f"Broker suite create failed: {db}")
    okc, cases, dc = D.broker_manual_cases(BROKER_MASTER_PLAN, BROKER_MANUAL_ROOT_SUITE, timeout)
    if not okc:
        return _cleanup(f"could not resolve master manual-broker cases: {dc}")
    if not cases:
        return _cleanup("master 'Manual Tests (Android Broker)' subtree resolved to 0 cases")
    oka, da = _add_cases(pid, broker_suite, [c["id"] for c in cases], BROKER_CONFIGS, timeout)
    if not oka:
        return _cleanup(f"adding {len(cases)} Broker cases failed: {da}")

    # 3) Native Auth — FLAT: a single dynamic (query) suite carrying the master's tag query, so
    # the cases show DIRECTLY under "Manual Tests (Native Auth)" (no extra folder level). The
    # query references the shared cases (no copies), inheriting the plan-root config.
    okq, na_query, dq = _native_auth_query(timeout)
    if not okq:
        return _cleanup(f"could not resolve the Native-Auth query: {dq}")
    okna, _na, dna = _create_suite(pid, root, BROKER_NATIVE_AUTH_SUITE_NAME, [],
                                   suite_type="dynamicTestSuite", query=na_query,
                                   inherit=True, timeout=timeout)
    if not okna:
        return _cleanup(f"Native Auth flat suite create failed: {dna}")

    # 4) UI Automation — FLAT: one static suite of all distinct UI-automation cases (referenced),
    # pinned to the full ECS + LocalFlight matrix (BROKER_UI_CONFIGS). Flat like the other two.
    okui, ui_suite, dui = _create_suite(pid, root, BROKER_UI_SUITE_NAME, BROKER_UI_CONFIGS,
                                        timeout=timeout)
    if not okui:
        return _cleanup(f"UI Automation flat suite create failed: {dui}")
    okuc, ui_cases, duc = D.broker_manual_cases(BROKER_MASTER_PLAN, BROKER_UI_ROOT_SUITE, timeout)
    if not okuc:
        return _cleanup(f"could not resolve UI-automation cases: {duc}")
    if ui_cases:
        okua, dua = _add_case_refs(pid, ui_suite, [c["id"] for c in ui_cases], timeout)
        if not okua:
            return _cleanup(f"referencing {len(ui_cases)} UI cases failed: {dua}")

    return (True, pid, "")


def _find_suite_by_name(plan_id, name, timeout=90):
    """The id of the suite named `name` in `plan_id` (case-insensitive), or None. (ok, sid, detail)."""
    ok, suites, d = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/testplan/Plans/{plan_id}/suites?{_API}", timeout)
    if not ok:
        return (False, None, d)
    want = (name or "").strip().lower()
    for s in suites:
        if (s.get("name") or "").strip().lower() == want:
            return (True, s.get("id"), "")
    return (True, None, "")


def _set_points_outcome(plan_id, suite_id, point_ids, outcome, timeout=90, chunk=40):
    """Set the manual outcome ('Passed' | 'Failed' | 'NotApplicable') on many test points at
    once. The classic points PATCH accepts a comma-separated id list (one shared outcome), so we
    chunk to keep the URL length safe — turning hundreds of single PATCHes into a handful.
    (ok, detail)."""
    ids = [str(i) for i in point_ids]
    for i in range(0, len(ids), chunk):
        batch = ",".join(ids[i:i + chunk])
        url = (f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{suite_id}"
               f"/points/{batch}?api-version=5.0")
        ok, _j, d = P._ado_rest_send(url, "PATCH", {"outcome": outcome}, timeout)
        if not ok:
            return (False, d)
    return (True, "")


# How each of the flat UI suite's flight-configs is fed from the pipelines: (flightProvider,
# MSAL-variant). An MRWP build is one flight (ECS/Local); its runs split into PROD-MSAL / RC-MSAL.
UI_CONFIG_FLIGHT_VARIANT = {
    292: ("ECS", "prod"),      # PROD MSAL - RC Broker (ECS)
    294: ("ECS", "rc"),        # RC MSAL - PROD Broker (ECS)
    328: ("Local", "prod"),    # PROD MSAL - RC Broker (LocalFlights)
    344: ("Local", "rc"),      # RC MSAL - PROD Broker (LocalFlight)
}


def fill_ui_automation_results(plan_id, verdicts, timeout=120):
    """Fill the plan's flat "UI Automation (Android Broker)" suite from per-config verdicts
    ({case_id: {(flight, variant): 'Passed'|'Failed'|'NotApplicable'}} — from
    pipelines.ui_automation_verdicts).

    Each of the suite's four flight-configs maps to a (flight, variant) via
    UI_CONFIG_FLIGHT_VARIANT, so every (case, config) test point gets the outcome of the matching
    pipeline run(s): passed if it passed there in >=1 run, failed if it really ran but never
    passed, else NotApplicable (skipped / never run for that flight+variant). Outcomes are written
    batched by value. Returns (ok, summary, detail) where summary =
      {points_total, set_passed, set_failed, set_not_applicable, cases_touched}."""
    verdicts = {int(k): v for k, v in (verdicts or {}).items()}
    oks, sid, d = _find_suite_by_name(plan_id, BROKER_UI_SUITE_NAME, timeout)
    if not oks:
        return (False, None, d)
    if not sid:
        return (False, None, f"'{BROKER_UI_SUITE_NAME}' suite not found in plan {plan_id}")
    okp, pts, dp = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{sid}/points?api-version=5.0", timeout)
    if not okp:
        return (False, None, dp)

    buckets = {"Passed": [], "Failed": [], "NotApplicable": []}
    cases_touched = set()
    for p in pts:
        try:
            cid = int((p.get("testCase") or {}).get("id"))
            cfg = int((p.get("configuration") or {}).get("id"))
        except (TypeError, ValueError):
            buckets["NotApplicable"].append(p.get("id"))
            continue
        fv = UI_CONFIG_FLIGHT_VARIANT.get(cfg)
        outcome = (verdicts.get(cid) or {}).get(fv) if fv else None
        if outcome not in ("Passed", "Failed"):
            outcome = "NotApplicable"          # skipped / no data for this flight+variant
        else:
            cases_touched.add(cid)
        buckets[outcome].append(p.get("id"))

    for outcome, point_ids in buckets.items():
        if point_ids:
            oko, do = _set_points_outcome(plan_id, sid, point_ids, outcome, timeout)
            if not oko:
                return (False, None, f"setting {len(point_ids)} points -> {outcome} failed: {do}")

    return (True, {"points_total": len(pts), "set_passed": len(buckets["Passed"]),
                   "set_failed": len(buckets["Failed"]),
                   "set_not_applicable": len(buckets["NotApplicable"]),
                   "cases_touched": len(cases_touched)}, "")


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
