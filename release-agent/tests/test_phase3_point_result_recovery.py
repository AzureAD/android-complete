"""Replaying automation never overwrites a tester's manually resolved point result."""
from copy import deepcopy
import json

import pytest

from tools import pipelines as P, testplans as T


@pytest.fixture(params=["broker", "auth"])
def target(request, monkeypatch):
    points = [{"id": 1, "testCase": {"id": 100}, "configuration": {"id": 292},
               "outcome": "Unspecified"}]
    writes = []
    monkeypatch.setattr(T, "_find_suite_by_name", lambda *a, **kw: (True, 901, ""))
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **kw: (True, deepcopy(points), ""))

    def patch(plan, suite, ids, outcome, timeout, **kwargs):
        writes.append((plan, suite, list(ids), outcome))
        for point in points:
            if point["id"] in ids:
                point["outcome"] = outcome
        return True, ""

    monkeypatch.setattr(T, "_set_points_outcome", patch)

    def fill(outcome="Failed", prior=None):
        if request.param == "broker":
            return T.fill_ui_automation_results(
                900, {100: {("ECS", "prod_msal_rc_broker"): outcome}},
                suite_id=901, prior_points=prior)
        return T.fill_auth_ui_results(900, 901, {100: outcome}, prior_points=prior)

    return points, writes, fill


def test_first_fill_writes_only_pending_points_and_keeps_a_receipt(target):
    points, writes, fill = target
    ok, result, detail = fill()
    assert ok, detail
    assert points[0]["outcome"] == "Failed" and len(writes) == 1
    assert result["applied_points"][0]["point_id"] == 1


@pytest.mark.parametrize("outcome", ["Passed", "Failed", "Blocked", None])
def test_unowned_existing_or_missing_result_is_preserved_not_replayed(target, outcome):
    points, writes, fill = target
    points[0]["outcome"] = outcome
    ok, result, detail = fill()
    assert not ok and "preserve manual results" in detail
    assert points[0]["outcome"] == outcome and not writes


def test_manual_resolution_after_an_acknowledged_write_blocks_recovery(target):
    points, writes, fill = target
    ok, result, _ = fill()
    assert ok
    points[0]["outcome"] = "Passed"
    writes.clear()
    ok, _, detail = fill(prior=result["applied_points"])
    assert not ok and "changed since" in detail
    assert points[0]["outcome"] == "Passed" and not writes


def test_matching_acknowledged_write_is_reused_without_repatching(target):
    _, writes, fill = target
    ok, result, _ = fill()
    assert ok
    writes.clear()
    ok, reused, detail = fill(prior=result["applied_points"])
    assert ok, detail
    assert not writes and reused["applied_points"] == result["applied_points"]
    assert reused["reused_points"] == [1] and reused["set_failed"] == 1


def test_new_source_can_replace_unchanged_automation_owned_result(target):
    points, writes, fill = target
    ok, result, _ = fill()
    assert ok
    writes.clear()
    ok, updated, detail = fill("Passed", result["applied_points"])
    assert ok, detail
    assert points[0]["outcome"] == "Passed" and len(writes) == 1
    assert updated["set_passed"] == 1 and updated["set_failed"] == 0


def test_corrupt_prior_receipt_never_authorizes_a_point_write(target):
    _, writes, fill = target
    ok, _, detail = fill(prior=[{"point_id": True, "case_id": 100, "outcome": "Failed"}])
    assert not ok and "receipt" in detail and not writes


@pytest.mark.parametrize("change", ["outcome", "case", "config", "missing"])
def test_native_batch_recheck_preserves_changes_after_initial_read(monkeypatch, change):
    original = [{"id": 1, "testCase": {"id": 100}, "configuration": {"id": 292},
                 "outcome": "Unspecified"}]
    current = deepcopy(original)
    if change == "outcome":
        current[0]["outcome"] = "Passed"
    elif change == "case":
        current[0]["testCase"]["id"] = 999
    elif change == "config":
        current[0]["configuration"]["id"] = 344
    else:
        current.clear()
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **kw: (True, current, ""))
    def forbidden(*args, **kwargs):
        pytest.fail("Changed targets must not receive an outcome PATCH")
    monkeypatch.setattr(P, "_ado_rest_send", forbidden)
    ok, detail = T._set_points_outcome(900, 901, [1], "Failed", expected_points=original)
    assert not ok and "changed before PATCH" in detail


def test_ui_effect_input_does_not_duplicate_full_raw_phase2_evidence(monkeypatch):
    from orchestrator.state import ReleaseState, StepState
    from steps.bug_bash import ui_test_status as U
    from tests._context import context
    from tests._mrwp_evidence import current_rc, PROD
    from tools import distribution

    rc = current_rc(ecs={
        PROD: [("test_100_ui", "Passed")],
        "sdk_UnitTests": [(f"raw_unit_case_{i}", "Passed") for i in range(1000)],
    })
    state = ReleaseState(release_id="2026-09", ccd="2026-09-09",
                         owner_email="owner@example.test", pipeline_runs={"rcs": [rc]})
    state.set_step("bug_bash", "clone_plans_broker", StepState(
        status="done", data={"plan_id": 900, "ui_suite_id": 901}))
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": 902}))
    monkeypatch.setattr(distribution, "validate_release_targets", lambda *a, **kw: None)
    prepared = U.prepare_effect(context(state))
    encoded = json.dumps(prepared)
    assert "raw_unit_case_999" not in encoded and "test_results" not in encoded
    assert len(encoded) < 15_000 and len(encoded) < len(json.dumps(rc)) // 4
    assert prepared["rc"]["ecs"]["run_id"] == rc["ecs"]["run_id"]
    assert prepared["broker_projection"] and prepared["auth_projection"]
