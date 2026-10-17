"""Human confirmations and final gates bind to the release evidence actually reviewed."""
from copy import deepcopy
import json

import pytest

from orchestrator.state import ReleaseState, StepState
from orchestrator import cli, cli_common as C, mocks, delivery as D
from orchestrator.commands.delivery_cmd import finish
from steps.bug_bash import native_auth_signoff as N, notify_native_auth as Notice
from tests._context import invoke, fresh_orchestrator as Orchestrator
from tests._harness import CONFIG, _active_step


def _ready(step=N.ID):
    state = ReleaseState(release_id="2000-01", ccd="2000-01-12",
                         owner_email="owner@example.test")
    _active_step(state, "bug_bash", step)
    state.set_step("bug_bash", "clone_plans_broker", StepState(
        status="done", data={"plan_id": 900, "ui_suite_id": 901}))
    state.set_step("bug_bash", "notify_native_auth", StepState())
    orch = Orchestrator(CONFIG, state, mocks={})
    notice = orch.handler("bug_bash", Notice.ID).build(orch.context(
        "bug_bash", Notice.ID, parameters={"engineer": "engineer@example.test",
                                        "engineer_source": "synthetic verified directory"}))
    item = D.descriptor(state, "human-contract:native", {
        "kind": "step", "phase": "bug_bash", "step": Notice.ID,
        "release_matches": {"ccd": state.ccd, "owner_email": state.owner_email},
        "state_matches": notice.notification["state_matches"],
    }, notice.tool, notice.payload, {
        "kind": "step", "record_as": Notice.ID, **notice.notification["completion"]})
    if step == N.ID:
        D.offer(orch, item)
        claim = D.claim(orch, item["id"], item["hash"], "synthetic-notice-worker")
        D.result(orch, item["id"], claim["execution_id"], "sent",
                 "Synthetic direct provider acknowledgement", {"id": "synthetic-message"})
        assert finish(orch, item["id"])
    else:
        state.set_step("bug_bash", Notice.ID, StepState(status="done"))
    return state, orch


@pytest.mark.parametrize("damage", ["notice", "engineer", "plan"])
def test_done_rechecks_actual_signoff_prompt_before_recording_human_success(damage):
    state, orch = _ready()
    if damage == "notice":
        state.set_step("bug_bash", "notify_native_auth", StepState(status="done"))
    elif damage == "engineer":
        state.set_step("bug_bash", "notify_native_auth", StepState(
            status="done", data={"engineer": ["invalid"]}))
    else:
        state.set_step("bug_bash", "clone_plans_broker", StepState(status="done"))
    decisions = deepcopy(state.gate_decisions)
    action = orch.complete_step("bug_bash", N.ID, "Owner confirmed")
    assert not state.is_done("bug_bash", N.ID)
    assert state.gate_decisions == decisions
    assert "sign-off needs" in action.message


def test_changed_signoff_source_requires_fresh_confirmation():
    state, orch = _ready()
    prompt = invoke(N.build, state)
    assert prompt.kind == "needs_human"
    state.set_step("bug_bash", "notify_native_auth", StepState(
        status="done", data={"engineer": "replacement@example.test"}))
    decisions = deepcopy(state.gate_decisions)
    message = orch.complete_step("bug_bash", N.ID, "Old sign-off").message
    assert "changed" in message or "sign-off needs" in message
    assert not state.is_done("bug_bash", N.ID)
    assert state.gate_decisions == decisions


def test_pending_signoff_cannot_silently_replace_its_acknowledged_delivery():
    state, orch = _ready()
    assert invoke(N.build, state).kind == "needs_human"
    reviewed = deepcopy(state.get_step("bug_bash", N.ID).data)
    notice = state.get_step("bug_bash", Notice.ID)
    receipt = state.notification_deliveries[notice.data["notification_id"]]
    receipt["attempts"][-1]["id"] = "replacement-notice-execution"
    notice.data["notification_execution_id"] = "replacement-notice-execution"
    state.set_step("bug_bash", Notice.ID, notice)
    assert Notice.notified_engineer(orch.context("bug_bash", Notice.ID)) == "engineer@example.test"

    action = orch.complete_step("bug_bash", N.ID, "Confirmation for the original notice")

    assert action.kind == "blocked" and "changed" in action.message
    assert not state.is_done("bug_bash", N.ID)
    assert state.get_step("bug_bash", N.ID).data == reviewed

    assert orch.reopen("bug_bash", N.ID, "Review the replacement notice explicitly").kind == "ran"
    assert orch.complete_step("bug_bash", N.ID, "Confirmed the current notice").kind == "ran"
    assert state.get_step("bug_bash", N.ID).data["attestation_binding"] != reviewed["attestation_binding"]


def test_pending_signoff_ignores_cosmetic_delivery_updates():
    state, orch = _ready()
    assert invoke(N.build, state).kind == "needs_human"
    reviewed = deepcopy(state.get_step("bug_bash", N.ID).data)
    notice = state.get_step("bug_bash", Notice.ID)
    notice.note = "Delivery details refreshed for display"
    state.set_step("bug_bash", Notice.ID, notice)
    receipt = state.notification_deliveries[notice.data["notification_id"]]
    receipt["attempts"][-1]["acknowledged_at"] = "2026-10-17T05:00:00Z"
    assert invoke(N.build, state).kind == "needs_human"
    assert state.get_step("bug_bash", N.ID).data == reviewed


def test_human_confirmation_is_retained_with_its_source_binding_and_changes_block_gate(tmp_path):
    state, orch = _ready()
    assert orch.complete_step("bug_bash", N.ID, "Owner received release-specific sign-off").kind == "ran"
    data = state.get_step("bug_bash", N.ID).data
    assert data["attestation_binding"] and data["gate_preconditions"]
    path = tmp_path / "release-state.json"
    state.save(path)
    state = ReleaseState.load(path)
    orch = Orchestrator(CONFIG, state, mocks={})
    assert orch.status_report()["current_step"] == "bugbash_complete"
    state.set_step("bug_bash", "notify_native_auth", StepState(
        status="done", data={"engineer": "replacement@example.test"}))
    decisions = deepcopy(state.gate_decisions)
    action = orch.approve_gate("Sign-off was for a different engineer")
    assert action.kind != "ran" and "precondition" in action.message
    assert state.gate_decisions == decisions
    assert not state.is_done("bug_bash", "bugbash_complete")


def test_final_gate_can_approve_current_sources_but_never_without_owner_action():
    state, orch = _ready()
    assert orch.complete_step("bug_bash", N.ID, "Owner received sign-off").kind == "ran"
    before = deepcopy(state.gate_decisions)
    assert orch.step_once().kind == "gate"
    assert state.gate_decisions == before
    assert orch.approve_gate("Owner reviewed this release and approved completion").kind == "ran"
    assert state.is_done("bug_bash", "bugbash_complete")


def test_ui_failure_human_done_cannot_ignore_stale_or_malformed_producer_conditions():
    state, orch = _ready("ui_failures")
    record = state.get_step("bug_bash", "ui_failures")
    record.data["gate_preconditions"] = [
        {"path": ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"], "value": 901}]
    state.set_step("bug_bash", "ui_failures", record)
    action = orch.complete_step("bug_bash", "ui_failures", "Owner reviewed old plan")
    assert action.kind != "ran" and "Completion precondition" in action.message
    assert not state.is_done("bug_bash", "ui_failures")


def test_missing_native_signoff_evidence_is_a_normal_blocked_prompt(tmp_path, monkeypatch, capsys):
    state, _ = _ready()
    state.set_step("bug_bash", "notify_native_auth", StepState(status="done"))
    C.save_state(state, str(tmp_path), state.release_id)
    before = (tmp_path / state.release_id / "release-state.json").read_bytes()
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    assert cli.main(["--runs-root", str(tmp_path), "step-action", "--release", state.release_id,
                     "--phase", "bug_bash", "--step", N.ID]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["kind"] == "blocked" and "sign-off needs" in result["reason"]
    assert (tmp_path / state.release_id / "release-state.json").read_bytes() == before


def test_done_cli_does_not_log_or_save_success_for_blocked_signoff(tmp_path, monkeypatch, capsys):
    state, _ = _ready()
    state.set_step("bug_bash", Notice.ID, StepState(status="done"))
    C.save_state(state, str(tmp_path), state.release_id)
    path = tmp_path / state.release_id / "release-state.json"
    before = path.read_bytes()
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    assert cli.main(["--runs-root", str(tmp_path), "done", "--release", state.release_id,
                     "--phase", "bug_bash", "--step", N.ID, "--note", "Owner confirmed"]) == 1
    assert "sign-off needs" in capsys.readouterr().out
    assert path.read_bytes() == before
    events = path.parent / "events.jsonl"
    assert not events.exists() or "reminder_done" not in events.read_text(encoding="utf-8")


def test_producer_refresh_cannot_rewrite_what_a_human_already_confirmed():
    state, orch = _ready("ui_failures")
    record = state.get_step("bug_bash", "ui_failures")
    path = ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"]
    record.data["gate_preconditions"] = [{"path": path, "value": 900}]
    state.set_step("bug_bash", "ui_failures", record)
    assert orch.complete_step("bug_bash", "ui_failures", "Reviewed plan900").kind == "ran"
    reviewed = state.get_step("bug_bash", "ui_failures")
    assert reviewed.data["confirmation_preconditions"] == [{"path": path, "value": 900}]
    state.steps["bug_bash.clone_plans_broker"]["data"]["plan_id"] = 902
    reviewed.data["gate_preconditions"] = [{"path": path, "value": 902}]
    state.set_step("bug_bash", "ui_failures", reviewed)
    # A later human completion also checks the frozen confirmation on prior work.
    state.set_step("bug_bash", "native_auth_signoff", StepState(status="done"))
    assert orch.approve_gate("Old review cannot cover new plan").kind != "ran"
    assert not state.is_done("bug_bash", "bugbash_complete")


def test_legacy_human_done_without_a_source_confirmation_cannot_cover_new_generated_facts():
    state, orch = _ready("ui_failures")
    state.set_step("bug_bash", "ui_failures", StepState(status="done", data={
        "gate_preconditions": [
            {"path": ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"], "value": 900}],
    }))
    state.set_step("bug_bash", N.ID, StepState(status="done"))
    action = orch.approve_gate("Legacy status is not a review of new evidence")
    assert action.kind != "ran" and "confirmation source missing" in action.message
    assert not state.is_done("bug_bash", "bugbash_complete")


def test_future_human_gate_and_scout_steps_are_pending_not_automatic_or_ready_for_approval():
    state = ReleaseState(release_id="2000-01", ccd="2000-01-12")
    _active_step(state, "bug_bash", "clone_plans_broker")
    report = Orchestrator(CONFIG, state, mocks={}).status_report()
    steps = {step["id"]: step for step in report["current_steps"]}
    phase_steps = {step["id"]: step for step in report["active_phase"]["steps"]}
    for step in ("send_invite", "activate_chat", "notify_native_auth", "ui_failures",
                 "native_auth_signoff", "bugbash_complete"):
        assert steps[step]["state"] == phase_steps[step]["status"] == "pending"
        assert not phase_steps[step]["needs_owner"]
    assert not report["pending_human"]


@pytest.mark.parametrize("damage", ["status", "completion", "missing"])
def test_final_gate_rechecks_acknowledged_native_notice_after_human_signoff(damage):
    state, orch = _ready()
    assert orch.complete_step("bug_bash", N.ID, "Owner received sign-off").kind == "ran"
    notice = state.get_step("bug_bash", Notice.ID)
    record = state.notification_deliveries[notice.data["notification_id"]]
    if damage == "status":
        record["status"] = "uncertain"
        # Keep the corrupted ledger structurally valid for this source-binding probe.
        record["attempts"][-1]["status"] = "uncertain"
    elif damage == "completion":
        record["completion"]["status"] = "suppressed"
    else:
        del state.notification_deliveries[notice.data["notification_id"]]
    assert orch.approve_gate("Old confirmation cannot cover a broken delivery").kind != "ran"
    assert not state.is_done("bug_bash", "bugbash_complete")


def test_real_ui_reminder_conditions_preserve_raw_id_types_and_survive_identical_rerun():
    from tests._mrwp_evidence import current_rc
    from tests._ui_results import publish

    state, orch = _ready("ui_failures")
    state.pipeline_runs = {"rcs": [current_rc()]}
    state.steps["bug_bash.clone_plans_broker"]["data"]["plan_id"] = "900"
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": "902"}))
    publish(state)
    assert orch.complete_step("bug_bash", "ui_failures", "Reviewed current frozen UI evidence").kind == "ran"
    before = deepcopy(state.get_step("bug_bash", "ui_failures").data["confirmation_preconditions"])
    publish(state)
    assert state.get_step("bug_bash", "ui_failures").data["confirmation_preconditions"] == before
    gate = orch.workflow.step("bug_bash", "bugbash_complete")
    assert not orch._transition_kernel()._gate_precondition_problem(gate)


def test_changed_failure_facts_cannot_reuse_completed_review_with_the_same_run_ids():
    from tests._mrwp_evidence import current_rc, PROD
    from tests._ui_results import publish

    state, orch = _ready("ui_failures")
    state.pipeline_runs = {"rcs": [current_rc()]}
    state.set_step("bug_bash", "clone_plans_auth", StepState(
        status="done", data={"suite_id": 902}))
    publish(state)
    assert orch.complete_step("bug_bash", "ui_failures", "Reviewed original failures").kind == "ran"
    reviewed = deepcopy(state.get_step("bug_bash", "ui_failures"))
    state.pipeline_runs["rcs"][-1] = current_rc(
        ecs={PROD: [("test_100_Default", "Failed")]})

    publish(state)

    refreshed = state.get_step("bug_bash", "ui_failures")
    assert refreshed.status == reviewed.status == "done"
    assert refreshed.completed_at == reviewed.completed_at
    assert "Reviewed original failures" in refreshed.note
    assert refreshed.data["confirmation_preconditions"] == reviewed.data["confirmation_preconditions"]
    assert refreshed.data["gate_preconditions"] != reviewed.data["confirmation_preconditions"]
    gate = orch.workflow.step("bug_bash", "bugbash_complete")
    assert "source changed" in orch._transition_kernel()._gate_precondition_problem(gate)

    assert orch.reopen("bug_bash", "ui_failures", "Review the changed failures").kind == "ran"
    assert orch.complete_step("bug_bash", "ui_failures", "Reviewed refreshed failures").kind == "ran"
    confirmed = state.get_step("bug_bash", "ui_failures")
    assert confirmed.data["confirmation_preconditions"] == refreshed.data["gate_preconditions"]
    assert confirmed.note == "Reviewed refreshed failures"
    assert not orch._transition_kernel()._gate_precondition_problem(gate)
