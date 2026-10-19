"""Offline current-Phase-2 Authenticator partition and three-suite recovery contracts."""
from copy import deepcopy
import pytest

from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import clone_plans_auth as C, ui_results as R
from steps.bug_bash import ui_test_status as U, distribute_tests as S, send_invite as I, bugbash_updates as BU
from tests._context import context, invoke, invoke_effect, effect_context
from tests._mrwp_evidence import current_rc
from tests._auth_evidence import auth_snapshot
from tests._ui_results import broker_fill
from tools import auth_partition as A, testplans as T, pipelines as P, distribution as D, bugbash as B


def state():
    result = ReleaseState(release_id="2026-08", ccd="2026-08-13", owner_email="owner@example.test")
    rc = current_rc()
    rc["auth"] = auth_snapshot(rc=rc["rc"], rows={
        P.AUTH_UI_SUITES[0]: [("test_2916347_android", "Failed"), ("test_2916524_android", "Passed")],
        P.MONTHLY_REPORT_ONLY: [("test_9999999_monthly", "Failed")]})
    result.pipeline_runs = {"rcs": [rc]}
    result.set_step("bug_bash", "clone_plans_broker", StepState(
        status="done", data={"plan_id": 900, "ui_suite_id": 901}))
    result._checkpoint = lambda: None
    return result


class Suites:
    def __init__(self, monkeypatch):
        self.rows, self.posts = {}, []
        self.read_failure = None
        monkeypatch.setattr(T, "get_suite", self.get)
        monkeypatch.setattr(P, "_ado_rest_get_all", self.all)
        monkeypatch.setattr(P, "_ado_rest_send", self.send)

    def get(self, plan, sid, *args, **kwargs):
        if sid == self.read_failure:
            return False, None, "HTTP 503"
        return True, deepcopy(self.rows.get(int(sid))), ""

    def all(self, url, *args):
        assert "/suites?" in url
        return True, deepcopy(list(self.rows.values())), ""

    def send(self, url, method, body, timeout):
        assert method == "POST" and "/suites?" in url
        assert set(body) <= {"suiteType", "name", "parentSuite", "queryString",
                             "inheritDefaultConfigurations"}
        sid = len(self.rows) + 800
        self.posts.append(deepcopy(body))
        self.rows[sid] = {
            "id": sid, "name": body["name"], "suiteType": body["suiteType"],
            "parentSuite": deepcopy(body["parentSuite"]),
            "inheritDefaultConfigurations": body.get("inheritDefaultConfigurations", False),
            "defaultConfigurations": [],
        }
        if "queryString" in body:
            self.rows[sid]["queryString"] = body["queryString"]
        return True, {"id": sid}, ""


@pytest.fixture
def suites(monkeypatch):
    return Suites(monkeypatch)


def created(suites):
    st = state()
    outcome = invoke(C.build, st)
    assert outcome.kind == "done", outcome
    st.steps["bug_bash." + C.ID]["status"] = "done"
    return st, st.steps["bug_bash." + C.ID]["data"]


def test_current_mapping_creates_parent_and_both_inheriting_categories(suites):
    st, data = created(suites)
    assert data["layout"] == A.LAYOUT
    assert [p["name"] for p in suites.posts] == [
        "Android release/08/13/2026", "Manual Tests", "Automated Tests"]
    assert [p["suiteType"] for p in suites.posts] == [
        "staticTestSuite", "dynamicTestSuite", "dynamicTestSuite"]
    assert all(p["inheritDefaultConfigurations"] is True for p in suites.posts)
    assert all(row["inheritDefaultConfigurations"] is True for row in suites.rows.values())
    assert data["classification"]["mapped_case_ids"] == [2916347, 2916524]
    assert [p["parentSuite"]["id"] for p in suites.posts] == [T.AUTH_ROOT_SUITE, 800, 800]
    assert all(r["status"] == "ready" for r in data["creations"].values())
    assert A.target(data)["suite_id"] == data["automated_suite_id"]
    before = deepcopy(data)
    assert invoke(C.build, st).kind == "done"
    assert len(suites.posts) == 3 and data == before


@pytest.mark.parametrize("category", ["parent", "manual", "automated"])
@pytest.mark.parametrize("inheritance", ["missing", False, None, 0, 1, "true", {}, []],
                         ids=["missing", "false", "null", "zero", "one", "string", "object", "array"])
def test_ignored_or_malformed_inheritance_retains_ack_and_blocks_recreation(
        suites, monkeypatch, category, inheritance):
    st = state()
    name = {"parent": T.auth_suite_name(st.ccd), "manual": "Manual Tests",
            "automated": "Automated Tests"}[category]
    field = {"parent": "suite_id", "manual": "manual_suite_id",
             "automated": "automated_suite_id"}[category]
    send = suites.send
    def ignored(url, method, body, timeout):
        response = send(url, method, body, timeout)
        if body["name"] == name:
            row = suites.rows[response[1]["id"]]
            if inheritance == "missing":
                row.pop("inheritDefaultConfigurations")
            else:
                row["inheritDefaultConfigurations"] = deepcopy(inheritance)
        return response
    monkeypatch.setattr(P, "_ado_rest_send", ignored)
    outcome = invoke(C.build, st)
    assert outcome.kind == "blocked" and "inherit" in outcome.reason
    data = st.get_step("bug_bash", C.ID).data
    sid = data[field]
    assert data["creations"][category]["suite_id"] == sid
    assert data["creations"][category]["status"] == "created"
    count = len(suites.posts)
    assert invoke(C.build, st).kind == "blocked" and len(suites.posts) == count
    suites.rows[sid]["inheritDefaultConfigurations"] = True
    assert invoke(C.build, st).kind == "done"
    assert len(suites.posts) == 3


def test_legacy_query_suite_keeps_original_creation_and_validation(suites):
    query = T.auth_bugbash_query()
    ok, sid, detail = T.create_auth_query_suite("legacy", query)
    assert ok, detail
    assert "inheritDefaultConfigurations" not in suites.posts[0]
    for inheritance in ("missing", False, None, 0, 1, "true", {}, []):
        if inheritance == "missing":
            suites.rows[sid].pop("inheritDefaultConfigurations", None)
        else:
            suites.rows[sid]["inheritDefaultConfigurations"] = inheritance
        assert T.validate_auth_query_suite(sid, "legacy", query)[0]
        assert not T.validate_auth_suite(sid, "legacy", query)[0]


@pytest.mark.parametrize("ids", [[], [10], [10, 20]])
def test_queries_are_disjoint_exhaustive_and_keep_all_base_filters(ids):
    source = {"version": 1, "authority": A.AUTHORITY, "rc": 2,
              "apk_build_id": 3, "test_build_id": 4, "mapped_case_ids": ids}
    queries = A.queries(source)
    curated = {10, 20, 30}
    manual, auto = curated - set(ids), curated & set(ids)
    assert not manual & auto and manual | auto == curated
    for query in queries.values():
        assert query.startswith(T.auth_bugbash_query() + " and ")
        assert "AutomationStatus" not in query and "contains 'Automated'" not in query
        assert "in ()" not in query.lower()
    if ids:
        assert queries["automated"].endswith("in (" + ", ".join(map(str, ids)) + ")")
        assert "not in (" in queries["manual"]
    else:
        assert queries["automated"].endswith("[System.Id] = 0")
        assert queries["manual"].endswith("[System.Id] > 0")
    assert "contains 'ReleaseBugBash'" in queries["manual"]


def test_empty_mapping_keeps_both_categories_and_monthly_report_only(suites):
    st = state()
    st.pipeline_runs["rcs"][-1]["auth"] = auth_snapshot(rc=2, rows={
        P.MONTHLY_REPORT_ONLY: [("test_9999999_monthly", "Failed")]})
    assert invoke(C.build, st).kind == "done"
    data = st.get_step("bug_bash", C.ID).data
    assert data["classification"]["mapped_case_ids"] == []
    assert len(suites.posts) == 3
    assert st.pipeline_runs["rcs"][-1]["auth"]["test"]["suites"][P.MONTHLY_REPORT_ONLY]["pct"] == 0


def test_fresh_source_is_required_and_test_only_name_is_supported(suites):
    st = state()
    st.pipeline_runs = {}
    assert invoke(C.build, st).kind == "blocked"
    assert suites.posts == []
    st = state()
    from steps.lib import mockctx
    with mockctx.active({"name": "TEST ONLY Auth categories"}):
        assert invoke(C.build, st).kind == "done"
    assert suites.posts[0]["name"] == "TEST ONLY Auth categories"


def test_real_engine_preserves_named_test_input_and_recovers_without_duplicate_posts(suites, tmp_path):
    from tests._context import fresh_orchestrator
    from orchestrator.effects import execution_input_is_valid

    config = tmp_path / "phases.yaml"
    config.write_text(
        "version: 1\nphases:\n"
        "  - id: bug_bash\n    name: Auth categories test\n    steps:\n"
        "      - id: clone_plans_auth\n        name: Create categories\n"
        "        kind: auto\n        owner: agent\n        effect_mode: transactional\n"
        "        effect_recovery: frozen\n        effect_retry: true\n",
        encoding="utf-8")
    (tmp_path / "readiness.yaml").write_text("items: []\n", encoding="utf-8")
    st = state()
    st.steps = {}
    checkpoints = []
    st._checkpoint = lambda: checkpoints.append(deepcopy(st.steps))
    name = "TEST ONLY named engine categories"
    orch = fresh_orchestrator(str(config), st, mocks={"bug_bash.clone_plans_auth": {"name": name}})
    send = suites.send

    def lose_readback(url, method, body, timeout):
        result = send(url, method, body, timeout)
        suites.read_failure = result[1]["id"]
        return result

    from unittest.mock import patch
    with patch.object(P, "_ado_rest_send", lose_readback):
        orch.run_until_gate()
    step = st.get_step("bug_bash", C.ID)
    assert step.status == "blocked" and len(suites.posts) == 1
    assert execution_input_is_valid(C.ID, step.execution)
    original = deepcopy(step.execution)
    assert original["effect_input"]["__effect_mocks__"] == {"name": name}
    assert step.data["suite_id"] == 800
    assert step.data["creations"]["parent"]["status"] == "created"
    suites.read_failure = None
    orch.run_until_gate()
    step = st.get_step("bug_bash", C.ID)
    assert step.status == "done" and len(suites.posts) == 3
    assert suites.posts[0]["name"] == name
    assert original["operation_key"] == st.get_step("bug_bash", C.ID).data["creations"]["parent"]["operation_key"]
    assert checkpoints


def test_domain_input_removes_only_engine_metadata_without_mutating_frozen_operation():
    frozen = {"layout": A.LAYOUT, "unknown_domain_key": "must still be rejected",
              "__effect_mocks__": {"name": "TEST ONLY exact"}}
    execution = {"effect_input": deepcopy(frozen)}
    result = C._effect_input(execution)
    assert result == {"layout": A.LAYOUT, "unknown_domain_key": "must still be rejected"}
    assert execution["effect_input"] == frozen


@pytest.mark.parametrize("kind", ["staticTestSuite", "dynamicTestSuite"])
def test_native_suite_read_preserves_inheritance_and_provider_not_in_serialization(monkeypatch, kind):
    query = T.auth_bugbash_query() + " and [System.Id] not in (10, 20)" if kind == "dynamicTestSuite" else None
    raw = {"id": 800, "name": "Manual Tests" if query else "TEST ONLY native",
           "suiteType": kind, "parentSuite": {"id": T.AUTH_ROOT_SUITE},
           "inheritDefaultConfigurations": True}
    if query:
        raw["queryString"] = query.replace("[System.Id] not in", "not [System.Id] in")
    monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, deepcopy(raw), ""))
    ok, info, detail = T.validate_auth_suite(
        800, raw["name"], query, suite_type=kind)
    assert ok, detail
    assert info["inheritDefaultConfigurations"] is True
    if query:
        raw["queryString"] = raw["queryString"].replace("10, 20", "10, 21")
        assert not T.validate_auth_suite(800, raw["name"], query, suite_type=kind)[0]
    raw["inheritDefaultConfigurations"] = False
    assert not T.validate_auth_suite(800, raw["name"], query, suite_type=kind)[0]


def test_query_not_in_normalization_preserves_quoted_scenario_values():
    clause = "SELECT [System.Id] FROM WorkItems WHERE [System.Title] = 'not [System.Id] in (10)'"
    assert T._normalized_query(clause) == clause
    assert T._normalized_query(clause + " and not [System.Id] in (10, 20)") == (
        clause + " and [System.Id] not in (10, 20)")
    assert T._normalized_query(clause) != T._normalized_query(
        clause.replace("'not [System.Id] in (10)'", "'[System.Id] not in (10)'"))


@pytest.mark.parametrize("damage", ["rc", "apk", "build", "ids", "incomplete"])
def test_source_drift_holds_frozen_execution_before_any_post(suites, damage):
    st = state()
    frozen = C.prepare_effect(context(st))
    rc = st.pipeline_runs["rcs"][-1]
    if damage == "incomplete":
        rc["auth"]["test"]["complete"] = False
    else:
        new_rc = 3 if damage == "rc" else 2
        rc["rc"] = new_rc
        rc["auth"] = auth_snapshot(rc=new_rc, apk=900012 if damage == "apk" else 900010,
                                   build=900013 if damage == "build" else 900011,
                                   rows={P.AUTH_UI_SUITES[0]: [
                                       ("test_2916347_android", "Passed"),
                                       ("test_2916525_android" if damage == "ids" else
                                        "test_2916524_android", "Passed")]})
    outcome = invoke_effect(C.execute, st, {"id": "frozen", "effect_input": frozen})
    assert outcome.kind == "blocked" and suites.posts == []


def test_outcome_only_refresh_does_not_reclassify(suites):
    st, data = created(suites)
    source = deepcopy(data["classification"])
    st.pipeline_runs["rcs"][-1]["auth"] = auth_snapshot(rc=2, rows={
        P.AUTH_UI_SUITES[0]: [("test_2916347_android", "Passed"), ("test_2916524_android", "Failed")]})
    assert invoke(C.build, st).kind == "done"
    assert st.get_step("bug_bash", C.ID).data["classification"] == source
    assert len(suites.posts) == 3


@pytest.mark.parametrize("key", ["parent", "manual", "automated"])
def test_each_post_has_durable_intent_and_ack_before_readback(suites, key, monkeypatch):
    st = state()
    original_send = suites.send
    def post(url, method, body, timeout):
        category = {"Manual Tests": "manual", "Automated Tests": "automated"}.get(body["name"], "parent")
        receipt = st.get_step("bug_bash", C.ID).data["creations"][category]
        assert receipt["status"] == "creating" and receipt["operation_key"]
        response = original_send(url, method, body, timeout)
        if category == key:
            suites.read_failure = response[1]["id"]
        return response
    monkeypatch.setattr(P, "_ado_rest_send", post)
    assert invoke(C.build, st).kind == "blocked"
    data = st.get_step("bug_bash", C.ID).data
    field = {"parent": "suite_id", "manual": "manual_suite_id", "automated": "automated_suite_id"}[key]
    assert data["creations"][key]["status"] == "created"
    assert data[field] == data["creations"][key]["suite_id"]
    count = len(suites.posts)
    assert invoke(C.build, st).kind == "blocked" and len(suites.posts) == count
    suites.read_failure = None
    assert invoke(C.build, st).kind == "done"
    assert len(suites.posts) == 3


@pytest.mark.parametrize("key", ["parent", "manual", "automated"])
@pytest.mark.parametrize("reached_server", [False, True])
def test_unknown_create_never_blindly_posts_again(suites, key, reached_server, monkeypatch):
    st = state()
    original = suites.send
    failed = False
    def uncertain(url, method, body, timeout):
        nonlocal failed
        category = {"Manual Tests": "manual", "Automated Tests": "automated"}.get(body["name"], "parent")
        if category == key and not failed:
            failed = True
            if reached_server:
                original(url, method, body, timeout)
            raise RuntimeError("process crashed before acknowledgement")
        return original(url, method, body, timeout)
    monkeypatch.setattr(P, "_ado_rest_send", uncertain)
    with pytest.raises(RuntimeError, match="crashed"):
        invoke(C.build, st)
    assert st.get_step("bug_bash", C.ID).data["creations"][key]["status"] == "creating"
    before = len(suites.posts)
    outcome = invoke(C.build, st)
    if reached_server:
        assert outcome.kind == "done" and len(suites.posts) == 3
    else:
        assert outcome.kind == "blocked" and len(suites.posts) == before
        frozen = C.prepare_effect(context(st))
        decision = invoke_effect(C.authorize_retry, st, {"effect_input": frozen}, "owner verified absence")
        assert decision.allowed
        receipt = st.get_step("bug_bash", C.ID).data["creations"][key]
        assert receipt["attempt_history"][0]["reason"] == "owner verified absence"
        assert invoke(C.build, st).kind == "done" and len(suites.posts) == 3


def test_never_migrates_same_named_legacy_dynamic_suite(suites):
    suites.rows[42] = {"id": 42, "name": T.auth_suite_name("2026-08-13"),
                      "parentSuite": {"id": T.AUTH_ROOT_SUITE}, "suiteType": "dynamicTestSuite",
                      "queryString": T.auth_bugbash_query()}
    st = state()
    assert invoke(C.build, st).kind == "blocked" and suites.posts == []
    assert "suite_id" not in st.get_step("bug_bash", C.ID).data


@pytest.mark.parametrize("damage", ["layout", "no_layout", "conflict", "query", "ids", "receipt",
                                  "name", "ack", "status", "missing_source"])
def test_malformed_saved_categories_fail_closed(suites, damage):
    st, data = created(suites)
    if damage == "layout":
        data["layout"] = "unknown"
    elif damage == "no_layout":
        data.pop("layout")
    elif damage == "conflict":
        data["manual_suite_id"] = data["suite_id"]
    elif damage == "query":
        data["queries"]["manual"] += " and [System.Id] = 9"
    elif damage == "ids":
        data["classification"]["mapped_case_ids"] = [True]
    elif damage == "receipt":
        data["creations"]["manual"] = {}
    elif damage == "name":
        data["suite_name"] = "different"
    elif damage == "ack":
        data["creations"]["manual"]["suite_id"] = True
    elif damage == "status":
        data["creations"]["manual"]["status"] = "creating"
    else:
        data.pop("classification")
    assert invoke(C.build, st).kind == "blocked" and len(suites.posts) == 3
    if damage not in ("receipt", "name", "ack", "status"):
        with pytest.raises((ValueError, TypeError)):
            A.target(data)


@pytest.mark.parametrize("damage", ["parent", "query", "type", "name", "duplicate", "whitespace"])
def test_live_category_identity_and_exhaustive_duplicates_hold(suites, damage):
    st, data = created(suites)
    row = suites.rows[data["manual_suite_id"]]
    if damage == "parent":
        row["parentSuite"]["id"] = T.AUTH_ROOT_SUITE
    elif damage == "query":
        row["queryString"] = T.auth_bugbash_query()
    elif damage == "type":
        row["suiteType"] = "staticTestSuite"
    elif damage == "name":
        row["name"] = "Manual"
    elif damage == "duplicate":
        suites.rows[999] = {**deepcopy(row), "id": 999}
    else:
        row["name"] += " "
    assert invoke(C.build, st).kind == "blocked" and len(suites.posts) == 3


def category_points():
    return [
        {"id": 11, "testCase": {"id": 2916347}, "configuration": {"id": 84}, "outcome": "Unspecified"},
        {"id": 12, "testCase": {"id": 2916524}, "configuration": {"id": 84}, "outcome": "Unspecified"},
    ]


def point_provider(monkeypatch, target, points):
    writes = []
    def read(url, *args):
        assert f"/Suites/{target['suite_id']}/points?" in url
        return True, deepcopy(points), ""
    def send(url, method, body, timeout):
        assert method == "PATCH" and f"/Suites/{target['suite_id']}/points/" in url
        ids = set(map(int, url.split("/points/")[1].split("?")[0].split(",")))
        writes.append(ids)
        for point in points:
            if point["id"] in ids:
                point["outcome"] = body["outcome"]
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_get_all", read)
    monkeypatch.setattr(P, "_ado_rest_send", send)
    return writes


def test_fill_targets_only_automated_child_and_reuses_receipts(suites, monkeypatch):
    st, data = created(suites)
    target = A.target(data)
    manual = [{"id": 33, "outcome": "Passed", "case_id": 55, "tester": "human"}]
    before = deepcopy(manual)
    points = category_points()
    writes = point_provider(monkeypatch, target, points)
    kwargs = {"target": target}
    ok, receipt, detail = T.fill_auth_ui_results(
        T.AUTH_PLAN, target["suite_id"], {2916347: "Failed", 2916524: "Passed"}, **kwargs)
    assert ok, detail
    assert receipt["target"] == target and manual == before
    assert all(p["config_id"] == 84 for p in receipt["applied_points"])
    assert len(writes) == 2
    assert T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"], {
        2916347: "Failed", 2916524: "Passed"}, prior_points=receipt["applied_points"], **kwargs)[0]
    assert len(writes) == 2
    points[0]["outcome"] = "Passed"
    assert not T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"], {
        2916347: "Failed"}, prior_points=receipt["applied_points"], **kwargs)[0]
    assert len(writes) == 2 and manual == before


@pytest.mark.parametrize("damage", ["case", "config", "point", "target", "replacement"])
def test_fill_changed_points_or_targets_preserve_human_results(suites, monkeypatch, damage):
    st, data = created(suites)
    target = A.target(data)
    points = category_points()
    writes = point_provider(monkeypatch, target, points)
    ok, receipt, _ = T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"], {2916347: "Failed"}, target=target)
    assert ok
    writes.clear()
    if damage == "case":
        points[0]["testCase"]["id"] = 55
    elif damage == "config":
        points[0]["configuration"]["id"] = 85
    elif damage == "point":
        points[0]["id"] = 45
    elif damage == "replacement":
        points[0]["id"], points[0]["outcome"] = 45, "Unspecified"
    else:
        target["manual_suite_id"] = target["suite_id"]
    assert not T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"], {
        2916347: "Passed"}, target=target, prior_points=receipt["applied_points"])[0]
    assert writes == []


def test_binding_publication_and_human_preconditions_include_all_category_identities(suites, monkeypatch):
    st, data = created(suites)
    target = A.target(data)
    monkeypatch.setattr(D, "validate_release_targets", lambda *a, **kw: None)
    monkeypatch.setattr(D, "set_point_testers", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)
    points = category_points()
    point_provider(monkeypatch, target, points)
    assert invoke(U.build, st).kind == "done"
    completed = R.completed_result(context(st))
    assert completed["auth"]["target"] == target
    assert completed["auth"]["automated_case_ids"] == [2916347, 2916524]
    assert completed["auth"]["failed_case_ids"] == [2916347]
    monkeypatch.setattr(P, "project_auth_ui_results", lambda *a: pytest.fail("Downstream must not reproject"))
    monkeypatch.setattr(P, "project_mrwp_ui_results", lambda *a: pytest.fail("Downstream must not reproject"))
    assert R.completed_result(context(st)) == completed
    assert S._fill_input(context(st))[0] == [2916347, 2916524]
    assert BU.plan_links(context(st))[1]["url"] == T.plan_web_url(T.AUTH_PLAN, data["suite_id"])
    paths = {tuple(p["path"]) for p in R.reminder_preconditions(
        effect_context(U.execute, st, {"effect_input": {}}))}
    for field in A.FIELDS:
        assert ("steps", "bug_bash.clone_plans_auth", "data", field) in paths
    matches = I.source_matches(context(st), C.ID)
    I._check_source_matches(context(st), {"state_matches": matches})
    data["manual_suite_id"] = 999
    with pytest.raises(ValueError):
        R.completed_result(context(st))
    with pytest.raises(ValueError):
        I._check_source_matches(context(st), {"state_matches": matches})


def test_distribution_reads_actual_category_cases_not_phase2_or_base_wiql(suites, monkeypatch):
    st, data = created(suites)
    target = A.target(data)
    manual = [{"id": 33, "testCase": {"id": 55}, "configuration": {"id": 84}}]
    auto = category_points()
    def points(url, *args):
        assert "/points?" in url
        return True, deepcopy(manual if f"/Suites/{target['manual_suite_id']}/" in url else auto), ""
    monkeypatch.setattr(P, "_ado_rest_get_all", points)
    monkeypatch.setattr(D, "_wiql_ids", lambda *a: pytest.fail("Category work must come from actual points"))
    monkeypatch.setattr(D, "_tags_of", lambda ids, *a: (
        True, {str(cid): {"tags": ["Automated"]} for cid in ids}, ""))
    ok, cases, detail = D.auth_bugbash_cases(target=target)
    assert ok, detail
    assert {c["id"] for c in cases} == {"55", "2916347", "2916524"}
    manual[0]["testCase"]["id"] = 2916347
    assert not D.auth_bugbash_cases(target=target)[0]


def test_progress_uses_categories_retains_failed_investigations_and_manual_resolution(suites, monkeypatch):
    st, data = created(suites)
    target = A.target(data)
    receipt = {"target": target, "applied_points": [
        {"point_id": 11, "case_id": 2916347, "config_id": 84, "outcome": "Failed"}],
        "failed_case_ids": [2916347]}
    def row(pid, cid, outcome):
        return {"point_id": str(pid), "case_id": str(cid), "config_id": "84", "name": str(cid),
                "outcome": outcome, "tester_upn": "tester@example.test"}
    manual = [row(30, 55, "Passed"), row(31, 56, "Unspecified")]
    auto = [row(11, 2916347, "Passed"), row(12, 2916524, "Passed")]
    monkeypatch.setattr(B, "_points_of_suite", lambda plan, sid, *a: (
        True, deepcopy(manual if sid == target["manual_suite_id"] else auto), ""))
    monkeypatch.setattr(B, "_broker_points", lambda *a: (True, [], ""))
    monkeypatch.setattr(B, "_broker_ui_triage_points", lambda *a: (True, [], ""))
    ok, progress, detail = B.gather_progress(900, "manual", T.AUTH_PLAN, data["suite_id"],
        auth_automated_ids=[2916347, 2916524], auto_failed_ids=[2916347],
        broker_ui_result={}, auth_ui_result=receipt)
    assert ok, detail
    assert (progress["total"], progress["done"], progress["remaining"]) == (3, 2, 1)
    assert progress["auto_failed_remaining"] == 0
    assert {t["id"] for t in progress["owners"]["tester@example.test"]["tests"]} == {
        "55", "56", "2916347"}
    auto[0]["config_id"] = "85"
    assert not B.gather_progress(900, "manual", T.AUTH_PLAN, data["suite_id"],
        auth_automated_ids=[2916347, 2916524], auto_failed_ids=[2916347],
        broker_ui_result={}, auth_ui_result=receipt)[0]


def destination_provider(suites, monkeypatch):
    monkeypatch.setattr(T, "get_plan", lambda *a: (True, {
        "id": 900, "name": T.broker_plan_name("2026-08"), "rootSuiteId": 899,
        "areaPath": T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION}, ""))
    get = suites.get
    def suite(plan, sid, *args, **kwargs):
        if plan == 900:
            return True, {"id": 901, "name": T.BROKER_UI_SUITE_NAME,
                          "suiteType": "staticTestSuite", "parentSuite": {"id": 899}}, ""
        return get(plan, sid, *args, **kwargs)
    monkeypatch.setattr(T, "get_suite", suite)


@pytest.mark.parametrize("category", ["parent", "manual", "automated"])
@pytest.mark.parametrize("damage", ["name", "type", "parent", "query", "duplicate",
                                  "inheritance_missing", "inheritance_false", "inheritance_malformed"])
def test_destination_validation_checks_full_hierarchy(suites, monkeypatch, category, damage):
    st, data = created(suites)
    destination_provider(suites, monkeypatch)
    binding = R.current_binding(context(st))
    D.validate_release_targets(st.release_id, st.ccd, binding)
    sid = data[{"parent": "suite_id", "manual": "manual_suite_id",
                "automated": "automated_suite_id"}[category]]
    row = suites.rows[sid]
    if damage == "name":
        row["name"] += " "
    elif damage == "type":
        row["suiteType"] = "requirementTestSuite"
    elif damage == "parent":
        row["parentSuite"]["id"] = 999
    elif damage == "query":
        row["queryString"] = "select [System.Id] from WorkItems"
    elif damage == "inheritance_missing":
        row.pop("inheritDefaultConfigurations")
    elif damage == "inheritance_false":
        row["inheritDefaultConfigurations"] = False
    elif damage == "inheritance_malformed":
        row["inheritDefaultConfigurations"] = "true"
    else:
        suites.rows[999] = {**deepcopy(row), "id": 999}
    with pytest.raises(ValueError, match="Authenticator"):
        D.validate_release_targets(st.release_id, st.ccd, binding)
    monkeypatch.setattr(T, "fill_ui_automation_results", lambda *a, **kw: pytest.fail("No Broker write"))
    monkeypatch.setattr(T, "fill_auth_ui_results", lambda *a, **kw: pytest.fail("No Authenticator write"))
    assert invoke(U.build, st).kind == "blocked"


@pytest.mark.parametrize("damage", ["rc", "build", "ids"])
def test_fill_mapping_drift_holds_before_either_product_writes(suites, monkeypatch, damage):
    st, data = created(suites)
    rc = st.pipeline_runs["rcs"][-1]
    if damage == "rc":
        rc["rc"] = 3
    rc["auth"] = auth_snapshot(rc=rc["rc"], build=900012 if damage == "build" else 900011,
        rows={P.AUTH_UI_SUITES[0]: [("test_2916347_android", "Failed"),
              ("test_2916525_android" if damage == "ids" else "test_2916524_android", "Passed")]})
    monkeypatch.setattr(T, "fill_ui_automation_results", lambda *a, **kw: pytest.fail("No Broker write"))
    monkeypatch.setattr(T, "fill_auth_ui_results", lambda *a, **kw: pytest.fail("No Authenticator write"))
    assert invoke(U.build, st).kind == "blocked"


@pytest.mark.parametrize("config_drift", [False, True])
def test_distribution_reads_manual_work_and_exact_failed_auto_points(suites, monkeypatch, config_drift):
    st, data = created(suites)
    target = A.target(data)
    fill = {"auth": {"target": target, "automated_case_ids": [2916347, 2916524],
                    "applied_points": [{"point_id": 11, "case_id": 2916347,
                                        "config_id": 84, "outcome": "Failed"}]},
            "broker": {"target": {"plan_id": 900, "suite_id": 901}, "applied_points": []}}
    def group(sid, rows):
        return {"plan_id": T.AUTH_PLAN, "suite_id": sid, "points": [
            {"id": pid, "case_id": str(cid), "config_id": str(cfg),
             "tester_id": "human", "tester_upn": "tester@example.test"} for pid, cid, cfg in rows]}
    observed = []
    def points(plan, sid, ids):
        observed.append((sid, list(ids)))
        rows = ([(33, 55, 84)] if sid == target["manual_suite_id"] else
                [(11, 2916347, 85 if config_drift else 84), (12, 2916347, 85)])
        return True, [group(sid, rows)] if plan == T.AUTH_PLAN else [], ""
    monkeypatch.setattr(S, "completed_result", lambda *a: deepcopy(fill))
    monkeypatch.setattr(D, "find_suite_id_by_name", lambda *a: (True, 899, ""))
    monkeypatch.setattr(D, "read_point_testers", points)
    args = (context(st), ["A:55", "A:2916347"], {"broker": {"suite_name": "Manual"}}, set(),
            {"A:2916347": {"reasons": ["failed_automation"]}})
    if config_drift:
        with pytest.raises(ValueError, match="configuration"):
            S._point_sets(*args)
    else:
        groups = S._point_sets(*args)
        assert [(g["suite_id"], [p["id"] for p in g["points"]]) for g in groups] == [
            (target["manual_suite_id"], [33]), (target["suite_id"], [11])]
        assert observed[-2:] == [(target["manual_suite_id"], ["55"]), (target["suite_id"], ["2916347"])]


def test_invitation_links_parent_and_binds_children_without_publishing(suites):
    st, data = created(suites)
    from steps.lib import mockctx
    with mockctx.active({"now": "2026-08-14T10:00:00-07:00", "flags": "{}"}):
        invitation = invoke(I.build, st)
    assert getattr(invitation, "outbound", False), invitation
    assert I.I.testplan_url(T.AUTH_PLAN, data["suite_id"]) in invitation.payload["body"]
    paths = {tuple(m["path"]) for m in invitation.notification["state_matches"]}
    assert ("steps", "bug_bash.clone_plans_auth", "data", "manual_suite_id") in paths
    assert ("steps", "bug_bash.clone_plans_auth", "data", "automated_suite_id") in paths
    assert len(suites.posts) == 3


def test_not_applicable_mapping_is_automated_and_outcome_refresh_reuses_identity(suites, monkeypatch):
    st = state()
    st.pipeline_runs["rcs"][-1]["auth"] = auth_snapshot(rc=2, rows={
        P.AUTH_UI_SUITES[0]: [("test_2916347_android", "NotApplicable")]})
    assert invoke(C.build, st).kind == "done"
    target = A.target(st.get_step("bug_bash", C.ID).data)
    assert target["classification"]["mapped_case_ids"] == [2916347]
    points = category_points()[:1]
    writes = point_provider(monkeypatch, target, points)
    ok, receipt, detail = T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"], {}, target=target)
    assert ok and not receipt["applied_points"] and not writes, detail
    ok, receipt, detail = T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"],
                                               {2916347: "Failed"}, target=target)
    assert ok, detail
    ok, refreshed, detail = T.fill_auth_ui_results(T.AUTH_PLAN, target["suite_id"],
        {2916347: "Passed"}, target=target, prior_points=receipt["applied_points"])
    assert ok and refreshed["target"] == receipt["target"], detail
    assert points[0]["outcome"] == "Passed" and len(writes) == 2
