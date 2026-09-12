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
from tools.ui_mapping import UI_CONFIG_FLIGHT_VARIANT

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
    if not isinstance(j, dict) or not j.get("id") or not j.get("rootSuite"):
        return (False, None, "plan lookup returned no id/rootSuite")
    root = (j or {}).get("rootSuite") or {}
    return (True, {"id": j.get("id"), "name": j.get("name"),
                   "areaPath": j.get("areaPath"), "iteration": j.get("iteration"),
                   "rootSuiteId": root.get("id"), "description": j.get("description", "")}, "")


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
        if not okf:
            return (False, None, _ds)
        if s and s.get("suiteType") == "dynamicTestSuite" and s.get("queryString"):
            return (True, s["queryString"], "")
        stack.extend(children.get(sid, []))
    return (False, None, "no dynamic query suite found under the Native-Auth root")


def build_broker_plan(dest_name, timeout=120, *, source, description, on_created):
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
        baseline matrix (BROKER_UI_CONFIGS), plus selectively frozen RC/RC points.

    All three are FLAT (no nested folders) and all cases are REFERENCED (shared, not duplicated)
    — see `_native_auth_query` for why the classic Test Suite Clone is deliberately avoided.
    Downstream (distribute_tests, gather_progress) find the flat Broker suite by name, so this is
    a drop-in. The caller checkpoints creation intent before calling and the returned ID
    via on_created immediately after the POST. Partial plans are retained for reconciliation,
    never silently deleted/replaced. Source is the caller's persisted pre-create snapshot.
    """
    from tools.broker_plans import _validate_source
    try:
        _validate_source(source)
        if not callable(on_created):
            raise ValueError("A creation checkpoint is required")
    except ValueError as exc:
        return False, None, str(exc)
    # 1) empty plan
    ok, j, d = P._ado_rest_send(
        f"{ORG}/{PROJECT}/_apis/testplan/plans?{_API}", "POST",
        {"name": dest_name, "areaPath": BROKER_AREA_PATH, "iteration": BROKER_ITERATION,
         "description": description},
        timeout)
    if not ok:
        return (False, None, d)
    pid = (j or {}).get("id")
    root = ((j or {}).get("rootSuite") or {}).get("id")
    if pid:
        on_created(pid)
    if not pid or not root:
        return (False, pid, "plan create returned no id/rootSuite")

    def _incomplete(reason):
        return (False, pid, f"Plan {pid} retained for recovery: {reason}")

    okrc, drc = _set_suite_configs(pid, root, source["root_configs"], timeout)
    if not okrc:
        return _incomplete(f"could not pin new-plan root configs: {drc}")

    # 2) FLAT Broker suite (all manual-broker cases resolved from the master subtree)
    okb, broker_suite, db = _create_suite(pid, root, BROKER_MANUAL_SUITE_NAME,
                                          source["broker_configs"], timeout=timeout)
    if not okb:
        return _incomplete(f"Broker suite create failed: {db}")
    cases = source["broker_cases"]
    oka, da = _add_cases(pid, broker_suite, cases, source["broker_configs"], timeout)
    if not oka:
        return _incomplete(f"adding {len(cases)} Broker cases failed: {da}")

    # 3) Native Auth — FLAT: a single dynamic (query) suite carrying the master's tag query, so
    # the cases show DIRECTLY under "Manual Tests (Native Auth)" (no extra folder level). The
    # query references the shared cases (no copies), inheriting the plan-root config.
    okna, _na, dna = _create_suite(pid, root, BROKER_NATIVE_AUTH_SUITE_NAME, [],
                                   suite_type="dynamicTestSuite", query=source["native_query"],
                                   inherit=True, timeout=timeout)
    if not okna:
        return _incomplete(f"Native Auth flat suite create failed: {dna}")

    # 4) UI Automation — FLAT: one static suite of all distinct UI-automation cases (referenced),
    # pinned to the full ECS + LocalFlight matrix (BROKER_UI_CONFIGS). Flat like the other two.
    okui, ui_suite, dui = _create_suite(pid, root, BROKER_UI_SUITE_NAME, source["ui_configs"],
                                        timeout=timeout)
    if not okui:
        return _incomplete(f"UI Automation flat suite create failed: {dui}")
    ui_cases = source["ui_cases"]
    if ui_cases:
        matrix = source.get("ui_case_configs")
        if matrix:
            # Explicit per-case assignments avoid inheriting six configs for every case.
            body = [{"workItem": {"id": cid}, "pointAssignments": [
                {"configurationId": cfg} for cfg in matrix[str(cid)]]} for cid in ui_cases]
            okua, _, dua = P._ado_rest_send(
                f"{ORG}/{PROJECT}/_apis/testplan/Plans/{pid}/Suites/{ui_suite}/TestCase"
                "?api-version=7.1-preview.3", "POST", body, timeout)
        else:
            okua, dua = _add_case_refs(pid, ui_suite, ui_cases, timeout)
        if not okua:
            return _incomplete(f"referencing {len(ui_cases)} UI cases failed: {dua}")

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


def _point_validation_error(points, *, require_config):
    """Check the entire target before the first result write, not just matched points."""
    from tools.pipelines.tests_results import _positive_test_id
    seen, pairs = set(), set()
    if not isinstance(points, list):
        return "Malformed test-point collection"
    for point in points:
        if not isinstance(point, dict):
            return "Malformed test point"
        case, config = point.get("testCase"), point.get("configuration")
        pid = _positive_test_id(point.get("id"))
        cid = _positive_test_id(case.get("id")) if isinstance(case, dict) else None
        cfg = _positive_test_id(config.get("id")) if isinstance(config, dict) else None
        if not pid or not cid or pid in seen or (require_config and not cfg):
            return "Invalid/duplicate test-point identity; no outcomes written"
        if cfg and (cid, cfg) in pairs:
            return "Duplicate case/configuration point; no outcomes written"
        seen.add(pid)
        pairs.add((cid, cfg))
    return ""


def fill_ui_automation_results(plan_id, verdicts, timeout=120, *, suite_id=None):
    """Fill the plan's flat "UI Automation (Android Broker)" suite from per-config verdicts
    ({case_id: {(flight, variant): 'Passed'|'Failed'|'NotApplicable'}} — from
    pipelines.project_mrwp_ui_results).

    Each supported configuration maps to a full (flight, MSAL/Broker combination) via
    UI_CONFIG_FLIGHT_VARIANT, so every (case, config) test point gets the outcome of the matching
    reconciled source tests. This writer never evaluates retries: distinct source failures
    already win in the projection. Explicit NA-only evidence sets NotApplicable; missing
    evidence/config mappings leave points untouched (including manual cases).
    Returns counts, mapping diagnostics and completed-write counts, also on a partial failure."""
    verdicts = {int(k): v for k, v in (verdicts or {}).items()}
    oks, sid, d = _find_suite_by_name(plan_id, BROKER_UI_SUITE_NAME, timeout)
    if not oks:
        return (False, None, d)
    if not sid:
        return (False, None, f"'{BROKER_UI_SUITE_NAME}' suite not found in plan {plan_id}")
    if suite_id is not None and int(sid) != int(suite_id):
        return False, None, "Broker UI suite identity changed; refresh clone_plans_broker before filling"
    okp, pts, dp = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{sid}/points?api-version=5.0", timeout)
    if not okp:
        return (False, None, dp)
    error = _point_validation_error(pts, require_config=True)
    if error:
        return False, None, error

    buckets = {"Passed": [], "Failed": [], "NotApplicable": []}
    cases_touched = set()
    matched = set()
    untouched = []
    for p in pts:
        cid = int(p["testCase"]["id"])
        cfg = int(p["configuration"]["id"])
        fv = UI_CONFIG_FLIGHT_VARIANT.get(cfg)
        outcome = (verdicts.get(cid) or {}).get(fv) if fv else None
        if outcome not in buckets:
            untouched.append({"point_id": p.get("id"), "case_id": cid, "config_id": cfg,
                              "reason": "unknown_config" if not fv else "no_source_verdict"})
            continue
        cases_touched.add(cid)
        matched.add((cid, fv))
        buckets[outcome].append(p.get("id"))

    summary = {"target": {"plan_id": int(plan_id), "suite_id": int(sid)}, "applied_points": [],
               "points_total": len(pts), "set_passed": 0, "set_failed": 0,
               "set_not_applicable": 0, "cases_touched": len(cases_touched),
               "untouched_points": untouched,
               "unmatched_verdicts": [
                   {"case_id": cid, "flight": fv[0], "variant": fv[1], "verdict": outcome,
                    "status": "no_matching_plan_point"}
                   for cid, values in sorted(verdicts.items()) for fv, outcome in sorted(values.items())
                   if (cid, fv) not in matched]}
    if any(row["variant"] == "rc_msal_rc_broker" for row in summary["unmatched_verdicts"]):
        return False, summary, ("Missing full-combination RC MSAL/RC Broker points. "
                                "Run broker-plan --preview-ui-repair and obtain owner-approved repair; "
                                "no outcomes were written and historical points remain untouched")
    count_key = {"Passed": "set_passed", "Failed": "set_failed", "NotApplicable": "set_not_applicable"}
    for outcome, point_ids in buckets.items():
        if point_ids:
            batch = [{"point_id": int(p["id"]), "case_id": int(p["testCase"]["id"]),
                      "config_id": int(p["configuration"]["id"]), "outcome": outcome}
                     for p in pts if p["id"] in point_ids]
            oko, do = _set_points_outcome(plan_id, sid, point_ids, outcome, timeout)
            if not oko:
                summary["incomplete_outcome"] = outcome
                summary["uncertain_points"] = batch
                return (False, summary, f"setting {len(point_ids)} points -> {outcome} failed: {do}; "
                        "earlier outcome batches remain applied; this batch may be partially applied")
            summary[count_key[outcome]] = len(point_ids)
            summary["applied_points"].extend(batch)

    return (True, summary, "")


def fill_auth_ui_results(plan_id, suite_id, case_outcomes, timeout=120):
    """Fill the Authenticator bug-bash suite's points from per-case AUTOMATED outcomes
    ({case_id: 'Passed'|'Failed'} — supplied by the ui_test_status mapping owner).

    Unlike the Broker fill, the auth suite has ONE default config (a point per case), and we
    only write the AUTOMATED cases — a point whose case isn't in `case_outcomes` is LEFT
    UNTOUCHED (it stays pending for a manual tester). Passed -> 'Passed'; Failed -> 'Failed'.
    Returns (ok, summary, detail) where summary = {points_total, set_passed, set_failed,
    failed_case_ids} — `failed_case_ids` is what the step reassigns to the release owner."""
    outcomes = {int(k): v for k, v in (case_outcomes or {}).items()}
    okp, pts, dp = P._ado_rest_get_all(
        f"{ORG}/{PROJECT}/_apis/test/Plans/{plan_id}/Suites/{suite_id}/points?api-version=5.0",
        timeout)
    if not okp:
        return (False, None, dp)
    error = _point_validation_error(pts, require_config=False)
    if error:
        return False, None, error
    buckets = {"Passed": [], "Failed": []}
    failed_cases = set()
    matched, untouched = set(), []
    for p in pts:
        cid = int(p["testCase"]["id"])
        oc = outcomes.get(cid)
        if oc not in ("Passed", "Failed"):
            untouched.append({"point_id": p.get("id"), "case_id": cid, "reason": "no_source_verdict"})
            continue                                   # manual case — leave untouched
        matched.add(cid)
        buckets[oc].append(p.get("id"))
        if oc == "Failed":
            failed_cases.add(cid)
    summary = {"target": {"plan_id": int(plan_id), "suite_id": int(suite_id)},
               "applied_points": [], "points_total": len(pts), "set_passed": 0, "set_failed": 0,
               "untouched_points": untouched,
               "unmatched_verdicts": [{"case_id": cid, "verdict": outcomes[cid]}
                                      for cid in sorted(outcomes) if cid not in matched],
               "failed_case_ids": []}
    for outcome, point_ids in buckets.items():
        if point_ids:
            batch = [{"point_id": int(p["id"]), "case_id": int(p["testCase"]["id"]), "outcome": outcome}
                     for p in pts if p["id"] in point_ids]
            oko, do = _set_points_outcome(plan_id, suite_id, point_ids, outcome, timeout)
            if not oko:
                summary.update(incomplete_outcome=outcome, uncertain_points=batch)
                return False, summary, f"setting {len(point_ids)} auth points -> {outcome} failed: {do}"
            summary["set_passed" if outcome == "Passed" else "set_failed"] = len(point_ids)
            summary["applied_points"].extend(batch)
            if outcome == "Failed":
                summary["failed_case_ids"] = sorted(failed_cases)
    return True, summary, ""


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
