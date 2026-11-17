# Copyright (c) Microsoft Corporation. All rights reserved.
"""RC summary notification (direct from captured evidence), 2-step contract, and human gate."""
from argparse import Namespace
from copy import deepcopy
from datetime import date
import json

import pytest

from orchestrator import cli, cli_common as C, delivery as D, mocks, schedule
from orchestrator.commands.delivery_cmd import finish
from orchestrator.commands.step_action import prepare_step
from orchestrator.state import ReleaseState, StepState
from tests._context import context, fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _ready_for_rc_report, _seed_rc_pipeline
from steps.build_verify import rc_report as R, rc_report_notify as N


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


def test_rc_report_notify_builds_directly_without_sharepoint_publish(ready):
    st, orch = ready
    assert orch.workflow.step("build_verify", "rc_report_publish") is None
    args = Namespace(phase="build_verify", step="rc_report_notify", release=st.release_id)
    out = prepare_step(args, st, orch)
    assert out["tool"] == "m_send_teams_message"
    assert "permission_to_send" not in out
    assert out["ready_to_execute"] is True
    assert "MRWP ECS:" in out["mcp_arguments"]["message"]
    assert "MRWP Local:" in out["mcp_arguments"]["message"]
    assert out["execution_protocol"]["contract"] == "python_direct_delivery"
    assert "deliver-step" in out["execute_command"]
    assert "record-step" in out["complete_command"]


def test_two_step_contract_completes_rc_notify_and_unlocks_human_gate(ready, tmp_path, capsys):
    st, orch = ready
    C.save_state(st, str(tmp_path), st.release_id)

    assert cli.main([
        "--runs-root", str(tmp_path),
        "step-action", "--release", st.release_id,
        "--phase", "build_verify", "--step", "rc_report_notify",
    ]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["tool"] == "m_send_teams_message"
    assert action["mcp_arguments"]["message"]

    assert cli.main([
        "--runs-root", str(tmp_path),
        "record-step", "--release", st.release_id,
        "--phase", "build_verify", "--step", "rc_report_notify",
        "--status", "pass", "--detail", "Sent RC verification summary to Scout bot",
    ]) == 0
    capsys.readouterr()

    saved = C.load_state(str(tmp_path), st.release_id)
    assert saved.is_done("build_verify", "rc_report_notify")
    assert saved.get_step("build_verify", "rc_report_notify").data.get("report_lineage")
    assert not saved.is_done("build_verify", "rc_report_gate")

    saved_orch = Orchestrator(CONFIG, saved, mocks={}, as_of=date(2026, 9, 11))
    assert saved_orch.approve_gate("Owner reviewed RC summary; proceed to Bug Bash").kind == "ran"
    assert saved.is_done("build_verify", "rc_report_gate")
    assert saved_orch.current_phase_id() == "bug_bash"


def test_changed_source_after_notify_blocks_stale_gate_approval(ready, tmp_path, capsys):
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    assert cli.main([
        "--runs-root", str(tmp_path),
        "record-step", "--release", st.release_id,
        "--phase", "build_verify", "--step", "rc_report_notify",
        "--status", "pass", "--detail", "Sent RC summary",
    ]) == 0
    capsys.readouterr()

    saved = C.load_state(str(tmp_path), st.release_id)
    _seed_rc_pipeline(saved, {"total": 100, "passed": 50, "failed": 50},
                      {"total": 100, "passed": 50, "failed": 50})
    C.save_state(saved, str(tmp_path), saved.release_id)

    assert cli.main([
        "--runs-root", str(tmp_path), "approve", "--release", saved.release_id,
        "--comment", "Reviewed the earlier clean report",
    ]) == 1
    assert "Gate precondition source changed" in capsys.readouterr().out
    after = C.load_state(str(tmp_path), saved.release_id)
    assert not after.is_done("build_verify", "rc_report_gate")


def test_fail_step_attaches_actionable_remediation_and_escape_hatches(ready, tmp_path, capsys):
    st, _ = ready
    C.save_state(st, str(tmp_path), st.release_id)
    assert cli.main([
        "--runs-root", str(tmp_path),
        "fail-step", "--release", st.release_id,
        "--phase", "build_verify", "--step", "rc_report_notify",
        "--tool", "workiq", "--reason", "M365 token expired",
    ]) == 0
    capsys.readouterr()

    saved = C.load_state(str(tmp_path), st.release_id)
    step = saved.get_step("build_verify", "rc_report_notify")
    assert step.status == "blocked"
    rem = step.data["remediation"]
    assert "WorkIQ" in rem["system"]
    assert "complete-manually" in rem["complete_manually_command"]
    assert "skip" in rem["skip_command"]

    # 1-click manual escape hatch works directly without --prior-writer-stopped
    assert cli.main([
        "--runs-root", str(tmp_path),
        "complete-manually", "--release", st.release_id,
        "--phase", "build_verify", "--step", "rc_report_notify",
        "--note", "Reviewed pipeline runs directly in ADO",
    ]) == 0
    capsys.readouterr()
    assert C.load_state(str(tmp_path), st.release_id).is_done("build_verify", "rc_report_notify")
