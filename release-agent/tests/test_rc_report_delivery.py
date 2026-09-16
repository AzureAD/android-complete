"""Consolidated report quality, generation and receipt safety; no separate gate."""
import copy
import json
from argparse import Namespace
from datetime import date

import pytest

from orchestrator import cli_common as C, delivery as D, mocks, schedule
from orchestrator.commands.delivery_cmd import finish
from orchestrator.commands.step_action import prepare_step
from orchestrator.commands import rc_poll, rc_report, release
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.state import ReleaseState, StepState
from tests._harness import CONFIG, _ready_for_rc_report, _seed_rc_pipeline


@pytest.fixture
def ready(monkeypatch):
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26", owner_email="test@example.com")
    _ready_for_rc_report(st)
    _seed_rc_pipeline(st, {"total": 100, "passed": 100, "failed": 0},
                      {"total": 100, "passed": 100, "failed": 0})
    orch = Orchestrator(CONFIG, st, mocks={}, as_of=date(2026, 9, 11))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    monkeypatch.setattr(schedule, "now_local", lambda zone: orch.now_local.astimezone(zone))
    return st, orch


def prepared(st, orch):
    args = Namespace(phase="build_verify", step="rc_report", release=st.release_id)
    item = prepare_step(args, st, orch)["notifications"][0]
    D.offer(orch, item)
    return item


def delivered(st, orch, *, finalize=True):
    item = prepared(st, orch)
    claim = D.claim(orch, item["id"], item["hash"], "test-worker")
    D.result(orch, item["id"], claim["execution_id"], "sent",
             "Simulated email accepted", {"id": "test-message"})
    if finalize:
        assert finish(orch, item["id"])
    return item


@pytest.mark.parametrize("passed,auth_failed,verdict", [
    (100, False, "pass"), (95, False, "pass"),
    (50, False, "attention"), (100, True, "attention"),
])
def test_confirmed_delivery_applies_both_frozen_quality_gates(ready, passed, auth_failed, verdict):
    st, orch = ready
    _seed_rc_pipeline(st, {"total": 100, "passed": passed, "failed": 100 - passed},
                      {"total": 100, "passed": passed, "failed": 100 - passed})
    if auth_failed:
        auth = st.pipeline_runs["rcs"][-1]["auth"]
        auth["build"]["result"], auth["test"] = "failed", None
    before = copy.deepcopy(st.pipeline_runs)
    item = delivered(st, orch, finalize=False)
    assert item["completion"]["status"] == verdict
    assert not st.is_done("build_verify", "rc_report")
    assert finish(orch, item["id"])
    record = st.get_step("build_verify", "rc_report")
    assert record.status == ("done" if verdict == "pass" else "blocked")
    assert record.note == item["completion"]["note"] and record.links == item["completion"]["links"]
    assert orch.current_phase_id() == ("bug_bash" if verdict == "pass" else "build_verify")
    assert st.gate_decisions == [] and st.pipeline_runs == before
    assert "build_verify.bugbash_approval" not in orch.handlers.handler_by_key


def test_blocked_quality_verdict_has_no_approval_or_repeated_report(ready, monkeypatch):
    st, orch = ready
    ui = {"total": 100, "passed": 50, "failed": 50}
    _seed_rc_pipeline(st, ui, ui)
    item = delivered(st, orch)
    assert orch.approve_gate("Invented separate approval").kind == "idle"
    assert orch.complete_step("Invented success").kind == "idle"
    assert orch.scout_pending_steps() == []
    monkeypatch.setattr(C, "load_orch", lambda *_: (st, orch))
    args = Namespace(runs_root="", release=st.release_id, config=CONFIG)
    assert rc_report.cmd_record_rc_report(args) == 1
    assert st.get_step("build_verify", "rc_report").status == "blocked"
    assert D.claim(orch, item["id"], item["hash"], "other")["permission_to_send"] is False
    assert len(st.notification_deliveries[item["id"]]["attempts"]) == 1


@pytest.mark.parametrize("change", ["owner", "generation"])
@pytest.mark.parametrize("claimed", [False, True])
def test_report_delivery_is_bound_to_owner_and_generation(ready, change, claimed):
    st, orch = ready
    item = delivered(st, orch, finalize=False) if claimed else prepared(st, orch)
    if change == "owner":
        st.owner_email = "other@example.com"
    else:
        assert orch.reopen("build_verify", "auth_ecs", "Owner reviewed the prior delivery").changed
    if claimed:
        assert finish(orch, item["id"])
        record = st.notification_deliveries[item["id"]]
        assert record["status"] == "sent" and record["completion"]["status"] == "suppressed"
        assert record["attempts"][-1]["receipt"] == {"id": "test-message"}
    else:
        with pytest.raises(ValueError, match="checkpoint changed|generation changed"):
            D.claim(orch, item["id"], item["hash"], "worker")
    assert not st.is_done("build_verify", "rc_report")


@pytest.mark.parametrize("change", ["receipt", "completion", "execution"])
def test_corrupt_report_receipt_or_completion_never_advances(ready, change):
    st, orch = ready
    item = delivered(st, orch, finalize=False)
    record = st.notification_deliveries[item["id"]]
    if change == "receipt":
        record["attempts"][-1]["hash"] = "corrupt"
    elif change == "completion":
        record["descriptor"]["completion"]["status"] = "attention"
    else:
        step = st.get_step("build_verify", "rc_report")
        step.execution["id"] = "unrelated-execution"
        st.set_step("build_verify", "rc_report", step)
    with pytest.raises(ValueError):
        finish(orch, item["id"])
    assert not st.is_done("build_verify", "rc_report")


def test_unclaimed_legacy_acknowledgement_cannot_invent_report_delivery(ready, monkeypatch):
    st, orch = ready
    monkeypatch.setattr(C, "load_orch", lambda *_: (st, orch))
    args = Namespace(runs_root="", release=st.release_id, config=CONFIG)
    assert rc_report.cmd_record_rc_report(args) == 1
    assert not st.notification_deliveries
    assert not st.is_done("build_verify", "rc_report")


def test_claimed_report_cannot_complete_without_a_transport_receipt(ready):
    st, orch = ready
    ui = {"total": 100, "passed": 50, "failed": 50}
    _seed_rc_pipeline(st, ui, ui)
    item = prepared(st, orch)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    with pytest.raises(ValueError, match="acknowledged delivery receipt"):
        orch.record_scout_step("build_verify", "rc_report", "pass",
                               execution_id=claim["execution_id"])
    D.result(orch, item["id"], claim["execution_id"], "sent", "Simulated acknowledgement")
    assert finish(orch, item["id"])
    assert st.get_step("build_verify", "rc_report").status == "blocked"


@pytest.mark.parametrize("step", [
    "checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local",
    "auth_ecs", "rc_report",
])
def test_reopening_report_inputs_invalidates_report_and_preserves_receipts(ready, step):
    st, orch = ready
    item = delivered(st, orch)
    before = copy.deepcopy(st.pipeline_runs), copy.deepcopy(st.notification_deliveries)
    st.set_step("preflight", "vitals", StepState(status="done", note="Unrelated attestation"))
    result = orch.reopen("build_verify", step, "Recapture requested")
    assert result.changed and "build_verify.rc_report" in result.affected
    assert not st.is_done("build_verify", "rc_report")
    assert st.get_step("build_verify", "rc_report").invalidated_at
    assert st.get_step("preflight", "vitals").note == "Unrelated attestation"
    assert (st.pipeline_runs, st.notification_deliveries) == before
    assert st.notification_deliveries[item["id"]]["status"] == "sent"


def test_retrigger_uses_current_dependency_closure_without_rewriting_snapshots(ready, tmp_path):
    st, orch = ready
    delivered(st, orch)
    before = copy.deepcopy(st.pipeline_runs), copy.deepcopy(st.notification_deliveries)
    affected = orch.workflow.invalidation_closure("build_verify", release._RC_RETRIGGER_ANCHOR)
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG, reason="New RC")
    assert release.cmd_rc_retriggered(args) == 0
    saved = C.load_state(str(tmp_path), st.release_id)
    assert all(saved.get_step(s.phase_id, s.id).status == "pending" for s in affected)
    assert saved.is_done("build_verify", "checker_fired")
    assert saved.is_done("build_verify", "orchestrator_health")
    assert (saved.pipeline_runs, saved.notification_deliveries) == before


def test_poller_reports_current_quality_hold_without_a_duplicate_email(ready, tmp_path, capsys):
    st, orch = ready
    ui = {"total": 100, "passed": 50, "failed": 50}
    _seed_rc_pipeline(st, ui, ui)
    item = delivered(st, orch)
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T12:00:00Z", as_of="2026-09-11")
    assert rc_poll.cmd_poll_rc(args) == 0
    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "blocked" and decision["step"] == "rc_report"
    saved = C.load_state(str(tmp_path), st.release_id)
    assert len(saved.notification_deliveries[item["id"]]["attempts"]) == 1
    assert saved.gate_decisions == []


def test_report_finalization_is_retryable_without_transport_replay(ready, tmp_path):
    st, orch = ready
    item = delivered(st, orch, finalize=False)
    C.save_state(st, str(tmp_path), st.release_id)
    saved, restarted = C.load_orch(str(tmp_path), st.release_id, CONFIG, orch.now_local)
    assert finish(restarted, item["id"])
    assert not finish(restarted, item["id"])
    assert saved.is_done("build_verify", "rc_report")
    assert len(saved.notification_deliveries[item["id"]]["attempts"]) == 1
    assert D.claim(restarted, item["id"], item["hash"], "other")["permission_to_send"] is False


@pytest.mark.parametrize("outcome", ["claimed", "uncertain", "sent", "not_sent"])
def test_changed_capture_cannot_bypass_prior_delivery_or_reuse_its_verdict(ready, outcome):
    st, orch = ready
    item = prepared(st, orch)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    if outcome != "claimed":
        D.result(orch, item["id"], claim["execution_id"], outcome, "Simulated transport result")
    before = copy.deepcopy(st.notification_deliveries[item["id"]])
    st.pipeline_runs["rcs"][-1]["ecs"]["resolved_at"] = "new capture"
    if outcome == "sent":
        assert not D.claim(orch, item["id"], item["hash"], "other")["permission_to_send"]
        assert finish(orch, item["id"])
        assert st.notification_deliveries[item["id"]]["completion"]["status"] == "suppressed"
    else:
        with pytest.raises(ValueError):
            D.claim(orch, item["id"], item["hash"], "other")
        assert st.notification_deliveries[item["id"]] == before
    assert not st.is_done("build_verify", "rc_report")


@pytest.mark.parametrize("result", ["failed", "canceled"])
def test_failed_auth_capture_delivers_frozen_hold_despite_blocked_telemetry(
        ready, result, tmp_path, monkeypatch, capsys):
    st, _ = ready
    st.set_step("build_verify", "auth_ecs", StepState())
    st.set_step("build_verify", "telemetry_verify", StepState())
    orch = Orchestrator(CONFIG, st, as_of=date(2026, 9, 11), mocks={
        "build_verify.auth_ecs": {"auth_build": {
            "build_id": 900010, "rc": 1, "status": "completed", "result": result}},
    })
    orch.step_once()
    assert st.is_done("build_verify", "auth_ecs")
    assert st.pipeline_runs["rcs"][-1]["auth"]["build"]["result"] == result
    assert orch.scout_pending_steps() == ["telemetry_verify", "rc_report"]
    telemetry = prepare_step(Namespace(
        phase="build_verify", step="telemetry_verify", release=st.release_id), st, orch)
    assert telemetry["kind"] == "blocked"
    assert st.get_step("build_verify", "telemetry_verify").status == "blocked"
    monkeypatch.setattr(C, "load_orch", lambda *_: (st, orch))
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T12:00:00Z", as_of="2026-09-11")
    assert rc_poll.cmd_poll_rc(args) == 0
    assert json.loads(capsys.readouterr().out)["steps"] == ["rc_report"]
    item = delivered(st, orch)
    assert item["completion"]["status"] == "attention"
    assert item["scope"]["state_matches"]
    assert st.get_step("build_verify", "rc_report").status == "blocked"
    assert orch.current_phase_id() == "build_verify"
    assert st.get_step("bug_bash", "clone_plans_broker").status == "pending"
    from steps import discover
    assert len(discover()) == 38
    assert set(discover()) <= orch.handlers.handler_by_key.keys()


@pytest.mark.parametrize("rows", [None, 0])
def test_clean_delivered_report_cannot_clear_unknown_or_zero_telemetry(
        ready, rows, monkeypatch, tmp_path):
    from orchestrator.commands import telemetry_cmd
    st, orch = ready
    st.set_step("build_verify", "telemetry_verify", StepState())
    st.pipeline_runs["rcs"][-1]["auth"]["build"]["build_number"] = "6.2609.6056-rc900010"
    if rows is not None:
        monkeypatch.setattr(C, "load_orch", lambda *_: (st, orch))
        assert telemetry_cmd.cmd_record_telemetry(Namespace(
            runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
            rows=rows, version="6.2609.6056", build_id="900010", as_of="2026-09-11")) == 2
    item = delivered(st, orch)
    assert item["completion"]["status"] == "pass"
    assert st.is_done("build_verify", "rc_report")
    assert not st.is_done("build_verify", "telemetry_verify")
    assert orch.current_phase_id() == "build_verify"
    assert orch.step_action_guard("bug_bash", "send_invite").kind == "blocked"


def test_telemetry_reopen_preserves_independent_report_but_invalidates_bug_bash(ready):
    st, orch = ready
    item = delivered(st, orch)
    report = copy.deepcopy(st.get_step("build_verify", "rc_report"))
    result = orch.reopen("build_verify", "telemetry_verify", "Recheck telemetry")
    assert result.changed
    assert "build_verify.rc_report" not in result.affected
    assert "bug_bash.clone_plans_broker" in result.affected
    assert st.get_step("build_verify", "rc_report") == report
    assert st.notification_deliveries[item["id"]]["status"] == "sent"
    assert orch.current_phase_id() == "build_verify"
