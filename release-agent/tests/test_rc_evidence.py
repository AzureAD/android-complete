"""Sequential eligibility and complete RC evidence are separate requirements."""
import copy
import json
from argparse import Namespace
from datetime import date

import pytest

from tests._harness import CONFIG, _seed_rc_pipeline, _ready_for_rc_report, _bv_state, _bv_build
from orchestrator import cli, cli_common as C, mocks
from orchestrator.commands import rc_report as RR, rc_poll as RP
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState, StepState
from steps.build_verify import _common as K, rc_report, _rc_report_rendering as rendering
from tools.pipelines import AUTH_UI_SUITES


@pytest.fixture
def ready():
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", owner_email="test@example.com")
    _ready_for_rc_report(st)
    ui = {"total": 100, "passed": 100, "failed": 0}
    _seed_rc_pipeline(st, ui, ui)
    return st


def args_for(tmp_path, st):
    C.save_state(st, str(tmp_path), st.release_id)
    return Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     as_of="2026-09-10", now="2026-09-10T12:00:00Z")


@pytest.mark.parametrize("path,value", [
    (("checker", "run_id"), None), (("orchestrator", "run_id"), "None"),
    (("rc",), None), (("ecs",), None), (("local",), None),
    (("ecs", "run_id"), "abc"), (("ecs", "run_id"), True),
    (("ecs", "complete"), False), (("local", "complete"), None),
    (("ecs", "ran"), 0), (("ecs", "tests"), None),
    (("local", "tests", "categories"), {}),
    (("ecs", "tests", "categories", "ui", "total"), 0),
    (("ecs", "tests", "categories", "ui", "passed"), 101),
    (("ecs", "tests", "categories", "ui", "failed"), -1),
    (("ecs", "tests", "categories", "ui", "total"), "100"),
    (("auth",), None), (("auth", "build", "rc"), 2),
    (("auth", "build", "result"), None), (("auth", "build", "complete"), False),
    (("auth", "test"), None), (("auth", "test", "complete"), False),
    (("auth", "test", "run_id"), None), (("auth", "test", "suites"), None),
])
def test_builder_and_recorder_refuse_missing_evidence(ready, tmp_path, path, value):
    container = ready.pipeline_runs if path[0] in ("checker", "orchestrator") else K.latest_rc(ready)
    for key in path[:-1]:
        container = container[key]
    container[path[-1]] = value
    assert rc_report.build(ready).kind == "blocked"
    args = args_for(tmp_path, ready)
    before = vars(C.load_state(str(tmp_path), ready.release_id))
    assert RR.cmd_record_rc_report(args) == 1
    assert vars(C.load_state(str(tmp_path), ready.release_id)) == before


def test_missing_provider_and_zero_ui_never_gate_clean():
    for model in ({}, {"mrwp": {"ECS": {"tests": {"categories": {"ui": {
            "total": 100, "passed": 100, "failed": 0}}}}}}):
        gate, auth = rc_report.rc_ui_gate(model), rc_report.auth_report_gate(model)
        assert gate["blocking"]
        assert auth["blocking"]
        html = rendering.rc_email_html(model, {}, gate, auth, rc_report.rc_next_action(model))
        assert "MRWP ECS: evidence unavailable" in html or "MRWP Local: evidence unavailable" in html
        assert "RC verified" not in html


def test_real_rc_counts_hold_at_84_5_and_missing_auth_still_prevents_report(ready, tmp_path):
    current = K.latest_rc(ready)
    current["ecs"]["tests"]["categories"]["ui"] = {"passed": 168, "total": 204, "failed": 30}
    current["local"]["tests"]["categories"]["ui"] = {"passed": 137, "total": 157, "failed": 8}
    assert rc_report.rc_ui_gate(rc_report.rc_report_model(ready))["pass_pct"] == 84.5
    assert rc_report.build(ready).kind == "needs_skill"
    assert RR.cmd_record_rc_report(args_for(tmp_path, ready)) == 2
    assert C.load_state(str(tmp_path), ready.release_id).get_step("build_verify", "rc_report").status == "blocked"
    current.pop("auth")
    assert rc_report.build(ready).kind == "blocked"


@pytest.mark.parametrize("failure", ["build", "suite", "absent-suite", "empty-suite"])
def test_evaluated_auth_failures_reportable_when_prerequisites_settled(ready, failure):
    auth = K.latest_rc(ready)["auth"]
    suite = auth["test"]["suites"][AUTH_UI_SUITES[0]]
    if failure == "build":
        auth["build"]["result"], auth["test"] = "failed", None
    elif failure == "suite":
        suite.update(passed=10, failed=90, total=100, pct=100)
    elif failure == "absent-suite":
        suite.update(present=False, passed=0, failed=0, total=0, pct=None)
    else:
        suite.update(passed=0, failed=0, total=0, pct=None)
    auth["verdict"] = "clean"
    model = rc_report.rc_report_model(ready)
    assert rc_report.report_readiness(model)["ready"]
    assert rc_report.auth_report_gate(model)["blocking"]
    out = rc_report.build(ready)
    assert out.kind == "needs_skill" and "HOLD" in out.payload["subject"]
    assert out.payload["followup_command"] == "record-rc-report"
    assert "RC verified" not in out.payload["body"]
    assert "No automatic advance" in out.payload["_plain_body"]


def test_gate_uses_unrounded_counts(ready):
    for key in ("ecs", "local"):
        K.latest_rc(ready)[key]["tests"]["categories"]["ui"] = {
            "total": 2000, "passed": 1799, "failed": 201}
    assert rc_report.rc_ui_gate(rc_report.rc_report_model(ready))["blocking"]
    for key in ("ecs", "local"):
        K.latest_rc(ready)[key]["tests"]["categories"]["ui"] = {
            "total": 20000, "passed": 19999, "failed": 1}
    assert rc_report.rc_ui_gate(rc_report.rc_report_model(ready))["verdict"] == "warn"


def test_cli_dispatch_and_recorder_cannot_bypass_predecessors(ready, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(mocks, "load_mocks", lambda *a, **k: {})
    ready.steps.pop("build_verify.telemetry_verify")
    C.save_state(ready, str(tmp_path), ready.release_id)
    base = ["--config", CONFIG, "--runs-root", str(tmp_path)]
    before = copy.deepcopy(vars(C.load_state(str(tmp_path), ready.release_id)))
    assert cli.main(base + ["step-action", "--release", ready.release_id,
                            "--phase", "build_verify", "--step", "rc_report"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "blocked"
    assert cli.main(base + ["record-rc-report", "--release", ready.release_id]) == 1
    assert "prerequisite" in json.loads(capsys.readouterr().out)["error"]
    assert vars(C.load_state(str(tmp_path), ready.release_id)) == before


def test_skipping_prerequisite_does_not_fabricate_evidence(ready, tmp_path):
    ready.set_step("build_verify", "auth_ecs", StepState(status="skipped", by="human"))
    K.latest_rc(ready).pop("auth")
    orch = Orchestrator(CONFIG, ready, mocks={})
    assert orch.step_action_guard("build_verify", "rc_report") is None
    assert rc_report.build(ready).kind == "blocked"
    assert RR.cmd_record_rc_report(args_for(tmp_path, ready)) == 1


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_recorder_preserves_terminal_records(ready, tmp_path, status):
    ready.set_step("build_verify", "rc_report", StepState(
        status=status, by="human", note="Owner reviewed", data={"review": "retained"}))
    ready.pipeline_runs = {}
    args = args_for(tmp_path, ready)
    before = vars(C.load_state(str(tmp_path), ready.release_id))
    assert RR.cmd_record_rc_report(args) == 0
    assert vars(C.load_state(str(tmp_path), ready.release_id)) == before


def test_new_partial_rc_cannot_reuse_previous_complete_rc(ready):
    old = copy.deepcopy(K.latest_rc(ready))
    K.stash_mrwp(ready, "ECS", {**old["ecs"], "run_id": "99999"}, rc=2)
    assert rc_report.rc_report_model(ready)["rc"] == 2
    assert not rc_report.report_readiness(rc_report.rc_report_model(ready))["ready"]
    assert rc_report.build(ready).kind == "blocked"


def test_auth_inflight_test_is_not_captured():
    st, orch = _bv_state({"build_verify.auth_ecs": {
        "auth_build": {"build_id": 123, "rc": 1, "status": "completed", "result": "succeeded"},
        "test_build": 124, "test_status": "inProgress", "suites": {}}})
    assert _bv_build(orch, st, "auth_ecs")["kind"] == "in_progress"
    assert not K.latest_rc(st).get("auth")


def test_mrwp_missing_summary_retries_instead_of_finishing():
    st, orch = _bv_state({"build_verify.mrwp_ecs": {
        "mrwp_id": 123, "stages": [{"name": "UI", "state": "completed", "result": "succeeded"}],
        "tests": None}})
    assert _bv_build(orch, st, "mrwp_ecs")["kind"] == "blocked"


@pytest.mark.parametrize("sid,status,expected", [
    ("auth_ecs", "blocked", "blocked"), ("auth_ecs", "in_flight", "waiting"),
    ("telemetry_verify", "blocked", "blocked"), ("telemetry_verify", "pending", "ready"),
    ("mrwp_local", "pending", "waiting"),
])
def test_poll_does_not_resolve_premature_done_report(
        ready, tmp_path, monkeypatch, capsys, sid, status, expected):
    ready.set_step("build_verify", "rc_report", StepState(status="done", by="scout"))
    ready.set_step("build_verify", sid, StepState(status=status, note="not finished"))
    monkeypatch.setattr(Orchestrator, "run_until_gate", lambda self: [])
    assert RP.cmd_poll_rc(args_for(tmp_path, ready)) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == expected
    if expected == "ready":
        assert decision["steps"] == ["telemetry_verify"]


@pytest.mark.parametrize("status,by,expected", [
    ("skipped", "human", "overridden"), ("done", "human", "overridden"), ("done", "scout", "passed")])
def test_resolved_poll_and_prompt_distinguish_override(
        ready, tmp_path, monkeypatch, capsys, status, by, expected):
    ready.set_step("build_verify", "rc_report", StepState(status=status, by=by, note="Owner decision"))
    monkeypatch.setattr(Orchestrator, "run_until_gate", lambda self: [])
    assert RP.cmd_poll_rc(args_for(tmp_path, ready)) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "resolved" and decision["status"] == expected
    prompt = rc_report.automation_prompt(ready.release_id, {"interval": True})
    assert "inspect decision.status" in prompt
    assert "never describe an override as PASSED" in prompt


def test_sequential_discovery_and_dispatch_wait_for_predecessors(ready):
    ready.steps.pop("build_verify.mrwp_local")
    ready.steps.pop("build_verify.telemetry_verify")
    orch = Orchestrator(CONFIG, ready, mocks={})
    assert orch.scout_pending_steps() == []
    assert orch.step_action_guard("build_verify", "rc_report").kind == "blocked"
    orch.skip_step("build_verify", "mrwp_local", "Verified externally")
    assert orch.scout_pending_steps() == ["telemetry_verify"]
    assert orch.step_action_guard("build_verify", "telemetry_verify") is None
    orch.skip_step("build_verify", "telemetry_verify", "Owner override")
    assert orch.scout_pending_steps() == ["rc_report"]


def test_phase_zero_parallel_uses_only_explicit_dependencies(ready):
    ready.steps = {}
    orch = Orchestrator(CONFIG, ready, as_of=date(2026, 9, 10),
                        mocks={"preflight.flight_reminder": {"outcome": "done"}})
    phase = orch.config["phases"][0]
    assert phase["execution"] == "parallel"
    assert "flight_reminder" in orch.scout_pending_steps()
    assert orch.step_action_guard("preflight", "flight_reminder") is None
    orch.step_once()
    assert ready.is_done("preflight", "flight_reminder")
    assert not ready.is_done("preflight", "notice")
    ready.set_step("preflight", "flight_reminder", StepState())
    step = next(s for s in phase["steps"] if s["id"] == "flight_reminder")
    step["depends_on"] = ["notice"]
    assert "flight_reminder" not in orch.scout_pending_steps()
    assert orch.step_action_guard("preflight", "flight_reminder").kind == "blocked"
    orch.skip_step("preflight", "notice", "Owner override")
    assert orch.step_action_guard("preflight", "flight_reminder") is None


def test_sequential_engine_respects_explicit_dependencies_and_mocks(ready):
    ready.steps.pop("build_verify.telemetry_verify")
    orch = Orchestrator(CONFIG, ready, mocks={"build_verify.telemetry_verify": {"outcome": "done"}})
    phase = orch._find_step("build_verify", "telemetry_verify")
    telemetry = next(s for s in phase["steps"] if s["id"] == "telemetry_verify")
    assert "rc_report" not in orch.scout_pending_steps()
    telemetry["depends_on"] = ["rc_report"]
    assert orch.step_once().kind == "waiting"
    assert not ready.is_done("build_verify", "telemetry_verify")
    telemetry.pop("depends_on")
    orch.step_once()
    assert ready.is_done("build_verify", "telemetry_verify")
    assert orch.step_action_guard("build_verify", "rc_report") is None
