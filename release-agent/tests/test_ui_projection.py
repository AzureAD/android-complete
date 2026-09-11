"""Phase 3 consumes Phase 2's exact evidence; all external mutations are stubbed."""
import copy
import json

import pytest

from orchestrator import cli_common as C
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import ui_test_status as U
from steps.build_verify import rc_report as R
from steps.lib import mockctx
from tests._mrwp_evidence import current_rc, PROD, RC
from tests._ui_results import checkpoint_memory, auth_fill
from tools import distribution as D, pipelines as P, testplans as T


def state_for(rc):
    st = ReleaseState(release_id="projection-test", owner_email="owner@example.com")
    st.pipeline_runs = {"rcs": [rc]}
    st.set_step("bug_bash", "clone_plans_broker", StepState(data={"plan_id": 900, "ui_suite_id": 901}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 902}))
    checkpoint_memory(st)
    return st


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected external I/O")


@pytest.fixture
def offline(monkeypatch):
    # Fixture snapshots use explicit, short-lived stubs for get_test_summary's reads.
    for name in ("_ado_rest_get", "_ado_rest_send", "_ado_rest_get_all", "_test_runs", "_run_results"):
        monkeypatch.setattr(P, name, forbidden)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(D, "set_assigned_to", forbidden)


def point(cid, config=292, point_id=1):
    return {"id": point_id, "testCase": {"id": cid}, "configuration": {"id": config}}


def plan_stub(monkeypatch, points):
    calls = []
    monkeypatch.setattr(T, "_find_suite_by_name", lambda *a: (True, 901, ""))
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **k: (True, points, ""))
    monkeypatch.setattr(T, "_set_points_outcome",
                        lambda plan, suite, ids, verdict, *a:
                        (calls.append((list(ids), verdict)) or True, ""))
    return calls


def test_same_snapshot_report_and_plan_recovered_pass_persists_provenance(offline, monkeypatch, tmp_path):
    title = "test_1561125_Recovered[param=1]"
    rc = current_rc(ecs={PROD + " # first": [(title, "Failed"), (title, "Failed")],
                         PROD + " # retry": [(title, "Passed")]},
                    local={PROD: [(title, "Passed")]})
    st = state_for(rc)
    old = current_rc(ecs={PROD: [(title, "Failed")]}, rc=1)
    st.pipeline_runs["rcs"].insert(0, old)
    model = R.rc_report_model(st)
    assert R.rc_ui_gate(model)["ui_passed"] == 2
    assert not model["mrwp"]["ECS"]["failed_suites"]
    test = model["mrwp"]["ECS"]["tests"]["suites"][0]["test_results"][0]
    assert test["verdict"] == "Passed" and test["recovered"]
    assert test["outcome_counts"] == {"Failed": 2, "Passed": 1}
    assert len(test["attempts"]) == 3
    calls = plan_stub(monkeypatch, [point(1561125), point(1561125, 328, 2)])
    with mockctx.active({}):
        assert U.build(st).kind == "done"
    assert calls == [([1, 2], "Passed")]
    data = st.get_step("bug_bash", U.ID).data
    assert data["broker"]["failed_case_ids"] == []
    assert not st.get_step("bug_bash", "ui_failures").note
    provenance = data["provenance"]
    assert provenance["rc"] == 2 and provenance["count_basis"] == P.MRWP_COUNT_BASIS
    assert [p["build_id"] for p in provenance["providers"]] == [201, 202]
    assert "attempts" not in json.dumps(data)
    C.save_state(st, str(tmp_path), st.release_id)
    assert C.load_state(str(tmp_path), st.release_id).get_step("bug_bash", U.ID).data == data


def test_distinct_parameters_and_api_failures_cannot_be_hidden_by_a_sibling_pass(offline, monkeypatch):
    rc = current_rc(ecs={
        PROD: [("test_100_Login[account=1]", "Passed"), ("test_100_Login[account=2]", "Failed"),
               ("test_200_Login", "Passed"), ("test_300_Skipped", "NotExecuted"),
               ("test_400_NA", "NotApplicable"), ("test_400_Executed", "Passed")],
        PROD.replace("32", "33"): [("test_200_Login", "Failed")],
        RC: [("test_100_Login[account=2]", "Passed")]},
        local={PROD: [("test_100_Login[account=2]", "Passed")]})
    st = state_for(rc)
    # A historical success must never mask the current failures.
    st.pipeline_runs["rcs"].insert(0, current_rc(ecs={PROD: [("test_100_Login[account=2]", "Passed")]}, rc=1))
    ok, projection, detail = P.project_mrwp_ui_results(rc)
    assert ok, detail
    assert projection["verdicts"] == {
        100: {("ECS", "prod_msal_rc_broker"): "Failed", ("ECS", "rc_msal_prod_broker"): "Passed",
              ("Local", "prod_msal_rc_broker"): "Passed"},
        200: {("ECS", "prod_msal_rc_broker"): "Failed"},
        300: {("ECS", "prod_msal_rc_broker"): "NotApplicable"},
        400: {("ECS", "prod_msal_rc_broker"): "Passed"}}
    assert R.rc_ui_gate(R.rc_report_model(st))["ui_failed"] == len(projection["failures"]) == 2
    calls = plan_stub(monkeypatch, [point(100), point(100, 294, 2), point(100, 328, 3),
                                    point(200, point_id=4), point(300, point_id=5),
                                    point(400, point_id=6)])
    assignments = []
    monkeypatch.setattr(D, "set_assigned_to", lambda cid, owner: (assignments.append(cid) or True, ""))
    with mockctx.active({}):
        assert U.build(st).kind == "done"
    assert calls == [([2, 3, 6], "Passed"), ([1, 4], "Failed"), ([5], "NotApplicable")]
    assert assignments == [100, 200]
    note = st.get_step("bug_bash", "ui_failures").note
    assert "account=2" in note and "API 33" in note
    assert "account=1" not in note


@pytest.mark.parametrize("path,value", [
    (("rc",), None), (("ecs",), None), (("local",), {}),
    (("ecs", "complete"), False), (("ecs", "ran"), 0), (("ecs", "run_id"), True),
    (("local", "tests_error"), "read failed"),
    (("ecs", "tests", "count_basis"), "result_entries"),
    (("ecs", "tests", "build_id"), 999), (("ecs", "tests", "build_id"), None),
    (("ecs", "tests", "suites"), None), (("ecs", "tests", "runs"), []),
    (("ecs", "tests", "runs", 0, "name"), "Different suite"),
    (("ecs", "tests", "result_entries"), 0), (("ecs", "failed_suites"), None),
    (("ecs", "tests", "categories", "ui", "passed"), 99),
    (("ecs", "tests", "suites", 0, "test_results"), None),
    (("ecs", "tests", "suites", 0, "run_ids"), [True]),
    (("ecs", "tests", "suites", 0, "test_results", 0, "title"), ""),
    (("ecs", "tests", "suites", 0, "test_results", 0, "verdict"), "Whatever"),
    (("ecs", "tests", "suites", 0, "test_results", 0, "attempts"), []),
    (("ecs", "tests", "suites", 0, "test_results", 0, "attempts", 0, "result_id"), False),
    (("ecs", "tests", "suites", 0, "test_results", 0, "attempts", 0, "run_id"), 99),
    (("ecs", "tests", "suites", 0, "test_results", 0, "attempts", 0, "outcome"), []),
    (("ecs", "tests", "suites", 0, "test_results", 0, "outcome_counts"), {"Passed": True}),
])
def test_invalid_evidence_blocks_before_any_external_mutation(offline, monkeypatch, path, value):
    rc = current_rc()
    parent = rc
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    st = state_for(rc)
    before = copy.deepcopy(st)
    monkeypatch.setattr(T, "fill_ui_automation_results", forbidden)
    with mockctx.active({}):
        result = U.build(st)
    assert result.kind == "blocked" and "refresh Phase-2" in result.reason
    assert st.pipeline_runs == before.pipeline_runs
    assert st.get_step("bug_bash", U.ID).data["result"]["status"] == "incomplete"


def test_na_only_and_skipped_mapping_are_not_success_shaped_missing_evidence(offline, monkeypatch):
    st = state_for(current_rc(ecs={
        PROD: [("test_100_X", "NotExecuted"), ("unmapped[case=999]", "Failed")],
        "Lab Api Tests": [("lab_request_123", "Failed")],
        "Unknown UI Suite": [("test_200_KnownCase", "Passed")]},
        local={PROD: [("test_100_X", "Inconclusive")]}))
    calls = plan_stub(monkeypatch, [point(100), point(100, 328, 2), point(999, point_id=3),
                                    point(200, 999, 4)])
    with mockctx.active({}):
        result = U.build(st)
    assert result.kind == "done" and "3 source test(s) skipped mapping" in result.note
    assert calls == [([1, 2], "NotApplicable")]
    data = st.get_step("bug_bash", U.ID).data
    assert len(data["summary"]["untouched_points"]) == 2
    skipped = data["provenance"]["providers"][0]["skipped_mapping"]
    assert {s["reason"] for s in skipped} == {"missing_case_id", "unknown_suite_variant"}
    note = st.get_step("bug_bash", "ui_failures").note
    assert "unmapped[case=999]" in note and "lab_request_123" in note
    assert "_workitems/edit/999" not in note
    assert data["broker"]["failed_case_ids"] == []


def test_projection_deterministic_under_all_record_orderings(offline):
    rc = current_rc(ecs={PROD: [("test_200_Z", "Failed"), ("test_100_X[a]", "Failed"),
                                ("test_100_X[a]", "Passed"), ("test_100_X[b]", "Failed")],
                         RC: [("lab_no_id", "Passed")]})
    expected = P.project_mrwp_ui_results(rc)
    shuffled = copy.deepcopy(rc)
    for slot in ("ecs", "local"):
        tests = shuffled[slot]["tests"]
        tests["suites"].reverse()
        tests["runs"].reverse()
        for suite in tests["suites"]:
            suite["run_ids"].reverse()
            suite["test_results"].reverse()
            for test in suite["test_results"]:
                test["attempts"].reverse()
    assert P.project_mrwp_ui_results(shuffled) == expected
    assert expected[0]


def test_duplicate_normalized_ids_and_cross_provider_build_reuse_block(offline):
    rc = current_rc(ecs={PROD: [("test_100_X", "Failed"), ("test_100_X", "Passed")]})
    attempts = rc["ecs"]["tests"]["suites"][0]["test_results"][0]["attempts"]
    attempts[1]["result_id"] = "01"
    assert not P.project_mrwp_ui_results(rc)[0]
    rc = current_rc()
    rc["local"] = copy.deepcopy(rc["ecs"])
    assert "same MRWP build" in P.project_mrwp_ui_results(rc)[2]


def test_rerun_clears_generated_failures_but_preserves_human_work(offline, monkeypatch):
    st = state_for(current_rc(ecs={PROD: [("test_100_X", "Failed")]}))
    plan_stub(monkeypatch, [point(100)])
    monkeypatch.setattr(D, "set_assigned_to", lambda *a: (True, ""))
    human_link = {"name": "Investigation", "url": "https://example.com/investigation"}
    st.set_step("bug_bash", "ui_failures",
                StepState(status="done", note="Human sign-off", links=[human_link], data={"ticket": 12}))
    with mockctx.active({}):
        U.build(st)
        U.build(st)
    step = st.get_step("bug_bash", "ui_failures")
    assert step.note.count("UI failures to investigate") == 1
    assert step.note.endswith("Human sign-off") and step.status == "done"
    st.pipeline_runs["rcs"].append(current_rc(rc=3))
    monkeypatch.setattr(D, "set_assigned_to", forbidden)
    with mockctx.active({}):
        U.build(st)
    step = st.get_step("bug_bash", "ui_failures")
    assert step.status == "done" and step.note == "Human sign-off"
    assert step.links == [human_link] and step.data == {"ticket": 12}


def test_unmarked_note_is_not_guessed_to_be_generated(offline, monkeypatch):
    st = state_for(current_rc())
    plan_stub(monkeypatch, [point(100)])
    st.set_step("bug_bash", "ui_failures", StepState(status="done",
        note="\U0001f9ea Bug Bash — UI failures to investigate\nold failure\n"
             "I'll mark this step complete for you.\nHuman investigation",
        links=[{"name": "MRWP ECS run", "url": "https://example.com/old-run"},
               {"name": "Human link", "url": "https://example.com/human"}],
        data={"broker_failed_tests": 1, "auth_failed_cases": [100], "keep": True}))
    before = copy.deepcopy(st.get_step("bug_bash", "ui_failures"))
    with mockctx.active({}):
        assert U.build(st).kind == "done"
    step = st.get_step("bug_bash", "ui_failures")
    assert step.note == before.note and step.status == "done"
    assert step.data == {"keep": True}
    assert step.links == before.links


def test_current_auth_snapshot_replaces_earlier_auth_failure(offline, monkeypatch):
    st = state_for(current_rc())
    st.set_step("bug_bash", U.ID, StepState(data={"auth": {"failed_case_ids": [999]}}))
    plan_stub(monkeypatch, [point(100)])
    with mockctx.active({}):
        assert U.build(st).kind == "done"
    assert st.get_step("bug_bash", U.ID).data["auth"]["failures"] == []
    assert not st.get_step("bug_bash", "ui_failures").note


def test_partial_fill_and_assignment_errors_are_explicit(offline, monkeypatch):
    st = state_for(current_rc(ecs={PROD: [("test_100_Pass", "Passed"), ("test_200_Fail", "Failed")]}))
    plan_stub(monkeypatch, [point(100), point(200, point_id=2)])
    monkeypatch.setattr(T, "_set_points_outcome",
                        lambda plan, suite, ids, outcome, *a: (outcome == "Passed", "write failed"))
    with mockctx.active({}):
        result = U.build(st)
    assert result.kind == "blocked" and "may already have changed" in result.reason
    data = st.get_step("bug_bash", U.ID).data
    assert data["fill_status"] == "incomplete" and data["summary"]["set_passed"] == 1
    assert data["summary"]["incomplete_outcome"] == "Failed" and data["provenance"]["rc"] == 2
    plan_stub(monkeypatch, [point(100), point(200, point_id=2)])
    monkeypatch.setattr(D, "set_assigned_to", lambda *a: (False, "assignment rejected"))
    with mockctx.active({}):
        result = U.build(st)
    assert "reassignment incomplete" in result.note
    assert st.get_step("bug_bash", U.ID).data["broker"]["assignment_errors"] == [
        {"case_id": 200, "detail": "assignment rejected"}]
    assert "all assigned" not in st.get_step("bug_bash", "ui_failures").note


def test_unmatched_verdicts_are_diagnosed_without_touching_manual_points(offline, monkeypatch):
    calls = plan_stub(monkeypatch, [point(300), point(100, 999, 2)])
    ok, summary, detail = T.fill_ui_automation_results(900, {100: {("ECS", "prod_msal_rc_broker"): "Passed"}})
    assert ok, detail
    assert not calls and len(summary["untouched_points"]) == 2
    assert summary["unmatched_verdicts"] == [
        {"case_id": 100, "flight": "ECS", "variant": "prod_msal_rc_broker", "verdict": "Passed",
         "status": "no_matching_plan_point"}]
