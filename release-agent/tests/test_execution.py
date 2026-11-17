"""Execution ownership and locking, using temporary state and no external actions."""
import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from orchestrator import cli, cli_common as C, delivery as D, mocks
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import NeedsSkill
from orchestrator.state import ReleaseState, StepState
from tests._harness import _active_step

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    monkeypatch.setenv("RELEASE_AGENT_MOCKS", str(tmp_path / "absent.yaml"))
    st = ReleaseState(release_id="2000-01", ccd="2000-01-12", timezone="UTC")
    _active_step(st, "ccd", "final_reminder")
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))
    return tmp_path, path


def invoke(run, capsys, command, *options):
    rc = cli.main(["--runs-root", str(run[0]), command, "--release", "2000-01",
                   "--phase", "ccd", "--step", "final_reminder", *options])
    return rc, capsys.readouterr().out


def reserve(run, capsys):
    base = ["--runs-root", str(run[0]), "notification"]
    assert cli.main(base + ["prepare", "--release", "2000-01", "--source", "step",
                           "--phase", "ccd", "--step", "final_reminder"]) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    assert cli.main(base + ["claim", "--release", "2000-01", "--id", item["id"],
                           "--hash", item["hash"], "--executor", "worker-A"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["permission_to_send"]
    return out["execution_id"]


def report_result(run, capsys, execution_id, outcome="sent", review=False):
    state = ReleaseState.load(str(run[1]))
    notification_id = next((
        key
        for key, record in state.notification_deliveries.items()
        if any(
            attempt.get("id") == execution_id
            for attempt in record.get("attempts", [])
        )
    ), next(iter(state.notification_deliveries)))
    receipt = []
    if outcome == "sent":
        receipt_path = run[0] / f"receipt-{execution_id}.json"
        item = state.notification_deliveries[notification_id]["descriptor"]
        receipt_path.write_text(json.dumps(D.exact_payload_receipt(item, {"accepted": True})),
                                encoding="utf-8")
        receipt = ["--receipt-file", str(receipt_path)]
    rc = cli.main(["--runs-root", str(run[0]), "notification", "result",
                   "--release", "2000-01", "--id", notification_id,
                   "--execution-id", execution_id, "--outcome", outcome,
                   "--evidence", "Test provider evidence", *receipt,
                   *(["--owner-review"] if review else [])])
    return rc, capsys.readouterr().out


@pytest.mark.real_revision
def test_two_workers_only_one_receives_reserved_work(run, capsys):
    base = ["--runs-root", str(run[0]), "notification", "prepare", "--release", "2000-01",
            "--source", "step", "--phase", "ccd", "--step", "final_reminder"]
    assert cli.main(base) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    args = [sys.executable, "-m", "orchestrator.cli", "--runs-root", str(run[0]),
            "notification", "claim", "--release", "2000-01", "--id", item["id"],
            "--hash", item["hash"], "--executor"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(subprocess.run, args + [owner], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
                for owner in ("A", "B")]
        results = [job.result() for job in jobs]
    rows = [json.loads(r.stdout) for r in results]
    assert sum(bool(r["permission_to_send"]) for r in rows) == 1
    loser = next(r for r in rows if not r["permission_to_send"])
    assert "payload" not in loser and "tool" not in loser
    winner = next(r for r in rows if r["permission_to_send"])
    st = ReleaseState.load(str(run[1]))
    assert st.get_step("ccd", "final_reminder").execution["id"] == winner["execution_id"]


def test_only_owner_records_and_success_closes_execution(run, capsys):
    execution_id = reserve(run, capsys)
    before = run[1].read_bytes()
    for token in ([], ["--execution-id", "wrong"]):
        assert invoke(run, capsys, "record-step", "--status", "pass", *token)[0] == 1
        assert run[1].read_bytes() == before
    assert report_result(run, capsys, "wrong")[0] == 1
    assert report_result(run, capsys, execution_id)[0] == 0
    st = ReleaseState.load(str(run[1]))
    assert st.is_done("ccd", "final_reminder")
    assert st.get_step("ccd", "final_reminder").execution is None
    before = run[1].read_bytes()
    assert report_result(run, capsys, execution_id)[0] == 0
    assert run[1].read_bytes() == before
    assert json.loads(invoke(run, capsys, "step-action", "--reserve", "--executor", "B")[1])["kind"] == "done"


def test_interruption_stays_reserved_until_owner_review(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    st.steps["ccd.final_reminder"]["execution"]["started_at"] = "2000-01-01"
    st.save(str(run[1]))
    assert json.loads(invoke(run, capsys, "step-action")[1])["kind"] == "blocked"
    assert report_result(run, capsys, execution_id, "uncertain")[0] == 0
    assert report_result(run, capsys, execution_id)[0] == 1
    assert invoke(run, capsys, "done")[0] == 1
    assert invoke(run, capsys, "reopen")[0] == 1
    assert invoke(run, capsys, "skip", "--reason", "bypass")[0] == 1
    assert report_result(run, capsys, execution_id, "not_sent", review=True)[0] == 0
    # Late completion from the previous execution cannot finish a reopened step.
    assert invoke(run, capsys, "record-step", "--status", "pass", "--execution-id", execution_id)[0] == 1
    assert reserve(run, capsys) != execution_id


def test_owner_can_confirm_interrupted_work_done_without_repeating_it(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    assert orch.complete_step("ccd", "final_reminder").kind == "idle"
    assert orch.complete_step("ccd", "final_reminder", "Owner found message; original runner stopped").kind == "idle"
    assert not st.is_done("ccd", "final_reminder")
    assert orch.step_execution("ccd", "final_reminder")["id"] == execution_id


def test_owner_force_reopen_clears_interrupted_execution(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    st.steps["ccd.final_reminder"]["execution"]["started_at"] = "2000-01-01"
    st.owner_email = "owner@example.test"
    st.save(str(run[1]))
    assert report_result(run, capsys, execution_id, "uncertain")[0] == 0

    assert invoke(run, capsys, "reopen", "--force-owner", "--reason", "owner override")[0] == 1
    rc, text = invoke(run, capsys, "reopen", "--force-owner", "--reason", "owner override",
                      "--prior-writer-stopped")
    assert rc == 0
    assert "New task attempt prepared" in text
    state = ReleaseState.load(str(run[1]))
    record = state.get_step("ccd", "final_reminder")
    assert record.status == "pending"
    assert record.execution is None
    assert state.task_attempts["ccd.final_reminder"]["history"][0]["step"]["execution"]["id"] == execution_id


def test_step_action_owner_force_rerun_unblocks_reserved_step(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    st.steps["ccd.final_reminder"]["execution"]["started_at"] = "2000-01-01"
    st.owner_email = "owner@example.test"
    st.save(str(run[1]))
    assert report_result(run, capsys, execution_id, "uncertain")[0] == 0

    rc, text = invoke(
        run, capsys, "step-action", "--owner-force-rerun", "--owner-force-reason", "owner rerun",
        "--prior-writer-stopped"
    )
    assert rc == 0
    payload = json.loads(text)
    assert payload["owner_forced_rerun"] is True
    assert payload["state_changed"] is True
    state = ReleaseState.load(str(run[1]))
    record = state.get_step("ccd", "final_reminder")
    assert record.execution is None

def test_broker_plan_rerun_accepts_a_new_name_and_preserves_old_binding(run, capsys, monkeypatch):
    state = ReleaseState.load(str(run[1]))
    state.owner_email = "owner@example.test"
    _active_step(state, "bug_bash", "clone_plans_broker")
    state.set_step("bug_bash", "clone_plans_broker", StepState(
        status="done", note="First plan created", data={"plan_id": 100, "plan_name": "First plan"}))
    state.resources["broker_test_plan"] = {"plan_id": 100, "identity": {"name": "First plan"}}
    state.save(str(run[1]))
    monkeypatch.setattr(mocks, "load_mocks", lambda: {"bug_bash.clone_plans_broker": {"clone_id": 200}})
    args = ["--runs-root", str(run[0]), "rerun", "--release", "2000-01",
            "--phase", "bug_bash", "--step", "clone_plans_broker",
            "--reason", "Build another named plan", "--param", "name=Owner's second plan"]

    assert cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "done" and payload["attempt_id"]
    assert payload["progression_policy"] == "automatic_until_input_or_wait"
    assert payload["must_continue_automatic_work"]
    assert any(item["step"] == "clone_plans_auth" for item in payload["work_dispatch"]["pending_work"])
    assert "owner_prompt" not in payload
    state = ReleaseState.load(str(run[1]))
    record = state.get_step("bug_bash", "clone_plans_broker")
    assert record.data["plan_id"] == 200 and record.data["plan_name"] == "Owner's second plan"
    archived = state.task_attempts["bug_bash.clone_plans_broker"]["history"][0]
    assert archived["step"]["data"]["plan_id"] == 100
    assert archived["resources"]["broker_test_plan"]["plan_id"] == 100
    assert not state.is_done("bug_bash", "ui_test_status")
    assert cli.main(["--runs-root", str(run[0]), "task-history", "--release", "2000-01",
                     "--phase", "bug_bash", "--step", "clone_plans_broker"]) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["history"][0]["note"] == "First plan created"
    assert not history["permission_to_execute"]


@pytest.mark.parametrize("command", ("skip", "complete-manually"))
def test_owner_can_record_blocked_future_work_without_sibling_execution(run, capsys, command):
    state = ReleaseState.load(str(run[1]))
    state.owner_email = "owner@example.test"
    state.set_step("monitor", "health_report", StepState(status="blocked", note="No connector"))
    state.save(str(run[1]))
    options = ["--reason", "Owner will manage this outside the tool"] if command == "skip" else [
        "--note", "Owner completed the health review manually",
        "--evidence", "https://example.test/release-review/1"]
    before = copy.deepcopy(state.steps)
    prior_reminder = state.get_step("ccd", "final_reminder")

    assert cli.main(["--runs-root", str(run[0]), command, "--release", "2000-01",
                     "--phase", "monitor", "--step", "health_report", *options,
                     *(["--json"] if command == "skip" else [])]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "done"
    assert output["verification"] == ("waived" if command == "skip" else "owner_attested")
    assert not output["permission_to_execute"]
    state = ReleaseState.load(str(run[1]))
    assert state.get_step("monitor", "health_report").status == ("skipped" if command == "skip" else "done")
    assert state.get_step("ccd", "final_reminder") == prior_reminder
    assert state.task_attempts["monitor.health_report"]["history"][0]["step"]["note"] == "No connector"
    assert not state.gate_decisions


def test_manual_outputs_are_not_promoted_to_provider_receipts(run, capsys):
    state = ReleaseState.load(str(run[1]))
    state.owner_email = "owner@example.test"
    state.save(str(run[1]))
    result = run[0] / "owner-result.json"
    result.write_text(json.dumps({"outputs": {"event_id": "manual-event", "receipt": "owner assertion"}}),
                      encoding="utf-8")
    assert invoke(run, capsys, "complete-manually", "--note", "Owner sent the reminder",
                  "--result-file", str(result))[0] == 0
    state = ReleaseState.load(str(run[1]))
    assert state.get_step("ccd", "final_reminder").data == {}
    assert not state.notification_deliveries
    assert state.task_attempts["ccd.final_reminder"]["result"]["outputs"]["event_id"] == "manual-event"


def test_step_info_advertises_only_valid_public_plan_inputs(capsys):
    assert cli.main(["step-info", "--phase", "bug_bash", "--step", "clone_plans_broker", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)["owner_controls"]
    assert info["rerun"] and info["skip"] and info["complete_manually"]
    assert [field["name"] for field in info["parameters"]] == ["name"]
    assert info["input_role"] == "prepare_effect"

def test_attempt_history_externalizes_large_outputs_and_roundtrips(run, capsys):
    state = ReleaseState.load(str(run[1]))
    state.owner_email = "owner@example.test"
    state.set_step("monitor", "health_report", StepState(
        status="blocked", note="Large prior result", data={"details": "x" * 100_000}))
    state.save(str(run[1]))
    assert cli.main(["--runs-root", str(run[0]), "complete-manually", "--release", "2000-01",
                     "--phase", "monitor", "--step", "health_report", "--note", "Owner reviewed the report"]) == 0
    capsys.readouterr()
    packed = json.loads(run[1].read_text(encoding="utf-8"))
    history = packed["task_attempts"]["monitor.health_report"]["history"]
    assert set(history[0]) == {"evidence_ref"}
    assert run[1].stat().st_size < 32_768
    loaded = ReleaseState.load(str(run[1]))
    original = loaded.task_attempts["monitor.health_report"]["history"][0]["step"]["data"]
    assert original["details"] == "x" * 100_000


def test_reservation_requires_executor_and_preserves_builder_block(run, capsys):
    from orchestrator.outcomes import Blocked
    before = run[1].read_bytes()
    assert invoke(run, capsys, "step-action", "--reserve")[0] == 1
    assert run[1].read_bytes() == before
    st = ReleaseState.load(str(run[1]))
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    blocked = Blocked("Required input missing")
    assert orch.reserve_step("ccd", "final_reminder", blocked, "A") is blocked
    assert not orch.step_execution("ccd", "final_reminder")


@pytest.mark.parametrize("change", ["halted", "blocked", "unsigned", "future", "previous-phase"])
def test_reservation_rechecks_engine_eligibility(run, capsys, change):
    st = ReleaseState.load(str(run[1]))
    if change == "halted":
        st.halt = {"reason": "test", "at": "2026-09-12T00:00:00Z"}
    elif change == "blocked":
        st.readiness_items["yubikey"] = {"status": "unable"}
    elif change == "unsigned":
        st.readiness_items.pop("yubikey", None)
    elif change == "future":
        st.ccd = "2099-01-12"
    else:
        st.steps.pop("preflight.cg")
    st.save(str(run[1]))
    before = run[1].read_bytes()
    rc, text = invoke(run, capsys, "step-action", "--reserve", "--executor", "A")
    assert rc == 0 and json.loads(text)["kind"] == "blocked"
    assert run[1].read_bytes() == before


def test_reservation_is_generic_for_every_outbound_action():
    simple = NeedsSkill(tool="workiq_send_email", payload={}, outbound=True)
    assert Orchestrator.supports_step_reservation(simple)
    for payload in ({"followup_command": "record-telemetry"}, {"_trigger": {"after": "poll"}},
                    {"followup_command": ""}, {"_trigger": {}}):
        assert Orchestrator.supports_step_reservation(
            NeedsSkill(tool="workiq_send_email", payload=payload, outbound=True))
    assert Orchestrator.supports_step_reservation(
        NeedsSkill(tool="create-payload-wiki", payload={}, outbound=True))


def test_parallel_phase_waits_on_reserved_step_without_running_it(run):
    st = ReleaseState.load(str(run[1]))
    st.steps.pop("preflight.notice")
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    action = NeedsSkill(tool="workiq_send_email", payload={}, outbound=True, record_as="notice")
    assert orch.reserve_step("preflight", "notice", action, "A").kind == "needs_skill"
    result = orch.run_until_gate()[-1]
    assert result.kind == "waiting" and result.step == "notice"
    assert orch.scout_pending_steps() == []


@pytest.mark.parametrize("sid", ["final_reminder", "pr_reminder"])
def test_direct_engine_reservation_enforces_ownership(run, sid):
    st = ReleaseState.load(str(run[1]))
    if sid == "pr_reminder":
        st.set_step("ccd", "final_reminder", StepState(status="done"))
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    action = NeedsSkill(tool="workiq_send_email", payload={}, outbound=True, record_as=sid)
    assert orch.reserve_step("ccd", sid, action, "A").kind == "needs_skill"
    before = copy.deepcopy(st.steps)
    assert orch.reserve_step("ccd", sid, action, "B").kind == "blocked"
    with pytest.raises(ValueError, match="owning execution"):
        orch.record_scout_step("ccd", sid, "pass")
    with pytest.raises(ValueError, match="owning execution"):
        orch.record_scout_step("ccd", sid, "pass", refresh=True)
    assert st.steps == before
    assert sid not in orch.scout_pending_steps()


@pytest.mark.parametrize("renderer", ["status_view", "notification", "notification_markdown", "notification_html"])
def test_interrupted_execution_never_instructs_an_automatic_repeat(run, capsys, renderer):
    from orchestrator import render
    execution_id = reserve(run, capsys)
    invoke(run, capsys, "record-step", "--status", "attention", "--execution-id", execution_id)
    st = ReleaseState.load(str(run[1]))
    for sid in ("pr_reminder", "localization"):
        st.set_step("ccd", sid, StepState(status="done"))
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    text = getattr(render, renderer)(orch.status_report())
    assert "Do not repeat" in text
    assert "do it, then mark" not in text and "Mark it done when complete" not in text


def test_old_live_lock_cannot_be_stolen(tmp_path):
    with C.state_lock(str(tmp_path), "R"):
        os.utime(tmp_path / "R" / ".state.lock", (0, 0))
        code = (
            "from orchestrator import cli_common as C\nimport sys\nC._LOCK_TIMEOUT=0.2\n"
            "try:\n    with C.state_lock(sys.argv[1], 'R'): raise AssertionError('stolen')\n"
            "except TimeoutError: print('blocked')\n")
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and result.stdout.strip() == "blocked", result.stderr
    assert (tmp_path / "R" / ".state.lock").exists()


def test_process_exit_releases_os_lock(tmp_path):
    code = ("from orchestrator import cli_common as C\nimport os, sys\n"
            "with C.state_lock(sys.argv[1], 'R'): os._exit(7)\n")
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT,
                            capture_output=True, timeout=10)
    assert result.returncode == 7
    with C.state_lock(str(tmp_path), "R"):
        pass
