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

from orchestrator import cli, cli_common as C, mocks
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import NeedsSkill
from orchestrator.state import ReleaseState, StepState

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    monkeypatch.setenv("RELEASE_AGENT_MOCKS", str(tmp_path / "absent.yaml"))
    st = ReleaseState(release_id="2000-01", ccd="2000-01-12", readiness_signed=True,
                      current_phase="ccd", current_step="final_reminder", timezone="UTC")
    cfg = yaml.safe_load(Path(C.DEFAULT_CONFIG).read_text(encoding="utf-8"))
    for step in cfg["phases"][0]["steps"]:
        st.set_step("preflight", step["id"], StepState(status="done"))
    path = tmp_path / st.release_id / "release-state.json"
    st.save(str(path))
    return tmp_path, path


def invoke(run, capsys, command, *options):
    rc = cli.main(["--runs-root", str(run[0]), command, "--release", "2000-01",
                   "--phase", "ccd", "--step", "final_reminder", *options])
    return rc, capsys.readouterr().out


def reserve(run, capsys):
    rc, text = invoke(run, capsys, "step-action", "--reserve", "--executor", "worker-A")
    out = json.loads(text)
    assert rc == 0 and out["kind"] == "needs_skill" and out["reservable"]
    return out["execution_id"]


def test_two_workers_only_one_receives_reserved_work(run):
    args = [sys.executable, "-m", "orchestrator.cli", "--runs-root", str(run[0]),
            "step-action", "--release", "2000-01", "--phase", "ccd",
            "--step", "final_reminder", "--reserve", "--executor"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(subprocess.run, args + [owner], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
                for owner in ("A", "B")]
        results = [job.result() for job in jobs]
    assert all(r.returncode == 0 for r in results), [(r.stdout, r.stderr) for r in results]
    rows = [json.loads(r.stdout) for r in results]
    assert sorted(r["kind"] for r in rows) == ["blocked", "needs_skill"]
    loser = next(r for r in rows if r["kind"] == "blocked")
    assert "payload" not in loser and "tool" not in loser
    winner = next(r for r in rows if r["kind"] == "needs_skill")
    st = ReleaseState.load(str(run[1]))
    assert st.get_step("ccd", "final_reminder").data["_execution"]["id"] == winner["execution_id"]


def test_only_owner_records_and_success_preserves_execution(run, capsys):
    execution_id = reserve(run, capsys)
    before = run[1].read_bytes()
    for token in ([], ["--execution-id", "wrong"]):
        assert invoke(run, capsys, "record-step", "--status", "pass", *token)[0] == 1
        assert run[1].read_bytes() == before
    assert invoke(run, capsys, "record-step", "--status", "pass",
                  "--execution-id", execution_id, "--detail", "Provider success: message-1")[0] == 0
    st = ReleaseState.load(str(run[1]))
    assert st.is_done("ccd", "final_reminder")
    assert st.get_step("ccd", "final_reminder").data["_execution"]["id"] == execution_id
    before = run[1].read_bytes()
    assert invoke(run, capsys, "record-step", "--status", "pass", "--execution-id", execution_id)[0] == 0
    assert run[1].read_bytes() == before
    assert json.loads(invoke(run, capsys, "step-action", "--reserve", "--executor", "B")[1])["kind"] == "done"


def test_interruption_stays_reserved_until_owner_review(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    st.steps["ccd.final_reminder"]["data"]["_execution"]["started_at"] = "2000-01-01"
    st.save(str(run[1]))
    assert json.loads(invoke(run, capsys, "step-action")[1])["kind"] == "blocked"
    assert invoke(run, capsys, "record-step", "--status", "attention",
                  "--execution-id", execution_id, "--detail", "Timeout; outcome unknown")[0] == 0
    assert invoke(run, capsys, "record-step", "--status", "pass", "--execution-id", execution_id)[0] == 1
    assert invoke(run, capsys, "done")[0] == 1
    assert invoke(run, capsys, "reopen")[0] == 1
    assert invoke(run, capsys, "skip", "--reason", "bypass")[0] == 1
    assert invoke(run, capsys, "reopen", "--reason", "Owner confirmed no action occurred; old runner stopped")[0] == 0
    # Late completion from the previous execution cannot finish a reopened step.
    assert invoke(run, capsys, "record-step", "--status", "pass", "--execution-id", execution_id)[0] == 1
    assert reserve(run, capsys) != execution_id


def test_owner_can_confirm_interrupted_work_done_without_repeating_it(run, capsys):
    execution_id = reserve(run, capsys)
    st = ReleaseState.load(str(run[1]))
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    assert orch.complete_step("ccd", "final_reminder").kind == "idle"
    assert orch.complete_step("ccd", "final_reminder", "Owner found message; original runner stopped").kind == "ran"
    assert st.is_done("ccd", "final_reminder")
    assert orch.step_execution("ccd", "final_reminder")["id"] == execution_id


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
    if change in ("halted", "blocked"):
        setattr(st, change, True)
    elif change == "unsigned":
        st.readiness_signed = False
    elif change == "future":
        st.ccd = "2099-01-12"
    else:
        st.steps.pop("preflight.cg")
    st.save(str(run[1]))
    before = run[1].read_bytes()
    rc, text = invoke(run, capsys, "step-action", "--reserve", "--executor", "A")
    assert rc == 0 and json.loads(text)["kind"] == "blocked"
    assert run[1].read_bytes() == before


def test_reservation_is_generic_and_specialized_followups_are_unchanged():
    simple = NeedsSkill(tool="workiq_send_email", payload={}, outbound=True)
    assert Orchestrator.supports_step_reservation(simple)
    for payload in ({"followup_command": "record-telemetry"}, {"_trigger": {"after": "poll"}},
                    {"followup_command": ""}, {"_trigger": {}}):
        assert not Orchestrator.supports_step_reservation(
            NeedsSkill(tool="workiq_send_email", payload=payload, outbound=True))
    assert not Orchestrator.supports_step_reservation(
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
    orch = Orchestrator(C.DEFAULT_CONFIG, st, mocks={})
    action = NeedsSkill(tool="workiq_send_email", payload={}, outbound=True, record_as=sid)
    assert orch.reserve_step("ccd", sid, action, "A").kind == "needs_skill"
    before = copy.deepcopy(st.steps)
    assert orch.reserve_step("ccd", sid, action, "B").kind == "blocked"
    with pytest.raises(ValueError, match="owning execution"):
        orch.record_scout_step("ccd", sid, "pass")
    with pytest.raises(ValueError, match="refresh"):
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
