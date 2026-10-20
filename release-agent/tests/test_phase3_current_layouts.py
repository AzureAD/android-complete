"""Both current clone handlers feed P3S3; Current Tester stays point-local."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re
import time

import pytest
import yaml

from orchestrator import cli_common as C
from orchestrator.engine import Orchestrator
from orchestrator.revision import bind_initial
from orchestrator.state import ReleaseState
from steps.bug_bash import ui_results, ui_test_status as U
from tests._auth_evidence import auth_snapshot
from tests._context import context
from tests._mrwp_evidence import current_rc, PROD
from tests.test_broker_scenarios import ADO
from tools import auth_monthly as M, broker_plans as B, distribution as D, pipelines as P, testplans as T


OWNER = "owner@example.test"
OTHER = "other-release@example.test"
STRESS = "Stress Tests - RC MSAL with RC Broker (API 32)"


class CurrentLayoutsADO(ADO):
    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        self.suites[T.AUTH_PLAN, T.AUTH_ROOT_SUITE] = {
            "id": T.AUTH_ROOT_SUITE, "name": "Authenticator root",
            "suiteType": "staticTestSuite", "defaultConfigurations": [{"id": 84}],
        }
        self.shared = {
            cid: {"id": cid, "fields": {
                "System.WorkItemType": "Test Case", "System.TeamProject": T.PROJECT,
                "System.Title": f"shared {cid}",
                "System.AssignedTo": {"uniqueName": OTHER},
            }, "relations": []}
            for cid in (100, 200, 701, 702, 2916347, 2916524, 777)
        }
        self.cases.update(deepcopy(self.shared))
        self.points[7000, 7001] = [self.point(7002, 2916347, outcome="Passed", tester=OTHER)]
        self.assignment_failure = None
        self.remove_automated_after_read = False

    def point(self, pid, cid, *, outcome="Unspecified", tester="manual@example.test"):
        return {"id": pid, "testCase": {"id": cid}, "configuration": {"id": 84},
                "outcome": outcome,
                "assignedTo": {"id": "id-" + tester, "uniqueName": tester}}

    def get_all(self, url, timeout):
        modern = re.search(r"/Plans/(\d+)/Suites/(\d+)/TestPoint", url, re.I)
        if modern:
            return True, [{
                "id": p["id"], "testCaseReference": p["testCase"],
                "configuration": p["configuration"], "results": {"outcome": p["outcome"]},
                "tester": p.get("assignedTo"),
            } for p in self.points.get(tuple(map(int, modern.groups())), [])], ""
        result = super().get_all(url, timeout)
        points = re.search(r"/Plans/(\d+)/Suites/(\d+)/points\?", url)
        if points and self.remove_automated_after_read:
            key = tuple(map(int, points.groups()))
            if self.suites.get(key, {}).get("name") == "Automated Tests":
                self.points[key].pop()
                self.remove_automated_after_read = False
        return result

    def send(self, url, method, body, timeout):
        point_write = re.search(r"/Plans/(\d+)/Suites/(\d+)/points/([\d,]+)\?", url)
        if point_write and "tester" in body and self.assignment_failure == "denied":
            self.writes.append((url, method, deepcopy(body)))
            return False, None, "Current Tester update denied"
        result = super().send(url, method, body, timeout)
        if "/wit/workitems/$" in url:
            self.cases[result[1]["id"]]["fields"]["System.TeamProject"] = T.PROJECT
        suite_create = re.search(r"/Plans/(\d+)/suites\?", url)
        if suite_create and method == "POST" and int(suite_create[1]) == T.AUTH_PLAN:
            sid = result[1]["id"]
            if body["name"] in ("Manual Tests", "Automated Tests"):
                ids = (777,) if body["name"] == "Manual Tests" else (2916347, 2916524)
                self.points[T.AUTH_PLAN, sid] = [self.point(self.new_id(), cid) for cid in ids]
        if point_write and "tester" in body:
            assert method == "PATCH" and set(body) == {"tester"}
            if self.assignment_failure != "ignored":
                pid, sid = map(int, point_write.groups()[:2])
                ids = set(map(int, point_write[3].split(",")))
                for point in self.points[pid, sid]:
                    if point["id"] in ids:
                        identity = body["tester"]["id"]
                        point["assignedTo"] = {
                            "id": identity,
                            "uniqueName": OWNER if identity == "owner-guid" else identity.removeprefix("id-"),
                        }
        return result


@pytest.fixture
def current_layouts(monkeypatch, tmp_path, request):
    save = ReleaseState.save

    def save_with_sharing_retry(state, path):
        # Windows scanners can briefly hold the real-disk fixture's atomic destination.
        for attempt in range(5):
            try:
                return save(state, path)
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(0.05 * 2 ** attempt)

    monkeypatch.setattr(ReleaseState, "save", save_with_sharing_retry)
    ado = CurrentLayoutsADO(monkeypatch)
    options = getattr(request, "param", {})
    monkeypatch.setattr(T, "_suite_full", lambda *a: (
        True, {"inheritDefaultConfigurations": False, "defaultConfigurations": [{"id": 293}]}, ""))
    monkeypatch.setattr(T, "_native_auth_query", lambda *a: (
        True, "SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'Native'", ""))
    monkeypatch.setattr(D, "broker_manual_cases", lambda plan, root, timeout: (
        True, [{"id": cid} for cid in (
            options.get("broker_manual_ids", (701, 702))
            if root == T.BROKER_MANUAL_ROOT_SUITE else (100, 200))], ""))
    monkeypatch.setattr(B, "verify_ui_configurations", lambda *a: (True, {}, ""))
    monkeypatch.setattr(D, "resolve_tester_identity", lambda upn, **kw:
                        "owner-guid" if upn == OWNER else "id-" + upn)
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **kw: pytest.fail("No shared Assigned To writes"))
    broker_outcome = options.get("broker_outcome", "Failed")
    rc = current_rc(
        {PROD: [("test_100_UI", broker_outcome), ("test_200_Pass", "Passed")],
         STRESS: [("stress_pass", "Passed")]},
        {PROD: [("test_200_UI", broker_outcome)], STRESS: [("stress_failure", broker_outcome)]})
    rc["auth"] = auth_snapshot(rc=rc["rc"], rows={
        P.AUTH_UI_SUITES[0]: [("test_2916347_android", "Failed"),
                             ("test_2916524_android", options.get("auth_second", "Passed"))],
        P.MONTHLY_REPORT_ONLY: [("monthly failure", options.get("monthly_outcome", "Failed")),
                               ("monthly passed", "Passed")],
    })
    config = tmp_path / "config" / "phases.yaml"
    config.parent.mkdir()
    config.write_text(
        "version: 1\nphases:\n"
        "  - id: bug_bash\n    name: Current layouts\n    steps:\n"
        "      - {id: clone_plans_broker, name: Broker, kind: auto, owner: agent, "
        "effect_mode: transactional, effect_recovery: frozen}\n"
        "      - {id: clone_plans_auth, name: Authenticator, kind: auto, owner: agent, "
        "effect_mode: transactional, effect_recovery: frozen, effect_retry: true}\n"
        "      - {id: ui_test_status, name: Fill results and Current Tester, kind: auto, "
        "owner: agent, effect_mode: idempotent, effect_recovery: match_current}\n"
        "      - {id: distribute_tests, name: Distribute Current Testers, kind: external, "
        "owner: agent, source: scout, write_command: distribute-tests, repeatable: true}\n"
        "      - {id: ui_failures, name: Review failures, kind: human_action, owner: human}\n",
        encoding="utf-8")
    if options.get("full_phase"):
        canonical = Path(C.DEFAULT_CONFIG)
        document = yaml.safe_load(canonical.read_text(encoding="utf-8"))
        document["phases"] = [p for p in document["phases"] if p["id"] == "bug_bash"]
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        (config.parent / "automations.yaml").write_bytes(
            canonical.with_name("automations.yaml").read_bytes())
    (config.parent / "readiness.yaml").write_text("items: []\n", encoding="utf-8")
    mocks = {
        "bug_bash.clone_plans_broker": {"name": "TEST ONLY - current Broker"},
        "bug_bash.clone_plans_auth": {"name": "TEST ONLY - current Auth", "layout": M.LAYOUT},
    }
    runs = str(tmp_path / "runs")
    state = ReleaseState(release_id="2000-01", ccd="2000-01-01", owner_email=OWNER,
                         pipeline_runs={"rcs": [rc]})
    bind_initial(Orchestrator(str(config), state, mocks=mocks))
    state.save(C.state_path(runs, state.release_id))
    with C.state_lock(runs, state.release_id):
        state = C.load_state(runs, state.release_id)
        orch = Orchestrator(str(config), state, mocks=mocks)
        orch.run_until_gate(max_steps=2)
        C.save_state(state, runs, state.release_id)
    assert state.get_step("bug_bash", "clone_plans_broker").status == "done"
    assert state.get_step("bug_bash", "clone_plans_auth").status == "done"
    before = deepcopy(ado.points)
    writes = len(ado.writes)

    def fill():
        with C.state_lock(runs, state.release_id):
            loaded = C.load_state(runs, state.release_id)
            engine = Orchestrator(str(config), loaded, mocks=mocks)
            engine.run_until_gate()
            C.save_state(loaded, runs, loaded.release_id)
        return loaded

    return ado, state, before, writes, fill


def test_current_clones_feed_fill_and_current_tester_without_shared_case_or_history_edits(current_layouts):
    ado, cloned, before, start, fill = current_layouts
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "done", state.get_step("bug_bash", U.ID).note
    result = ui_results.completed_result(context(state))
    broker = state.resources[B.RESOURCE]
    auth = state.get_step("bug_bash", "clone_plans_auth").data
    assert broker["source"]["schema_version"] == 4
    assert broker["source"]["scenario_projection"]["classification_policy"] == 2
    assert set(broker["automation"]["flights"]["Local"]["categories"]) == {"UI Automation", "Stress Tests"}
    assert auth["layout"] == M.LAYOUT
    assert Counter(p["outcome"] for p in result["auth"]["applied_points"]) == {"Failed": 1, "Passed": 1}
    assert len(result["broker"]["applied_points"]) == 3
    assert len(result["monthly"]["applied_points"]) == 1
    expected = {
        (record["target"]["plan_id"], p.get("suite_id", record["target"].get("suite_id")), p["point_id"])
        for record in (result["broker"], result["auth"], result["monthly"])
        for p in record["applied_points"] if p["outcome"] == "Failed"
    }
    changed = {(pid, sid, p["id"]) for (pid, sid), points in ado.points.items()
               for p in points if p.get("assignedTo", {}).get("id") == "owner-guid"}
    assert changed == expected and len(changed) == 5
    for key in ((broker["plan_id"], broker["suites"]["broker"]),
                (T.AUTH_PLAN, auth["manual_suite_id"]), (7000, 7001)):
        assert ado.points[key] == before[key]
    assert all(ado.cases[cid] == value for cid, value in ado.shared.items())
    assert all(method == "PATCH" and "/points/" in url for url, method, _ in ado.writes[start:])
    assert all(set(body) <= {"outcome", "tester"} for _, _, body in ado.writes[start:])
    assert state.pipeline_runs == cloned.pipeline_runs
    writes = deepcopy(ado.writes)
    assert fill().get_step("bug_bash", U.ID).status == "done"
    assert ado.writes == writes


@pytest.mark.parametrize("failure", ["denied", "ignored"])
def test_current_layout_tester_errors_do_not_fake_assignment_success(current_layouts, failure):
    ado, _, _, _, fill = current_layouts
    ado.assignment_failure = failure
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "done"
    result = ui_results.completed_result(context(state))
    data = state.get_step("bug_bash", U.ID).data
    assert result["status"] == "complete"
    assert data["broker"]["failed_assigned_to_owner"] == data["auth"]["failed_assigned_to_owner"] == 0
    assert data["monthly_failed_assigned_to_owner"] == 0
    assert len(data["broker"]["assignment_errors"]) == 3
    assert len(data["auth"]["assignment_errors"]) == len(data["monthly_assignment_errors"]) == 1
    assert all(ado.cases[cid] == value for cid, value in ado.shared.items())


@pytest.mark.parametrize("drift", ["broker_point", "automated_point", "monthly_configuration"])
def test_current_layout_target_drift_blocks_before_any_result_or_tester_write(current_layouts, drift):
    ado, cloned, _, start, fill = current_layouts
    broker = cloned.resources[B.RESOURCE]
    auth = cloned.get_step("bug_bash", "clone_plans_auth").data
    if drift == "broker_point":
        row = broker["scenario_bindings"][0]
        ado.points[broker["plan_id"], row["suite_id"]].clear()
    elif drift == "automated_point":
        ado.points[T.AUTH_PLAN, auth["automated_suite_id"]].pop()
    else:
        ado.points[T.AUTH_PLAN, auth["monthly"]["suite_id"]][0]["configuration"]["id"] = 85
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "blocked"
    assert len(ado.writes) == start
    with pytest.raises(ValueError):
        ui_results.completed_result(context(state))


def test_current_layout_coverage_is_rechecked_after_preparation_before_broker_writes(current_layouts):
    ado, _, _, start, fill = current_layouts
    ado.remove_automated_after_read = True
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "blocked"
    assert len(ado.writes) == start


@pytest.mark.parametrize("current_layouts", [{"auth_second": "NotApplicable"}], indirect=True)
def test_current_layout_na_only_automated_case_keeps_pending_result_and_current_tester(current_layouts):
    ado, _, before, _, fill = current_layouts
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "done"
    result = ui_results.completed_result(context(state))
    assert result["auth"]["automated_case_ids"] == [2916347, 2916524]
    assert len(result["auth"]["applied_points"]) == 1
    sid = result["auth"]["target"]["suite_id"]
    point = next(p for p in ado.points[T.AUTH_PLAN, sid] if p["testCase"]["id"] == 2916524)
    assert point == next(p for p in before[T.AUTH_PLAN, sid] if p["id"] == point["id"])


@pytest.mark.parametrize("current_layouts", [
    {"broker_outcome": "Passed", "monthly_outcome": "Passed"},
], indirect=True)
def test_current_layout_zero_failures_keep_broker_manual_only_and_omit_monthly_folder(current_layouts):
    ado, _, before, _, fill = current_layouts
    state = fill()
    assert state.get_step("bug_bash", U.ID).status == "done"
    result = ui_results.completed_result(context(state))
    broker = state.resources[B.RESOURCE]
    assert broker["automation"]["root_suite_id"] is None
    assert broker["automation"]["flights"] == {}
    assert result["broker"]["applied_points"] == []
    assert result["monthly"]["applied_points"] == []
    assert result["auth"]["target"]["monthly"]["suite_id"] is None
    assert not any(suite["name"] == M.NAME for suite in ado.suites.values())
    key = (broker["plan_id"], broker["suites"]["broker"])
    assert ado.points[key] == before[key]


def test_p3s3_help_names_current_tester_and_current_layout_coverage():
    from orchestrator.knowledge import get_knowledge

    help_text = get_knowledge("bug_bash", U.ID)
    assert "Current Tester" in help_text["what"]
    assert "System.AssignedTo" in help_text["what"]
    assert "entire frozen mapped-case set" in help_text["how"]
    assert "Stress Tests separate from UI Automation" in help_text["what"]
