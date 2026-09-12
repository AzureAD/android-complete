"""Owner boundaries and durable publication, using synthetic evidence and local state."""
from copy import deepcopy

import pytest

from orchestrator import cli_common as C
from orchestrator.state import ReleaseState, StepState
from steps.build_verify import rc_report as R, _rc_report_rendering as rendering
from steps.bug_bash import ui_test_status as U, ui_results, distribute_tests as D, bugbash_updates as BU
from steps.lib import mockctx
from tests._auth_evidence import auth_snapshot, capture
from tests._mrwp_evidence import current_rc, PROD
from tests._ui_results import publish, checkpoint_memory, broker_fill, auth_fill
from tools import pipelines as P, testplans as T, distribution, bugbash


def forbidden(*args, **kwargs):
    raise AssertionError("Upstream acquisition/projection/reconciliation must not run here")


def state():
    st = ReleaseState(release_id="2000-01", owner_email="owner@example.test")
    rc = current_rc(rc=1)
    rc["auth"] = auth_snapshot(rows={
        P.AUTH_UI_SUITES[0]: [("test_100_pass", "Passed"), ("test_200_fail", "Failed")],
        P.MONTHLY_REPORT_ONLY: [("monthly_failure", "Failed")]})
    st.pipeline_runs = {"rcs": [rc]}
    st.set_step("bug_bash", "clone_plans_broker", StepState(data={"plan_id": 900, "ui_suite_id": 901}))
    st.set_step("bug_bash", "clone_plans_auth", StepState(data={"suite_id": 902}))
    checkpoint_memory(st)
    return st


def block_upstream(monkeypatch):
    from tools.pipelines import auth_evidence, ui_projection, test_evidence, tests_results
    for module in (P, auth_evidence, ui_projection, test_evidence, tests_results):
        for name in ("project_auth_ui_results", "project_mrwp_ui_results", "reconcile_retries",
                     "collect_auth_ui_evidence", "inspect_auth_ui_evidence",
                     "validate_snapshot_tests", "get_test_summary", "_test_runs", "_run_results",
                     "_ado_rest_get", "_ado_rest_get_all", "_ado_rest_send"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)


def distribution_inputs():
    from tests.test_distribution import observe
    return observe({
        "roster": [{"upn": "tester@example.test", "name": "Tester"}], "oce": "oce@example.test",
        "broker_cases": [{"id": 10, "assignee": "tester@example.test"}],
        "auth_cases": [{"id": 100, "assignee": "tester@example.test"}, {"id": 200, "assignee": "owner@example.test"},
                       {"id": 300, "assignee": "tester@example.test"}],
    }, automated=[100, 200], failed=[200])


def test_capture_and_report_prepare_no_target_mappings(monkeypatch):
    st = state()
    from tools.pipelines import ui_projection
    for module in (P, ui_projection):
        monkeypatch.setattr(module, "project_auth_ui_results", forbidden)
        monkeypatch.setattr(module, "project_mrwp_ui_results", forbidden)
    captured = capture(rows={P.MONTHLY_REPORT_ONLY: [("monthly_failure", "Failed")]})
    assert set(captured) == {"evidence", "suites"}
    model = R.rc_report_model(st)
    assert not model["ui_evidence"]["issues"]
    monthly = next(f for f in model["ui_evidence"]["failures"] if f.get("report_only"))
    assert monthly["title"] == "monthly_failure" and monthly["links"]
    assert "config_id" not in str(model["ui_evidence"])


def test_renderers_only_render_prepared_facts(monkeypatch):
    st = state()
    model = R.rc_report_model(st)
    gate, auth = R.rc_ui_gate(model), R.auth_report_gate(model)
    block_upstream(monkeypatch)
    monkeypatch.setattr(R, "validate_snapshot_tests", forbidden)
    # Presentation has no need for raw Auth capture, nor to derive facts from it.
    del model["auth"]["test"]["evidence"]
    for text in (rendering.rc_email_plain(model, {}, gate, auth, ""),
                 rendering.rc_email_html(model, {}, gate, auth, "")):
        assert "monthly_failure" in text and "no test-plan case map" in text
        assert "runId=2" in text and "resultId=1" in text


def test_distribution_progress_use_result_without_raw_evidence(monkeypatch):
    st = state()
    publish(st)
    block_upstream(monkeypatch)
    rc = st.pipeline_runs["rcs"][-1]
    del rc["auth"]["test"]["evidence"]
    rc["auth"]["test"]["suites"] = "not a capture"
    for slot in ("ecs", "local"):
        rc[slot].pop("tests")
        rc[slot].pop("failed_suites")
    with mockctx.active(distribution_inputs()):
        outcome, report = D.inspect_distribution(st, oof=[])
        assert outcome.kind == "done"
    assert set(report["_targets"]) == {"B:10", "A:200", "A:300"}
    assert set(report["owner_triage"]) == {"A:200"} and report["auth_excluded_automated"] == 2
    assert "plan" not in st.get_step("bug_bash", D.ID).data
    seen = []
    monkeypatch.setattr(bugbash, "gather_progress",
                        lambda *a, **kw: (seen.append(kw) is None, {}, ""))
    assert BU.gather(st)[0] and seen == [{
        "auto_failed_ids": [200], "auth_automated_ids": [100, 200],
        "broker_ui_result": ui_results.completed_result(st)["broker"]}]


@pytest.mark.parametrize("damage", ["missing", "incomplete", "failed_ids", "target", "point", "duplicate"])
def test_missing_partial_malformed_receipt_never_becomes_empty_work(monkeypatch, damage):
    st = state()
    publish(st)
    data = st.get_step("bug_bash", U.ID).data
    result = data["result"]
    if damage == "missing":
        del data["result"]
    elif damage == "incomplete":
        result["status"] = "incomplete"
    elif damage == "failed_ids":
        result["auth"]["failed_case_ids"].append(999)
    elif damage == "target":
        result["auth"]["target"]["suite_id"] = 999
    elif damage == "point":
        result["auth"]["applied_points"][0]["point_id"] = None
    else:
        result["auth"]["applied_points"].append(deepcopy(result["auth"]["applied_points"][0]))
    block_upstream(monkeypatch)
    monkeypatch.setattr(bugbash, "gather_progress", forbidden)
    with pytest.raises(ValueError):
        ui_results.completed_result(st)
    with mockctx.active(distribution_inputs()):
        assert D.build(st, oof=[]).kind == "blocked"
    assert not st.get_step("bug_bash", D.ID).data.get("plan")
    assert not BU.gather(st)[0]


@pytest.mark.parametrize("change", [
    "release", "rc", "ecs", "local", "apk", "test", "broker_plan", "broker_suite", "auth_plan",
    "auth_suite", "ecs_inflight", "auth_inflight",
])
def test_all_authoritative_binding_changes_reject_result_and_preview(monkeypatch, change):
    st = state()
    publish(st)
    with mockctx.active(distribution_inputs()):
        assert D.build(st, oof=[]).kind == "done"
    rc = st.pipeline_runs["rcs"][-1]
    if change == "release":
        st.release_id = "2000-02"
    elif change == "rc":
        rc["rc"] = 2
    elif change in ("ecs", "local"):
        rc[change]["run_id"] = 999
    elif change in ("apk", "test"):
        rc["auth"]["build" if change == "apk" else "test"]["run_id"] = 999
    elif change in ("broker_plan", "broker_suite"):
        st.get_step("bug_bash", "clone_plans_broker").data[
            "plan_id" if change == "broker_plan" else "ui_suite_id"] = 999
    elif change == "auth_plan":
        monkeypatch.setattr(T, "AUTH_PLAN", 999)
    elif change == "auth_suite":
        st.get_step("bug_bash", "clone_plans_auth").data["suite_id"] = 999
    elif change == "ecs_inflight":
        rc["ecs"]["complete"] = False
    else:
        rc["auth"]["test"]["complete"] = False
    with pytest.raises(ValueError):
        ui_results.completed_result(st)
    with mockctx.active(distribution_inputs()):
        assert D.build(st).kind == "blocked"
    assert not BU.gather(st)[0]


def test_same_source_new_fill_requires_distribution_review():
    st = state()
    publish(st)
    with mockctx.active(distribution_inputs()):
        _, old = D.inspect_distribution(st, oof=[])
    prior_result = ui_results.completed_result(st)
    publish(st)
    current = ui_results.completed_result(st)
    assert prior_result["id"] != current["id"] and prior_result["binding"] == current["binding"]
    with mockctx.active(distribution_inputs()):
        _, fresh = D.inspect_distribution(st)
    assert old["review_hash"] != fresh["review_hash"] and "plan" not in st.get_step("bug_bash", D.ID).data


def test_crash_before_write_durably_invalidates_previous_receipt(monkeypatch, tmp_path):
    st = state()
    publish(st)
    old_id = ui_results.completed_result(st)["id"]
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))

    def crash(*args, **kwargs):
        persisted = ReleaseState.load(str(path))
        assert persisted.get_step("bug_bash", U.ID).data["result"]["id"] != old_id
        with pytest.raises(ValueError, match="missing/partial/invalidated"):
            ui_results.completed_result(persisted)
        raise SystemExit("simulated process termination")

    monkeypatch.setattr(T, "fill_ui_automation_results", crash)
    monkeypatch.setattr(T, "fill_auth_ui_results", forbidden)
    with C.state_lock(str(tmp_path), st.release_id):
        current = C.load_state(str(tmp_path), st.release_id)
        with pytest.raises(SystemExit):
            U.build(current)
    persisted = ReleaseState.load(str(path))
    assert persisted.get_step("bug_bash", U.ID).data["result"]["stage"] == "broker_write"
    assert not BU.gather(persisted)[0]


def test_failed_initial_checkpoint_prevents_all_provider_work(monkeypatch):
    st = state()
    publish(st)
    block_upstream(monkeypatch)
    monkeypatch.setattr(T, "fill_ui_automation_results", forbidden)
    monkeypatch.setattr(T, "fill_auth_ui_results", forbidden)
    st._checkpoint = lambda: (_ for _ in ()).throw(OSError("disk unavailable"))
    with pytest.raises(OSError, match="disk unavailable"):
        U.build(st)
    with pytest.raises(ValueError):
        ui_results.completed_result(st)


def test_unexpected_writer_error_propagates_after_durable_invalidation(monkeypatch, tmp_path):
    st = state()
    publish(st)
    path = tmp_path / "state.json"
    st._checkpoint = lambda: st.save(str(path))
    monkeypatch.setattr(T, "fill_ui_automation_results", forbidden)
    with pytest.raises(AssertionError, match="must not run here"):
        U.build(st)
    with pytest.raises(ValueError, match="missing/partial/invalidated"):
        ui_results.completed_result(ReleaseState.load(str(path)))


def test_failed_completion_checkpoint_invalidates_in_memory_and_disk(monkeypatch, tmp_path):
    st = state()
    path = tmp_path / "state.json"

    def checkpoint():
        if st.get_step("bug_bash", U.ID).data["result"]["status"] == "complete":
            raise OSError("completion checkpoint failed")
        st.save(str(path))

    st._checkpoint = checkpoint
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(distribution, "set_assigned_to", lambda *a: (True, ""))
    assert U.build(st).kind == "blocked"
    for copy in (st, ReleaseState.load(str(path))):
        with pytest.raises(ValueError):
            ui_results.completed_result(copy)
        assert "completion checkpoint failed" in copy.get_step("bug_bash", U.ID).data["result"]["error"]


def test_partial_auth_write_preserves_diagnostics_without_publishing(monkeypatch):
    st = state()
    publish(st)
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)

    def partial(plan, suite, outcomes):
        _, summary, _ = auth_fill(plan, suite, outcomes)
        summary["uncertain_points"] = [p for p in summary["applied_points"] if p["outcome"] == "Failed"]
        summary["applied_points"] = [p for p in summary["applied_points"] if p["outcome"] == "Passed"]
        summary.update(set_failed=0, failed_case_ids=[], incomplete_outcome="Failed")
        return False, summary, "synthetic partial batch"

    monkeypatch.setattr(T, "fill_auth_ui_results", partial)
    monkeypatch.setattr(distribution, "set_assigned_to", forbidden)
    assert U.build(st).kind == "blocked"
    data = st.get_step("bug_bash", U.ID).data
    assert data["summary"]["applied_points"] and data["auth"]["mapping"]["uncertain_points"]
    assert data["result"]["investigations"]["report_only_or_unmapped_auth"]
    assert not BU.gather(st)[0]


def test_only_applied_failures_enter_progress_or_assignments(monkeypatch):
    st = state()
    st.pipeline_runs["rcs"][-1]["ecs"] = current_rc(
        ecs={PROD: [("test_500_unmatched", "Failed")]})["ecs"]
    monkeypatch.setattr(T, "fill_ui_automation_results",
                        lambda plan, verdicts, **kw: broker_fill(plan, {}, **kw))
    monkeypatch.setattr(T, "fill_auth_ui_results",
                        lambda plan, suite, outcomes: auth_fill(plan, suite, {100: "Passed"}))
    monkeypatch.setattr(distribution, "set_assigned_to", forbidden)
    assert U.build(st).kind == "done"
    result = ui_results.completed_result(st)
    assert result["auth"]["automated_case_ids"] == [100, 200]
    assert result["auth"]["failed_case_ids"] == result["broker"]["failed_case_ids"] == []
    assert {f["title"] for f in result["investigations"]["auth"]} == {"test_200_fail", "monthly_failure"}
    assert "test_500_unmatched" in st.get_step("bug_bash", "ui_failures").note
    assert BU._auto_failed_ids(st) == []


def test_assignment_failure_is_nonblocking_and_not_claimed_as_success(monkeypatch):
    st = state()
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)
    monkeypatch.setattr(T, "fill_auth_ui_results", auth_fill)
    monkeypatch.setattr(distribution, "set_assigned_to", lambda *a: (False, "assignment rejected"))
    outcome = U.build(st)
    assert outcome.kind == "done" and "reassignment incomplete" in outcome.note
    assert "assigned to owner" not in outcome.note
    assert ui_results.completed_result(st)["auth"]["failed_case_ids"] == [200]
    data = st.get_step("bug_bash", U.ID).data["auth"]
    assert data["failed_assigned_to_owner"] == 0 and data["assignment_errors"]


@pytest.mark.parametrize("missing", ["target", "applied_points"])
def test_success_shaped_writer_summary_cannot_publish(monkeypatch, missing):
    st = state()
    monkeypatch.setattr(T, "fill_ui_automation_results", broker_fill)

    def incomplete(plan, suite, outcomes):
        ok, summary, detail = auth_fill(plan, suite, outcomes)
        del summary[missing]
        return ok, summary, detail

    monkeypatch.setattr(T, "fill_auth_ui_results", incomplete)
    monkeypatch.setattr(distribution, "set_assigned_to", lambda *a: (True, ""))
    assert U.build(st).kind == "blocked"
    with pytest.raises(ValueError, match="missing/partial/invalidated"):
        ui_results.completed_result(st)


def test_auth_writer_reports_completed_and_uncertain_batches(monkeypatch):
    points = [{"id": 1, "testCase": {"id": 100}}, {"id": 2, "testCase": {"id": 200}}]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, points, ""))
    monkeypatch.setattr(T, "_set_points_outcome",
                        lambda p, s, ids, outcome, t: (outcome == "Passed", "partial batch"))
    ok, summary, detail = T.fill_auth_ui_results(900, 902, {100: "Passed", 200: "Failed"})
    assert not ok and "partial batch" in detail
    assert summary["target"] == {"plan_id": 900, "suite_id": 902}
    assert summary["applied_points"] == [{"point_id": 1, "case_id": 100, "outcome": "Passed"}]
    assert summary["uncertain_points"] == [{"point_id": 2, "case_id": 200, "outcome": "Failed"}]
    assert summary["failed_case_ids"] == [] and summary["set_failed"] == 0
