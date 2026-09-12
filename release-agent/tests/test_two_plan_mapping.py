"""Report/plan regression matrix; synthetic transport and temporary state only."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import ui_test_status as U, distribute_tests as D, bugbash_updates as BU
from steps.build_verify import rc_report as R, _rc_report_rendering as rendering, auth_ecs
from tests._auth_evidence import auth_snapshot, capture
from tests._mrwp_evidence import current_rc, PROD, RC
from tests._ui_results import checkpoint_memory, broker_fill, auth_fill
from tools import pipelines as P, testplans as T, broker_plans as B, distribution
from tools.ui_mapping import route_suite, config_for, CONFIG_NAMES


LTW = "LTW, RC MSAL - RC Broker (API 32)"
MONTHLY_FAILURES = [
    "present2Cards_selectBothCards_presentationCompletes",
    "simplePresentation_sharesSelfIssuedCard_presentationCompletes",
    "verifyCardInformation_viewDetailsAndActivity_displaysCorrectInfo",
    "presentationWithTwoPossibleCards_switchCards_presentationCompletes",
]


def audit_shape():
    e2e = [(f"test_{100 + i}_freshInstall", "Failed" if i >= 20 else "Passed") for i in range(26)]
    e2e += [(f"test_{100 + i}_upgrade", "Passed") for i in range(3)]
    monthly = [(f"monthly_{i}", "Passed") for i in range(329)]
    monthly += [(name, "Failed") for name in MONTHLY_FAILURES]
    return {P.AUTH_UI_SUITES[0]: e2e, P.MONTHLY_REPORT_ONLY: monthly}


def state(rc=None):
    st = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    st.pipeline_runs = {"rcs": [rc or current_rc()]}
    st.set_step("bug_bash", "clone_plans_broker", StepState(data={"plan_id": 900, "ui_suite_id": 902}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 901}))
    checkpoint_memory(st)
    return st


@pytest.mark.parametrize("suite,ecs,local,disposition", [
    (PROD, 292, 328, "exact_combination"),
    (RC, 294, 344, "exact_combination"),
    (LTW, 293, 330, "ltw_exact_combination"),
    ("RC MSAL - RC Broker (API 28)", 293, 330, "exact_combination"),
    ("Stress Tests - RC MSAL with RC Broker (API 32)", 293, 330, "stress_exact_combination"),
    ("PROD MSAL - RC BrokerHost (API 32)", 292, 328, "brokerhost_explicit_rollup"),
])
def test_complete_variant_routes_both_providers(suite, ecs, local, disposition):
    variant, why = route_suite(suite)
    assert why == disposition
    assert config_for("ECS", variant) == ecs and config_for("Local", variant) == local
    rows = {suite: [("test_100_x", "Failed")]}
    ok, p, detail = P.project_mrwp_ui_results(current_rc(ecs=rows, local=rows))
    assert ok, detail
    assert [v["sources"][0]["config_id"] for v in p["provenance"]["providers"]] == [ecs, local]


def test_unknown_combination_is_diagnosed_and_stress_failure_not_merged():
    rc = current_rc(ecs={
        "Unknown RC MSAL - Mystery Broker": [("test_100_x", "Failed")],
        "Stress Tests - RC MSAL with RC Broker (API 32)": [
            ("test_concurrentAcquireTokenSilent_withBroker", "Failed")],
        PROD: [("test_concurrentAcquireTokenSilent_withBroker", "Passed")],
    }, local={PROD: [("test_concurrentAcquireTokenSilent_withBroker", "Passed")]})
    ok, p, detail = P.project_mrwp_ui_results(rc)
    assert ok, detail
    assert p["verdicts"] == {} and len(p["failures"]) == 2
    assert p["failures"][1]["routing"] == "unknown_suite_variant"
    assert all(f["links"] and f["status"] == "unmapped" for f in p["failures"])


def test_monthly_333_report_only_all_failures_shared_by_report_fill_and_distribution(monkeypatch, tmp_path):
    rc = current_rc(rc=1)
    rc["auth"] = auth_snapshot(rows=audit_shape())
    st = state(rc)
    path = tmp_path / "state.json"
    st.save(str(path))
    st = ReleaseState.load(str(path))
    st._checkpoint = lambda: st.save(str(path))
    rc = st.pipeline_runs["rcs"][-1]
    ok, projected, detail = P.project_auth_ui_results(rc)
    assert ok, detail
    assert len(projected["cases"]) == 26
    assert sum(c["outcome"] == "Failed" for c in projected["cases"].values()) == 6
    assert projected["provenance"]["source_executions"] == 362
    assert projected["provenance"]["dispositions"] == {"mapped": 29, "intentional_report_only": 333}
    assert len(projected["failures"]) == 10
    monthly = [f for f in projected["failures"] if f["status"] == "intentional_report_only"]
    assert {f["title"] for f in monthly} == set(MONTHLY_FAILURES)
    assert all(f["case_id"] is None and f["links"] for f in monthly)
    seen, assignments = {}, []
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)

    def fill(plan, suite, outcomes):
        seen.update(outcomes)
        return auth_fill(plan, suite, outcomes)

    monkeypatch.setattr(T, "fill_auth_ui_results", fill)
    monkeypatch.setattr(distribution, "set_assigned_to",
                        lambda cid, owner: (assignments.append(cid) is None, ""))
    assert U.build(st).kind == "done"
    assert set(seen) == D._auth_automated_ids(st)
    assert BU._auto_failed_ids(st) == assignments == list(range(120, 126))
    reminder = st.get_step("bug_bash", "ui_failures")
    assert not st.is_done("bug_bash", "ui_failures")
    model = R.rc_report_model(st)
    model["release"] = st.release_id
    gate, auth = R.rc_ui_gate(model), R.auth_report_gate(model)
    from orchestrator.commands.rc_report import _format
    surfaces = [reminder.note, _format(model),
                rendering.rc_email_plain(model, {}, gate, auth, ""),
                rendering.rc_email_html(model, {}, gate, auth, "")]
    assert '<a href="' + monthly[0]["links"][0]["url"].replace("&", "&amp;") + '">' in surfaces[-1]
    for text in surfaces:
        assert "intentionally" in text and "no test-plan case map" in text
        for f in monthly:
            assert f["title"] in text and f["links"][0]["url"].replace("&", "&amp;") in text.replace(
                "&amp;", "&").replace("&", "&amp;")
    assert not gate["blocking"] and auth["blocking"]
    assert rc["auth"]["test"]["suites"][P.AUTH_UI_SUITES[0]]["passed"] == 23
    assert rc["auth"]["test"]["suites"][P.MONTHLY_REPORT_ONLY]["passed"] == 329


def test_capture_keeps_source_diagnostics():
    rows = [{"id": 1, "run_id": 22, "testCaseTitle": "test_100_upgrade", "outcome": "Failed",
             "errorMessage": "synthetic assertion", "stackTrace": "synthetic stack",
             "automatedTestStorage": "TestCase100"}]
    attempt = P.reconcile_retries(rows, include_tests=True)["test_results"][0]["attempts"][0]
    assert attempt["errorMessage"] == rows[0]["errorMessage"]
    assert attempt["stackTrace"] == rows[0]["stackTrace"]
    assert attempt["automatedTestStorage"] == rows[0]["automatedTestStorage"]


def test_report_surfaces_missing_broker_source_evidence():
    lines = rendering.source_evidence_lines(R.prepare_report_evidence({"rc": 1, "mrwp": {}}))
    assert len(lines) == 2
    assert lines[0].startswith("Broker ECS evidence unavailable:")
    assert all("refresh" in line for line in lines)


def test_monthly_execution_denominator_does_not_become_distinct_test_count():
    monthly = [(f"monthly_{i}", "Passed") for i in range(320)]
    monthly += [(name, "Failed") for name in MONTHLY_FAILURES]
    monthly += monthly[:9]
    rc = {"rc": 1, "auth": auth_snapshot(rows={P.MONTHLY_REPORT_ONLY: monthly})}
    ok, projection, detail = P.project_auth_ui_results(rc)
    assert ok, detail
    assert projection["provenance"]["source_executions"] == 333
    assert projection["provenance"]["distinct_tests"] == 324
    assert projection["provenance"]["dispositions"] == {"intentional_report_only": 324}
    assert len(projection["failures"]) == 4 and not projection["cases"]
    assert rc["auth"]["test"]["suites"][P.MONTHLY_REPORT_ONLY]["passed"] == 329
    assert sum(len(s["links"]) for s in projection["sources"]) == 333


@pytest.mark.parametrize("bad", [
    None, [None], [{"id": None}],
    [{"id": 292}, {"id": 292}],
    [{"id": 292, "values": None}],
    [{"id": 292, "values": [{"name": "x", "value": "a"}, {"name": "x", "value": "b"}]}],
])
def test_malformed_configuration_metadata_fails_closed(monkeypatch, bad):
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **k: (True, bad, ""))
    assert not B.verify_ui_configurations()[0]


def test_exact_retry_recovery_then_distinct_scenario_failure_and_determinism():
    rows = {P.AUTH_UI_SUITES[0]: [
        ("test_100_freshInstall", "Failed"), ("test_100_freshInstall", "Passed"),
        ("test_100_upgrade", "Failed"), ("test_200_retry", "Passed"), ("test_200_retry", "Failed")]}
    rc = {"rc": 1, "auth": auth_snapshot(rows=rows)}
    ok, p, detail = P.project_auth_ui_results(rc)
    assert ok, detail
    assert p["cases"][100]["outcome"] == "Failed" and p["cases"][200]["outcome"] == "Passed"
    assert [f["title"] for f in p["failures"]] == ["test_100_upgrade"]
    evidence = rc["auth"]["test"]["evidence"]
    for s in evidence["summary"]["suites"]:
        s["test_results"].reverse()
        for t in s["test_results"]:
            t["attempts"].reverse()
    evidence["summary"]["failed_suites"] = copy.deepcopy(evidence["summary"]["suites"])
    assert P.project_auth_ui_results(rc) == (True, p, "")


@pytest.mark.parametrize("path,value", [
    (("rc",), 2), (("auth", "build", "run_id"), 999), (("auth", "test", "run_id"), 999),
    (("auth", "test", "evidence"), None), (("auth", "test", "evidence", "build_id"), None),
    (("auth", "test", "evidence", "runs"), []), (("auth", "test", "complete"), False),
    (("auth", "test", "evidence", "summary", "result_entries"), 9),
])
def test_invalid_auth_evidence_blocks_before_any_external_writes(monkeypatch, path, value):
    rc = current_rc(rc=1)
    c = rc
    for key in path[:-1]:
        c = c[key]
    c[path[-1]] = value

    def forbidden(*a, **k):
        raise AssertionError("No writes on invalid source evidence")

    monkeypatch.setattr(T, "fill_auth_ui_results", forbidden)
    monkeypatch.setattr(T, "fill_ui_automation_results", forbidden)
    assert U.build(state(rc)).kind == "blocked"
    with pytest.raises(ValueError, match="refresh"):
        D._auth_automated_ids(state(rc))


def test_capture_paginates_runs_and_results_without_changing_aggregate_gate(monkeypatch):
    from tools.pipelines import tests_results as tr
    calls = []
    runs = [{"id": i + 1, "name": name, "totalTests": n, "passedTests": n - 1,
             "notApplicableTests": 0, "state": "Completed", "incompleteTests": 0,
             "build": {"id": 22}}
            for i, (name, n) in enumerate([(P.AUTH_UI_SUITES[0], 5), (P.MONTHLY_REPORT_ONLY, 7)])]

    def get(url, timeout):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        if "/results?" in url:
            rid = int(url.split("/Runs/")[1].split("/")[0])
            n = runs[rid - 1]["totalTests"]
            data = [{"id": i + 1, "testCaseTitle": f"test_{100+i}_scenario",
                     "outcome": "Failed" if i == n - 1 else "Passed"} for i in range(n)]
        elif "/runs/" in url:
            return True, runs[int(url.split("/runs/")[1].split("?")[0]) - 1], ""
        else:
            data = runs
        skip, top = int(query["$skip"][0]), int(query["$top"][0])
        return True, {"value": data[skip:skip + top]}, ""

    monkeypatch.setattr(P, "_ado_rest_get", get)
    monkeypatch.setattr(P, "_auth_test_source_build_id", lambda *a: 11)
    original = tr._test_pages
    monkeypatch.setattr(tr, "_test_pages", lambda url, timeout, **kw:
                        original(url, timeout, **{**kw, "page": 2}))
    ok, captured, detail = P.collect_auth_ui_evidence(22, 11, 1)
    assert ok, detail
    assert captured["evidence"]["summary"]["result_entries"] == 12
    assert any("$skip=2" in c and "/runs?" in c for c in calls)
    assert any("$skip=6" in c and "/results?" in c for c in calls)
    assert captured["suites"][P.MONTHLY_REPORT_ONLY]["total"] == 7


@pytest.mark.parametrize("change", ["missing-build", "wrong-build", "incomplete", "missing-results"])
def test_capture_rejects_unattributed_or_incomplete_source(monkeypatch, change):
    run = {"id": 1, "name": P.MONTHLY_REPORT_ONLY, "state": "Completed", "incompleteTests": 0,
           "totalTests": 1, "passedTests": 1, "notApplicableTests": 0, "build": {"id": 22}}
    if change == "missing-build":
        run.pop("build")
    elif change == "wrong-build":
        run["build"]["id"] = 23
    elif change == "incomplete":
        run["incompleteTests"] = 1
    monkeypatch.setattr(P, "_auth_test_source_build_id", lambda *a: 11)
    monkeypatch.setattr(P, "_test_runs", lambda *a: (True, [run], ""))
    monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, run, ""))
    monkeypatch.setattr(P, "_run_results", lambda *a, **k: (True, [], ""))
    assert not P.collect_auth_ui_evidence(22, 11, 1)[0]


def test_wrong_ltw_targets_require_preview_no_manual_or_old_results_cleared(monkeypatch):
    points = [{"id": 1, "testCase": {"id": 100}, "configuration": {"id": 294}, "outcome": "Failed"},
              {"id": 2, "testCase": {"id": 999}, "configuration": {"id": 292}, "outcome": "Passed"}]
    monkeypatch.setattr(T, "_find_suite_by_name", lambda *a: (True, 901, ""))
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, copy.deepcopy(points), ""))
    monkeypatch.setattr(B, "verify_ui_configurations", lambda *a: (True, CONFIG_NAMES, ""))
    rc = current_rc(ecs={LTW: [("test_100_x", "Failed")]})
    ok, projection, _ = P.project_mrwp_ui_results(rc)
    assert ok
    ok, summary, detail = T.fill_ui_automation_results(900, projection["verdicts"])
    assert not ok and "preview-ui-repair" in detail and summary["set_failed"] == 0
    ok, preview, detail = B.preview_ui_repair(900, rc)
    assert ok, detail
    assert preview["read_only"] and "Equal outcomes do not establish ownership" in preview["historical_cleanup"]
    changed = next(c for c in preview["changes"] if c["source"]["case_id"] == 100)
    assert changed["old_config_id"] == 294 and changed["new_config_id"] == 293
    assert changed["old_point"] == points[0] and changed["old_result_action"] == "preserve_owner_review_required"
    assert B.preview_ui_repair(900, rc)[1] == preview


def test_new_capture_invalidates_stale_evidence_while_new_test_is_running():
    from steps.lib.mockctx import active
    st = state(current_rc(rc=1))
    with active({"auth_build": {"build_id": 900010, "rc": 1, "status": "completed", "result": "succeeded"},
                 "test_build": 900012, "test_status": "inProgress"}):
        assert auth_ecs.build(st).kind == "in_progress"
    assert "auth" not in st.pipeline_runs["rcs"][-1]


def test_auth_writer_preserves_unmapped_manual_points(monkeypatch):
    points = [{"id": 1, "testCase": {"id": 100}, "outcome": "Unspecified"},
              {"id": 2, "testCase": {"id": 999}, "outcome": "Failed"}]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, points, ""))
    writes = []
    monkeypatch.setattr(T, "_set_points_outcome", lambda p, s, ids, outcome, timeout:
                        (writes.append((ids, outcome)) is None, ""))
    ok, summary, detail = T.fill_auth_ui_results(714514, 901, {100: "Passed"})
    assert ok, detail
    assert writes == [([1], "Passed")] and points[1]["outcome"] == "Failed"
    assert summary["untouched_points"][0]["point_id"] == 2


def test_source_snapshot_selects_rc_rc_configs_only_for_represented_cases(monkeypatch):
    rows = {PROD: [("test_100_x", "Passed"), ("test_101_y", "Passed")],
            LTW: [("test_100_ltw", "Failed")]}
    rc = current_rc(ecs=rows, local=rows)
    monkeypatch.setattr(T, "_suite_full", lambda *a:
                        (True, {"defaultConfigurations": [{"id": 293}]}, ""))
    monkeypatch.setattr(T, "_native_auth_query", lambda *a: (True, "SELECT [System.Id] FROM WorkItems", ""))
    monkeypatch.setattr(distribution, "broker_manual_cases",
                        lambda *a: (True, [{"id": 100}, {"id": 101}], ""))
    configs = [{"id": cid, "name": name, "state": "active", "values": []}
               for cid, name in CONFIG_NAMES.items()]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, configs, ""))
    ok, source, detail = B._snapshot(120, rc)
    assert ok, detail
    assert source["ui_case_configs"] == {
        "100": [292, 293, 294, 328, 330, 344], "101": [292, 294, 328, 344]}
    configs[0]["name"] = "wrong combination"
    assert not B._snapshot(120, rc)[0]


@pytest.mark.parametrize("point", [
    {"id": None, "testCase": {"id": 100}, "configuration": {"id": 293}},
    {"id": 1, "testCase": {}, "configuration": {"id": 293}},
    {"id": 1, "testCase": {"id": 100}, "configuration": {}},
])
def test_invalid_target_points_block_before_writes(monkeypatch, point):
    monkeypatch.setattr(T, "_find_suite_by_name", lambda *a: (True, 901, ""))
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, [point], ""))
    assert not T.fill_ui_automation_results(900, {100: {("ECS", "rc_msal_rc_broker"): "Failed"}})[0]


def test_invalid_matrix_blocks_before_plan_creation():
    source = {"root_configs": [293], "broker_configs": [293, 330], "ui_configs": T.BROKER_UI_CONFIGS,
              "broker_cases": [100], "ui_cases": [100], "native_query": "SELECT id FROM WorkItems",
              "ui_case_configs": {"100": [999]}}
    ok, pid, detail = T.build_broker_plan("test", source=source, description="test",
                                        on_created=lambda pid: None)
    assert not ok and pid is None and "configurations" in detail
