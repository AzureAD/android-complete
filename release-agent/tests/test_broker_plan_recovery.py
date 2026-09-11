"""Creation/recovery regressions: fake ADO, real locked checkpoints in temporary runs."""
import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from orchestrator import cli, cli_common as C, mocks
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import clone_plans_broker as step
from tools import broker_plans as B, pipelines as P, testplans as T

RID = "2000-01"
SOURCE = {"root_configs": [293], "broker_configs": list(T.BROKER_CONFIGS),
          "ui_configs": list(T.BROKER_UI_CONFIGS), "broker_cases": [111, 112],
          "ui_cases": [222], "native_query": "SELECT [System.Id] FROM WorkItems WHERE tag='native'"}


class ADO:
    def __init__(self, path):
        self.path, self.plans, self.suites, self.points = path, {}, {}, {}
        self.writes = []
        self.failure = None

    def add(self, pid=900, complete=True, marked=False, area=None):
        root = pid * 10
        desc = B.identity(RID, T.broker_plan_name(RID))["marker"] if marked else ""
        if complete and marked:
            desc += "\n" + B._COMPLETE
        self.plans[pid] = {"id": pid, "name": T.broker_plan_name(RID),
                           "areaPath": area or T.BROKER_AREA_PATH, "iteration": T.BROKER_ITERATION,
                           "rootSuite": {"id": root}, "description": desc}
        self.suites[pid] = [{"id": root, "name": self.plans[pid]["name"],
                             "suiteType": "staticTestSuite", "inheritDefaultConfigurations": False,
                             "defaultConfigurations": [{"id": 293}]}]
        if complete:
            for n, (name, key) in enumerate(((T.BROKER_MANUAL_SUITE_NAME, "broker"),
                                             (T.BROKER_NATIVE_AUTH_SUITE_NAME, "native"),
                                             (T.BROKER_UI_SUITE_NAME, "ui")), 1):
                suite = {"id": root + n, "name": name, "parentSuite": {"id": root},
                         "suiteType": "dynamicTestSuite" if key == "native" else "staticTestSuite",
                         "inheritDefaultConfigurations": key == "native"}
                if key == "native":
                    suite["queryString"] = SOURCE["native_query"]
                else:
                    suite["defaultConfigurations"] = [{"id": c} for c in SOURCE[key + "_configs"]]
                    self.points[(pid, root+n)] = [
                        {"testCase": {"id": cid}, "configuration": {"id": cfg}, "outcome": "Passed"}
                        for cid in SOURCE[key + "_cases"] for cfg in SOURCE[key + "_configs"]]
                self.suites[pid].append(suite)
        return pid

    def get_all(self, url, timeout, **kwargs):
        if self.failure == "list":
            return False, None, "AUTH: HTTP 403"
        if "/testplan/plans?" in url:
            assert "filterActivePlans=false" in url and "includePlanDetails=true" in url
            return True, list(self.plans.values()), ""
        match = re.search(r"/Plans/(\d+)/suites\?", url)
        if match:
            return True, copy.deepcopy(self.suites.get(int(match[1]), [])), ""
        match = re.search(r"/Plans/(\d+)/Suites/(\d+)/points\?", url)
        if match:
            return True, copy.deepcopy(self.points.get((int(match[1]), int(match[2])), [])), ""
        raise AssertionError(url)

    def get(self, url, timeout):
        if self.failure == "get":
            return False, None, "HTTP 503"
        match = re.search(r"/testplan/plans/(\d+)\?", url)
        if match:
            pid = int(match[1])
            return (True, copy.deepcopy(self.plans[pid]), "") if pid in self.plans else (False, None, "HTTP 404")
        match = re.search(r"/Plans/(\d+)/suites/(\d+)\?", url)
        if match:
            suite = next(s for s in self.suites[int(match[1])] if s["id"] == int(match[2]))
            return True, copy.deepcopy(suite), ""
        raise AssertionError(url)

    def send(self, url, method, body, timeout):
        self.writes.append((url, method, copy.deepcopy(body)))
        if "/testplan/plans?" in url and method == "POST":
            record = ReleaseState.load(str(self.path)).resources[B.RESOURCE]
            assert record["status"] == "creating"
            self.source = record["source"]
            B._validate_source(self.source)
            if self.failure == "create_not_visible":
                return False, None, "timeout (unknown outcome)"
            pid = self.add(900, complete=False, marked=True)
            assert body["areaPath"] == T.BROKER_AREA_PATH
            if self.failure == "create_ack":
                return False, None, "timeout after server created plan"
            return True, copy.deepcopy(self.plans[pid]), ""
        match = re.search(r"/testplan/plans/(\d+)\?", url)
        if match and method == "PATCH":
            if self.failure == "marker":
                return False, None, "HTTP 503"
            self.plans[int(match[1])].update(body)
            return True, {}, ""
        record = ReleaseState.load(str(self.path)).resources[B.RESOURCE]
        assert record["plan_id"] == 900 and record["status"] == "created"
        match = re.search(r"/Plans/(\d+)/suites\?", url)
        if match and method == "POST":
            pid = int(match[1])
            sid = pid * 10 + len(self.suites[pid])
            self.suites[pid].append(dict(body, id=sid))
            return True, {"id": sid}, ""
        match = re.search(r"/Plans/(\d+)/suites/(\d+)\?", url)
        if match and method == "PATCH":
            s = next(s for s in self.suites[int(match[1])] if s["id"] == int(match[2]))
            s.update(body)
            return True, {}, ""
        match = re.search(r"/Plans/(\d+)/Suites/(\d+)/TestCase\?", url)
        if match and method == "POST":
            if self.failure == "cases":
                return False, None, "HTTP 400"
            pid, sid = int(match[1]), int(match[2])
            s = next(s for s in self.suites[pid] if s["id"] == sid)
            self.points[(pid, sid)] = [
                {"testCase": {"id": c["workItem"]["id"]}, "configuration": {"id": cfg["id"]}}
                for c in body for cfg in (
                    [{"id": p["configurationId"]} for p in c["pointAssignments"]]
                    if "pointAssignments" in c else s["defaultConfigurations"])]
            if self.failure == "crash_after_cases" and s["name"] == T.BROKER_UI_SUITE_NAME:
                raise SystemExit("process lost after last suite write")
            return True, {}, ""
        raise AssertionError((url, method))

    @property
    def creates(self):
        return [w for w in self.writes if "/testplan/plans?" in w[0] and w[1] == "POST"]


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    path = tmp_path / RID / "release-state.json"
    st = ReleaseState(release_id=RID, owner_email="owner@example.test", ccd="2000-01-12")
    st.save(str(path))
    ado = ADO(path)
    monkeypatch.setattr(P, "_ado_rest_get_all", ado.get_all)
    monkeypatch.setattr(P, "_ado_rest_get", ado.get)
    monkeypatch.setattr(P, "_ado_rest_send", ado.send)
    monkeypatch.setattr(B, "_snapshot", lambda timeout, rc=None: (True, copy.deepcopy(SOURCE), ""))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    return tmp_path, path, ado


@contextmanager
def locked(fixture):
    with C.state_lock(str(fixture[0]), RID):
        st = C.load_state(str(fixture[0]), RID)
        yield st
        C.save_state(st, str(fixture[0]), RID)


def test_create_then_retry_and_reopen_reuses_resource(fixture):
    with locked(fixture) as st:
        orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
        phase = next(p for p in orch.config["phases"] if p["id"] == "bug_bash")
        spec = next(s for s in phase["steps"] if s["id"] == step.ID)
        assert orch._run_auto_step(phase, spec, block_holds=True).kind == "ran"
        assert st.is_done("bug_bash", step.ID)
        assert st.resources[B.RESOURCE]["status"] == "ready"
        orch.reopen_step("bug_bash", step.ID, "Recheck existing work")
        assert not st.get_step("bug_bash", step.ID).data
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
        assert st.get_step("bug_bash", step.ID).data["plan_id"] == 900
    assert len(fixture[2].creates) == 1
    assert not any(method == "DELETE" for _, method, _ in fixture[2].writes)


def test_selective_ui_matrix_is_frozen_across_partial_build_recovery(fixture, monkeypatch):
    source = copy.deepcopy(SOURCE)
    source["ui_cases"] = [222, 223]
    source["ui_case_configs"] = {
        "222": sorted(T.BROKER_UI_CONFIGS + [293, 330]), "223": list(T.BROKER_UI_CONFIGS)}
    monkeypatch.setattr(B, "_snapshot", lambda *a: (True, copy.deepcopy(source), ""))
    ado = fixture[2]
    ado.failure = "crash_after_cases"
    with pytest.raises(SystemExit), locked(fixture) as st:
        step.build(st)
    ado.failure = None
    writes = len(ado.writes)

    def no_resnapshot(*a):
        raise AssertionError("Recovery must use frozen membership/configuration evidence")

    monkeypatch.setattr(B, "_snapshot", no_resnapshot)
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
        assert st.resources[B.RESOURCE]["source"] == source
    points = ado.points[(900, 9003)]
    assert len(points) == 10
    assert {(p["testCase"]["id"], p["configuration"]["id"]) for p in points
            if p["configuration"]["id"] in (293, 330)} == {(222, 293), (222, 330)}
    assert len(ado.creates) == 1 and len(ado.writes) == writes + 1  # completion marker only
    points[:] = [p for p in points if p["configuration"]["id"] != 293]
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    assert len(ado.writes) == writes + 1


def test_ui_repair_preview_cli_never_binds_or_saves_state(fixture, monkeypatch, capsys):
    from tests._mrwp_evidence import current_rc
    with locked(fixture) as st:
        st.pipeline_runs = {"rcs": [current_rc()]}
    before = fixture[1].read_bytes()
    called = []
    monkeypatch.setattr(B, "preview_ui_repair", lambda pid, rc:
                        (called.append((pid, rc["rc"])) is None, {"read_only": True}, ""))
    assert cli.main(["--runs-root", str(fixture[0]), "broker-plan", "--release", RID,
                     "--plan-id", "900", "--preview-ui-repair"]) == 0
    assert json.loads(capsys.readouterr().out)["preview"]["read_only"]
    assert called == [(900, 2)] and fixture[1].read_bytes() == before
    assert not fixture[2].writes


def test_competing_workers_share_one_creation(fixture):
    def worker():
        with locked(fixture) as st:
            return step.build(st).kind
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert results == ["done", "done"]
    assert len(fixture[2].creates) == 1


def test_owner_can_authorize_retry_only_after_absence_is_verified(fixture, capsys):
    ado = fixture[2]
    ado.failure = "create_not_visible"
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    args = ["--runs-root", str(fixture[0]), "broker-plan", "--release", RID, "--confirm-not-created"]
    assert cli.main(args) == 1
    capsys.readouterr()
    ado.failure = "list"
    assert cli.main(args + ["--reason", "Owner confirmed no plan and original runner stopped"]) == 1
    capsys.readouterr()
    ado.failure = None
    ado.add()
    assert cli.main(args + ["--reason", "Owner claims no plan"]) == 1
    capsys.readouterr()
    ado.plans.clear()
    assert cli.main(args + ["--reason", "Owner confirmed failed request; original runner stopped"]) == 0
    capsys.readouterr()
    with locked(fixture) as st:
        assert st.resources[B.RESOURCE]["status"] == "retry_authorized"
        assert len(st.resources[B.RESOURCE]["attempt_history"]) == 1
        assert step.build(st).kind == "done"
    assert len(ado.creates) == 2


def test_marker_failure_retries_patch_not_creation(fixture):
    ado = fixture[2]
    ado.failure = "marker"
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    ado.failure = None
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
    assert len(ado.creates) == 1

@pytest.mark.parametrize("failure", ["get", "deleted", "wrong_name"])
def test_bound_plan_lookup_never_falls_through_to_creation(fixture, failure):
    ado = fixture[2]
    ado.add()
    with locked(fixture) as st:
        st.set_step("bug_bash", step.ID, StepState(data={"plan_id": 900}))
        if failure == "deleted":
            ado.plans.clear()
        elif failure == "wrong_name":
            ado.plans[900]["name"] = "another release"
        else:
            ado.failure = failure
        assert step.build(st).kind == "blocked"
    assert not ado.writes


@pytest.mark.parametrize("marked", [False, True])
def test_lost_local_id_recovers_single_complete_plan_without_writes(fixture, marked):
    ado = fixture[2]
    ado.add(marked=marked)
    before = copy.deepcopy(ado.points)
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
        assert st.resources[B.RESOURCE]["plan_id"] == 900
    assert ado.points == before and not ado.writes


def test_duplicates_block_then_owner_binds_without_reopening_or_creating(fixture, capsys):
    ado = fixture[2]
    ado.add(900)
    ado.add(901)
    with locked(fixture) as st:
        out = step.build(st)
        assert out.kind == "blocked" and "900, 901" in out.reason
    args = ["--runs-root", str(fixture[0]), "broker-plan", "--release", RID]
    assert cli.main(args) == 0
    assert len(json.loads(capsys.readouterr().out)["candidates"]) == 2
    assert cli.main(args + ["--plan-id", "901"]) == 1
    capsys.readouterr()
    assert cli.main(args + ["--plan-id", "901", "--reason", "Owner selected existing plan with results"]) == 0
    capsys.readouterr()
    with locked(fixture) as st:
        assert st.resources[B.RESOURCE]["selection"]["by"] == st.owner_email
        assert not st.is_done("bug_bash", step.ID)
        assert step.build(st).kind == "done"
        assert st.get_step("bug_bash", step.ID).data["plan_id"] == 901
    assert not ado.writes
    assert cli.main(args + ["--plan-id", "900", "--reason", "Replace"]) == 1


def test_discovery_failure_blocks_without_reserving_or_creating(fixture):
    fixture[2].failure = "list"
    with locked(fixture) as st:
        out = step.build(st)
        assert out.kind == "blocked" and "403" in out.reason
        assert not st.resources[B.RESOURCE]
    assert not fixture[2].writes


@pytest.mark.parametrize("failure", ["create_ack", "create_not_visible", "cases"])
def test_uncertain_or_partial_creation_never_reposts_plan(fixture, failure):
    ado = fixture[2]
    ado.failure = failure
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    ado.failure = None
    with locked(fixture) as st:
        Orchestrator(C.DEFAULT_CONFIG, st, mocks={}).reopen_step("bug_bash", step.ID)
        assert step.build(st).kind == "blocked"
    assert len(ado.creates) == 1
    assert not any(method == "DELETE" for _, method, _ in ado.writes)


def test_crash_after_last_write_recovers_from_checkpoint_without_repeating_writes(fixture):
    ado = fixture[2]
    ado.failure = "crash_after_cases"
    with pytest.raises(SystemExit):
        with locked(fixture) as st:
            step.build(st)
    saved = ReleaseState.load(str(fixture[1]))
    assert saved.resources[B.RESOURCE]["status"] == "created"
    assert saved.resources[B.RESOURCE]["plan_id"] == 900
    ado.failure = None
    writes = len(ado.writes)
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
    assert len(ado.creates) == 1
    assert all(method == "PATCH" and "/testplan/plans/" in url
               for url, method, _ in ado.writes[writes:])


@pytest.mark.parametrize("damage", ["suite", "point", "config", "query", "root_config", "marker", "area"])
def test_incomplete_or_wrong_plan_is_not_adopted(fixture, damage):
    ado = fixture[2]
    ado.add(marked=True)
    if damage == "suite":
        ado.suites[900].pop()
    elif damage == "point":
        ado.points[(900, 9001)].pop()
    elif damage == "config":
        ado.points[(900, 9001)][0]["configuration"]["id"] = 999
    elif damage == "query":
        ado.suites[900][2]["queryString"] = ""
    elif damage == "root_config":
        ado.suites[900][0]["defaultConfigurations"] = []
    elif damage == "marker":
        ado.plans[900]["description"] = B._MARKER + "1999-12"
    else:
        ado.plans[900]["areaPath"] = "AnotherProject"
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    assert not ado.writes


def test_saved_source_detects_entire_missing_case_even_if_remaining_matrix_is_valid(fixture):
    ado = fixture[2]
    ado.failure = "crash_after_cases"
    with pytest.raises(SystemExit):
        with locked(fixture) as st:
            step.build(st)
    ado.failure = None
    ado.points[(900, 9001)] = [p for p in ado.points[(900, 9001)] if p["testCase"]["id"] != 112]
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    assert len(ado.creates) == 1


def test_old_project_root_plan_requires_explicit_observed_area_selection(fixture, capsys):
    ado = fixture[2]
    ado.add(area=T.PROJECT)
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    assert cli.main(["--runs-root", str(fixture[0]), "broker-plan", "--release", RID,
                     "--plan-id", "900", "--area-path", T.PROJECT,
                     "--reason", "Owner confirmed existing plan at the project root"]) == 0
    capsys.readouterr()
    with locked(fixture) as st:
        assert step.build(st).kind == "done"
    assert not ado.writes


def test_checkpoint_requires_live_lock_and_is_not_serialized(fixture):
    st = C.load_state(str(fixture[0]), RID)
    with pytest.raises(RuntimeError, match="lock"):
        st.checkpoint()
    with C.state_lock(str(fixture[0]), RID):
        with pytest.raises(RuntimeError, match="lock"):
            st.checkpoint()
    with locked(fixture) as st:
        st.checkpoint()
    with pytest.raises(RuntimeError, match="lock"):
        st.checkpoint()
    with C.state_lock(str(fixture[0]), RID):
        with pytest.raises(RuntimeError, match="lock"):
            st.checkpoint()
    assert "_checkpoint" not in json.loads(fixture[1].read_text())


def test_checkpoint_failure_prevents_first_external_write(fixture, monkeypatch):
    def fail():
        raise OSError("disk full")
    with C.state_lock(str(fixture[0]), RID):
        st = C.load_state(str(fixture[0]), RID)
        st._checkpoint = fail
        with pytest.raises(OSError, match="disk full"):
            step.build(st)
    assert not fixture[2].writes


@pytest.mark.parametrize("mode", ["cap", "repeated", "malformed", "second_page_error", "count"])
def test_incomplete_paging_is_failure_not_empty_success(monkeypatch, mode):
    calls = []
    def page(url, timeout):
        calls.append(url)
        if mode == "malformed":
            return True, {}, {}, ""
        if mode == "count":
            return True, {"count": 1, "value": []}, {}, ""
        if mode == "second_page_error" and len(calls) > 1:
            return False, None, {}, "HTTP 503"
        return True, {"value": []}, {"x-ms-continuationtoken": "next +/&"}, ""
    monkeypatch.setattr(P, "_ado_rest_get_h", page)
    ok, items, detail = P._ado_rest_get_all("https://test.invalid?api-version=7.1", 1,
                                         cap_pages=1 if mode == "cap" else 3)
    assert not ok and items is None and detail
    if len(calls) > 1:
        assert "next%20%2B%2F%26" in calls[1]


@pytest.mark.parametrize("value", [0, False, "", [], None, "oops"])
def test_present_invalid_stored_id_blocks_before_discovery_or_creation(fixture, value):
    with locked(fixture) as st:
        st.set_step("bug_bash", step.ID, StepState(data={"plan_id": value}))
        assert step.build(st).kind == "blocked"
    assert not fixture[2].writes


@pytest.mark.parametrize("source", [{}, None, [], {"native_query": "SELECT x"}, dict(SOURCE, broker_cases=[])])
def test_invalid_saved_source_never_adopts_partial_plan(fixture, source):
    ado = fixture[2]
    ado.add(marked=True)
    ado.plans[900]["description"] = B.identity(RID, T.broker_plan_name(RID))["marker"]
    ado.points[(900, 9001)] = [p for p in ado.points[(900, 9001)] if p["testCase"]["id"] != 112]
    with locked(fixture) as st:
        st.resources[B.RESOURCE] = {"identity": B.identity(RID, T.broker_plan_name(RID)),
                                    "status": "created", "plan_id": 900, "source": source}
        assert step.build(st).kind == "blocked"
    assert not ado.writes


@pytest.mark.parametrize("entry", [{}, {"id": 900}, {"id": 900, "name": ""},
                                  {"name": "Other plan"}, {"id": 0, "name": "Other plan"},
                                  {"id": 900, "name": "Other plan", "description": []}])
def test_malformed_plan_listing_cannot_authorize_create(fixture, monkeypatch, entry):
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a, **k: (True, [entry], ""))
    with locked(fixture) as st:
        assert step.build(st).kind == "blocked"
    assert not fixture[2].writes
