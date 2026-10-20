"""Current clones and completed P3S3 feed point-scoped Current Tester distribution."""
from copy import deepcopy
import json

import pytest

from orchestrator import cli_common as C
from orchestrator.cli import build_parser
from orchestrator.commands import distribute as command
from orchestrator.state import ReleaseState
from steps.bug_bash import distribute_tests as S
from tests._context import context
from tests.test_phase3_current_layouts import current_layouts, OWNER, OTHER
from tools import distribution as D, testplans as T
from tools import pipelines as P


TESTER = "tester@example.test"
OCE = "oce@example.test"


def distribution_inputs(monkeypatch, state):
    broker = state.resources["broker_test_plan"]
    monkeypatch.setattr(D, "_tags_of", lambda ids, *a: (
        True, {str(cid): {"tags": []} for cid in ids}, ""))
    return {
        "roster": [{"upn": upn, "name": upn.split("@")[0]} for upn in (TESTER, OWNER, OCE)],
        "oce": OCE,
        "broker_cases": [{"id": str(cid)} for cid in broker["source"]["broker_cases"]],
    }


def inspect(monkeypatch, state):
    inputs = distribution_inputs(monkeypatch, state)
    return S.inspect_distribution(context(state, inputs=inputs), oof=[])


def cli(monkeypatch, state, tmp_path, capsys, *flags):
    inputs = distribution_inputs(monkeypatch, state)
    monkeypatch.setattr(command.mocks_mod, "load_mocks",
                        lambda: {"bug_bash.distribute_tests": inputs})
    args = build_parser().parse_args([
        "--config", str(tmp_path / "config" / "phases.yaml"),
        "--runs-root", str(tmp_path / "runs"),
        "distribute-tests", "--release", state.release_id,
        "--no-oof", "--oce", OCE, "--approved-by", "test-reviewer",
        "--executor", "current-layout-test", *flags,
    ])
    with C.state_lock(args.runs_root, state.release_id):
        result = command.cmd_distribute_tests(args)
    return result, json.loads(capsys.readouterr().out)


def saved_state(tmp_path, state):
    return ReleaseState.load(C.state_path(str(tmp_path / "runs"), state.release_id))


def test_resolved_monthly_investigation_never_becomes_manual_work(current_layouts, monkeypatch):
    ado, _, _, _, fill = current_layouts
    state = fill()
    auth = state.get_step("bug_bash", "clone_plans_auth").data
    monthly = ado.points[T.AUTH_PLAN, auth["monthly"]["suite_id"]][0]
    monthly.update(outcome="Passed", assignedTo={"id": "id-" + OTHER, "uniqueName": OTHER})
    before = deepcopy(monthly)
    _, report = inspect(monkeypatch, state)
    assert "error" not in report, report
    key = "A:" + str(monthly["testCase"]["id"])
    assert report["auth_total"] == 1
    assert key not in report["_targets"]
    assert key not in report["owner_triage"]
    assert not any(p["point_id"] == monthly["id"] for p in report["point_changes"])
    assert monthly == before


@pytest.mark.parametrize("current_layouts", [{"broker_manual_ids": (100, 701)}], indirect=True)
def test_manual_current_tester_does_not_inherit_same_case_failure_ownership(current_layouts, monkeypatch):
    ado, _, _, _, fill = current_layouts
    state = fill()
    broker = state.resources["broker_test_plan"]
    assert any(row["case_id"] == 100 for row in broker["scenario_bindings"])
    for point in ado.points[broker["plan_id"], broker["suites"]["broker"]]:
        point["assignedTo"] = {"id": "id-" + TESTER, "uniqueName": TESTER}
    _, report = inspect(monkeypatch, state)
    assert "error" not in report, report
    assert report["broker_total"] == 2
    assert report["_targets"]["B:100"] == TESTER
    assert report["_targets"]["B:100:triage"] == OWNER
    manual_ids = {p["id"] for p in ado.points[broker["plan_id"], broker["suites"]["broker"]]}
    assert not any(change["point_id"] in manual_ids for change in report["point_changes"])


def test_checked_distribution_updates_only_current_testers_in_current_manual_suites(
        current_layouts, monkeypatch, tmp_path, capsys):
    ado, _, _, _, fill = current_layouts
    state = fill()
    before = deepcopy(ado.points)
    cases = deepcopy(ado.cases)
    raw_state = (tmp_path / "runs" / state.release_id / "release-state.json").read_bytes()
    start = len(ado.writes)
    code, preview = cli(monkeypatch, state, tmp_path, capsys)
    assert code == 0 and not preview["permission_to_execute"]
    assert not ado.writes[start:]
    assert (tmp_path / "runs" / state.release_id / "release-state.json").read_bytes() == raw_state
    operations = preview["plan"]["operations"]
    broker = state.resources["broker_test_plan"]
    auth = state.get_step("bug_bash", "clone_plans_auth").data
    assert {(o["target"]["plan_id"], o["target"]["suite_id"]) for o in operations} == {
        (broker["plan_id"], broker["suites"]["broker"]), (T.AUTH_PLAN, auth["manual_suite_id"])}
    code, completed = cli(
        monkeypatch, state, tmp_path, capsys, "--apply", "--review-hash", preview["review_hash"])
    assert code == 0 and completed["valid"]
    loaded = saved_state(tmp_path, state)
    assert loaded.get_step("bug_bash", S.ID).status == "done"
    assert "plan" not in loaded.get_step("bug_bash", S.ID).data
    assert ado.cases == cases
    for key, points in ado.points.items():
        if key in {(broker["plan_id"], broker["suites"]["broker"]), (T.AUTH_PLAN, auth["manual_suite_id"])}:
            assert all(p["assignedTo"]["uniqueName"] == TESTER for p in points)
            assert [p["outcome"] for p in points] == [p["outcome"] for p in before[key]]
        else:
            assert points == before[key]
    assert all(method == "PATCH" and "/points/" in url and set(body) == {"tester"}
               for url, method, body in ado.writes[start:])


@pytest.mark.parametrize("current_layouts", [{"broker_manual_ids": (100, 701)}], indirect=True)
def test_checked_triage_correction_preserves_same_case_manual_and_untracked_configuration(
        current_layouts, monkeypatch, tmp_path, capsys):
    ado, _, _, _, fill = current_layouts
    state = fill()
    broker = state.resources["broker_test_plan"]
    row = next(r for r in broker["scenario_bindings"] if r["case_id"] == 100)
    for point in ado.points[broker["plan_id"], broker["suites"]["broker"]]:
        point["assignedTo"] = {"id": "id-" + TESTER, "uniqueName": TESTER}
    failure_points = ado.points[broker["plan_id"], row["suite_id"]]
    failure = next(p for p in failure_points
                   if p["testCase"]["id"] == row["case_id"]
                   and p["configuration"]["id"] == row["config_id"])
    failure["assignedTo"] = {"id": "id-" + OTHER, "uniqueName": OTHER}
    other_config = ado.point(ado.new_id(), 100, outcome="Passed", tester=OTHER)
    other_config["configuration"]["id"] = 85
    failure_points.append(other_config)
    protected = deepcopy(other_config)
    manual = deepcopy(ado.points[broker["plan_id"], broker["suites"]["broker"]])
    code, preview = cli(monkeypatch, state, tmp_path, capsys)
    assert code == 0
    triage_op = next(o for o in preview["plan"]["operations"] if o["target"]["suite_id"] == row["suite_id"])
    assert triage_op["content"]["assignments"]["100"] == OWNER
    assert triage_op["content"]["updates"] == [{"tester_id": "owner-guid", "point_ids": [failure["id"]]}]
    assert triage_op["preconditions"]["points"] == {
        str(failure["id"]): {"case_id": "100", "config_id": str(row["config_id"])}}
    code, completed = cli(
        monkeypatch, state, tmp_path, capsys, "--apply", "--review-hash", preview["review_hash"])
    assert code == 0 and completed["valid"]
    assert ado.points[broker["plan_id"], broker["suites"]["broker"]] == manual
    assert failure["assignedTo"]["uniqueName"] == OWNER
    assert other_config == protected


@pytest.mark.parametrize("change", ["tester", "monthly_resolution", "configuration"])
def test_current_layout_drift_invalidates_distribution_approval_before_writes(
        current_layouts, monkeypatch, tmp_path, capsys, change):
    ado, _, _, _, fill = current_layouts
    state = fill()
    code, preview = cli(monkeypatch, state, tmp_path, capsys)
    assert code == 0
    auth = state.get_step("bug_bash", "clone_plans_auth").data
    if change == "monthly_resolution":
        point = ado.points[T.AUTH_PLAN, auth["monthly"]["suite_id"]][0]
        point["outcome"] = "Passed"
    else:
        point = ado.points[T.AUTH_PLAN, auth["manual_suite_id"]][0]
        if change == "tester":
            point["assignedTo"] = {"id": "id-" + OTHER, "uniqueName": OTHER}
        else:
            point["configuration"]["id"] = 85
    start = len(ado.writes)
    code, report = cli(
        monkeypatch, state, tmp_path, capsys, "--apply", "--review-hash", preview["review_hash"])
    assert code == 1 and not report["permission_to_execute"]
    assert not ado.writes[start:]


def test_monthly_resolution_during_apply_stops_its_tester_update(
        current_layouts, monkeypatch, tmp_path, capsys):
    ado, _, _, _, fill = current_layouts
    state = fill()
    auth = state.get_step("bug_bash", "clone_plans_auth").data
    monthly = ado.points[T.AUTH_PLAN, auth["monthly"]["suite_id"]][0]
    monthly["assignedTo"] = {"id": "id-" + OTHER, "uniqueName": OTHER}
    code, preview = cli(monkeypatch, state, tmp_path, capsys)
    assert code == 0
    send = P._ado_rest_send

    def resolve_after_manual_assignment(url, method, body, timeout):
        result = send(url, method, body, timeout)
        if f"/Suites/{auth['manual_suite_id']}/points/" in url and "tester" in body:
            monthly["outcome"] = "Passed"
        return result

    monkeypatch.setattr(P, "_ado_rest_send", resolve_after_manual_assignment)
    code, report = cli(
        monkeypatch, state, tmp_path, capsys, "--apply", "--review-hash", preview["review_hash"])
    assert code == 2 and "reviewed outcome changed" in report["error"]
    assert monthly["outcome"] == "Passed"
    assert monthly["assignedTo"]["uniqueName"] == OTHER
    record = saved_state(tmp_path, state).get_step("bug_bash", S.ID)
    assert record.status == "blocked" and record.execution


def test_distribution_help_names_current_tester_and_independent_workloads():
    from orchestrator.knowledge import get_knowledge

    help_text = get_knowledge("bug_bash", S.ID)
    assert "Current Tester" in help_text["what"]
    assert "System.AssignedTo" in help_text["what"]
    assert "including after their points are resolved" in help_text["what"]
    assert "B:<id>:triage" in help_text["how"]
