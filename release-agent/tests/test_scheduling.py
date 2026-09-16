"""Scheduling parity across dispatch, projections, status and intent eligibility."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from tests._context import fresh_orchestrator as Orchestrator, adopt_test_revision
from orchestrator.outcomes import Done, InProgress
from orchestrator.state import ReleaseState, StepState
from orchestrator.transitions import EligibilityEvaluator, TransitionIntent


def _step(sid, kind="auto", **kwargs):
    return {
        "id": sid, "name": sid.title(), "kind": kind,
        **({"effect_mode": "read_only"} if kind == "auto" else {}), **kwargs,
    }


def _engine(tmp_path, steps, *, execution="parallel", anchor=None, fire=None, mocks=None):
    calls, checkpoints = [], []
    control = {"outcome": Done("finished"), "input": "original"}
    modules = {}
    for step in steps:
        sid, kind = step["id"], step["kind"]
        if kind not in ("auto", "external"):
            continue

        def build(_state, sid=sid):
            calls.append((sid, "build"))
            return control["outcome"]

        module = SimpleNamespace(
            ID=sid, KIND="agent" if kind == "auto" else "scout", build=build,
            CONFIG={"fire_at_local": fire[sid]} if fire and sid in fire else {},
        )
        if kind == "auto":
            module.EFFECT_MODE = step.get("effect_mode", "read_only")
            if module.EFFECT_MODE != "read_only":
                module.EFFECT_RECOVERY = step.get("effect_recovery", "frozen")

                def prepare(_state, sid=sid):
                    calls.append((sid, "prepare"))
                    return {"target": control["input"]}

                def execute(context, sid=sid):
                    owned = context.effect.execution
                    assert checkpoints
                    assert state.get_step("phase", sid).execution == owned
                    calls.append((sid, "execute"))
                    return control["outcome"]

                def reconcile(context, sid=sid):
                    owned = context.effect.execution
                    assert state.get_step("phase", sid).execution == owned
                    calls.append((sid, "reconcile"))
                    return control["outcome"]

                module.prepare_effect = prepare
                module.execute = execute
                module.reconcile = reconcile
        modules[sid] = module
    config = {"phases": [{
        "id": "phase", "name": "Phase", "execution": execution,
        **({"anchor": anchor} if anchor else {}), "steps": steps,
    }]}
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state = ReleaseState(release_id="r", timezone="UTC", ccd="2026-09-12")
    state._checkpoint = lambda: checkpoints.append(deepcopy(state.steps))
    orch = Orchestrator(
        str(path), state, mocks=mocks or {},
        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        handler_resolver=lambda _phase, sid: modules.get(sid),
    )
    return orch, calls, control, checkpoints


def _eligible(orch, intent, sid):
    return EligibilityEvaluator(
        orch.state, orch.workflow, orch._projection(),
    ).evaluate(intent, "phase", sid)


def _parity(orch):
    before = deepcopy(orch.state.steps), deepcopy(orch.state.gate_decisions)
    selection = orch.scheduling()
    projection = orch._projection()
    assert projection.frontier_phase() == selection.frontier
    assert projection.current_hold() == selection.focus_hold
    assert projection.pending_actions() == selection.action_holds
    assert projection.pending_human() == selection.pending_human
    assert projection.release_status() == selection.status
    report = orch.status_report()
    assert report["status"] == selection.status
    assert report["current_step"] == (
        selection.focus_hold.step_id if selection.focus_hold else None
    )
    assert report["pending_human"] == list(selection.pending_human)
    assert report["scout_pending"] == orch.scout_pending_steps() == list(selection.scout_pending)
    assert (orch.state.steps, orch.state.gate_decisions) == before
    return selection


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("status", ["pending", "running", "blocked", "in_flight"])
def test_auto_readiness_is_shared_but_hold_focus_is_not_a_dispatch_veto(tmp_path, execution, status):
    orch, calls, _, _ = _engine(
        tmp_path, [_step("observe"), _step("sibling"), _step("human", "human_action")],
        execution=execution,
    )
    orch.state.set_step("phase", "observe", StepState(status=status, note="observation"))
    selection = _parity(orch)
    assert [candidate.step.id for candidate in selection.runnable] == (
        ["observe", "sibling"] if execution == "parallel" else ["observe"]
    )
    assert _eligible(orch, TransitionIntent.EXECUTE, "observe").allowed
    assert _eligible(orch, TransitionIntent.EXECUTE, "sibling").allowed == (execution == "parallel")
    attempted = set()
    action = orch.step_once(attempted)
    assert action.step == selection.runnable[0].step.id
    assert calls == [("observe", "build")]
    assert attempted == {"phase.observe"}
    assert selection.step("phase", "observe").status == status
    with pytest.raises(FrozenInstanceError):
        selection.status = "changed"
    with pytest.raises(FrozenInstanceError):
        selection.steps[0].status = "changed"
    with pytest.raises(TypeError):
        selection.steps[0].definition.raw["name"] = "changed"


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
def test_pending_gate_focus_does_not_block_independent_work_but_denial_does(tmp_path, execution):
    orch, calls, _, _ = _engine(tmp_path, [
        _step("gate", "approval_gate"), _step("auto"),
        _step("external", "external"), _step("other_gate", "approval_gate"),
    ], execution=execution)
    selection = _parity(orch)
    assert selection.status == "holding_gate"
    assert selection.focus_hold.step_id == "gate"
    assert bool(selection.runnable) == (execution == "parallel")
    assert _eligible(orch, TransitionIntent.PREPARE, "external").allowed == (execution == "parallel")
    action = orch.step_once()
    assert (action.kind, action.step) == (
        ("ran", "auto") if execution == "parallel" else ("gate", "gate")
    )
    calls.clear()
    orch.state.gate_decisions.append({
        "step": "phase.other_gate", "decision": "denied", "comment": "no approval",
    })
    selection = _parity(orch)
    assert selection.suspension == "denied" and selection.status == "blocked"
    assert selection.focus_hold.step_id == "other_gate"
    assert selection.runnable == selection.action_holds == ()
    assert not _eligible(orch, TransitionIntent.PREPARE, "external").allowed
    assert orch.step_once().kind == "blocked"
    assert not calls


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("mocked", [False, True])
@pytest.mark.parametrize("kind", ["auto", "external"])
def test_timed_new_work_including_mocks_uses_one_readiness_boundary(
        tmp_path, execution, mocked, kind):
    orch, calls, _, _ = _engine(
        tmp_path, [_step("timed", kind)], execution=execution, anchor="CCD",
        fire={"timed": "13:00"},
        mocks={"phase.timed": {"outcome": "done"}} if mocked else {},
    )
    intent = TransitionIntent.EXECUTE if kind == "auto" else TransitionIntent.PREPARE
    selection = _parity(orch)
    assert selection.phase("phase").due and not selection.step("phase", "timed").time_ready
    assert selection.status == "scheduled" and not selection.runnable
    assert not selection.action_holds
    assert not _eligible(orch, intent, "timed").allowed
    assert orch.status_report()["current_steps"][0]["state"] == "scheduled"
    assert orch.step_once().kind == "scheduled" and not calls

    orch.now_local = orch.now_local.replace(hour=13)
    selection = _parity(orch)
    assert selection.step("phase", "timed").time_ready
    assert _eligible(orch, intent, "timed").allowed
    if kind == "auto" or mocked:
        assert selection.runnable[0].step.id == "timed"
        assert orch.step_once().kind == "ran"
        assert calls == ([] if mocked else [("timed", "build")])
        assert not selection.scout_pending
    else:
        assert selection.scout_pending == ("timed",)
        assert orch.step_once().kind == "reminder" and not calls


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("suspension", ["halted", "cancelled", "readiness_gate", "blocked", "denied"])
def test_global_suspension_suppresses_actions_dispatch_and_recovery(tmp_path, execution, suspension):
    orch, calls, control, _ = _engine(tmp_path, [
        _step("owned", effect_mode="transactional", effect_recovery="frozen"),
        _step("external", "external"), _step("gate", "approval_gate"),
    ], execution=execution)
    control["outcome"] = InProgress("still running")
    orch.step_once()
    owned = deepcopy(orch.state.get_step("phase", "owned"))
    calls.clear()
    if suspension == "halted":
        orch.state.halt = {"reason": "incident"}
    elif suspension == "cancelled":
        orch.state.cancellation = {"reason": "cancel"}
    elif suspension == "denied":
        orch.state.gate_decisions = [{"step": "phase.gate", "decision": "denied"}]
    else:
        orch.gate.config = {"items": [{"id": "required", "text": "Required", "verify": "attest"}]}
        if suspension == "blocked":
            orch.gate.decline(["required"])
    selection = _parity(orch)
    assert selection.suspension == suspension
    assert selection.runnable == selection.action_holds == ()
    assert not _eligible(orch, TransitionIntent.PREPARE, "external").allowed
    assert orch.step_once().kind == {
        "readiness_gate": "readiness", "denied": "blocked",
    }.get(suspension, suspension)
    assert not calls and orch.state.get_step("phase", "owned") == owned


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("reserved", [False, True])
def test_external_inflight_is_a_wait_but_new_poll_requires_time_permission(
        tmp_path, execution, reserved):
    orch, calls, _, _ = _engine(tmp_path, [
        _step("external", "external", pollable=True), _step("sibling"),
    ], execution=execution, fire={"external": "11:00"})
    if reserved:
        assert orch._transition_kernel().reserve("phase", "external", "worker").changed
    previous = orch.state.get_step("phase", "external")
    previous.status = "in_flight"
    previous.note = "external running"
    orch.state.set_step("phase", "external", previous)
    selection = _parity(orch)
    if execution == "sequential" or not reserved:
        assert selection.focus_hold.kind == "in_flight"
    else:
        assert selection.focus_hold is None
    assert not selection.scout_pending
    assert not _eligible(orch, TransitionIntent.PREPARE, "external").allowed
    if reserved:
        assert not _eligible(orch, TransitionIntent.RESERVE, "external").allowed
    assert _eligible(orch, TransitionIntent.POLL, "external").allowed
    actions = orch.run_until_gate()
    assert actions[-1].kind == "waiting" and actions[-1].step == "external"
    assert calls == ([("sibling", "build")] if execution == "parallel" else [])
    assert orch.state.get_step("phase", "external") == previous
    # A fresh provider read still requires its configured invocation window.
    orch.now_local = orch.now_local.replace(hour=10)
    selection = _parity(orch)
    assert not selection.step("phase", "external").time_ready
    assert selection.focus_hold.kind == "in_flight" and selection.status == "running"
    assert orch.step_once().kind == "waiting"
    assert not _eligible(orch, TransitionIntent.POLL, "external").allowed


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("status", ["running", "blocked"])
def test_external_reservation_is_a_hold_not_a_runnable_or_pending_action(tmp_path, execution, status):
    orch, calls, _, _ = _engine(
        tmp_path, [_step("reserved", "external")], execution=execution,
    )
    assert orch._transition_kernel().reserve("phase", "reserved", "worker").changed
    record = orch.state.get_step("phase", "reserved")
    record.status = status
    orch.state.set_step("phase", "reserved", record)
    selection = _parity(orch)
    assert selection.focus_hold.reason == "reservation"
    assert not selection.runnable and not selection.action_holds
    assert not _eligible(orch, TransitionIntent.PREPARE, "reserved").allowed
    assert not _eligible(orch, TransitionIntent.RESERVE, "reserved").allowed
    action = orch.step_once()
    assert action.kind == "waiting" and action.step == selection.focus_hold.step_id
    assert not calls and orch.state.get_step("phase", "reserved") == record


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize("status", ["running", "blocked", "in_flight"])
@pytest.mark.parametrize("mode", ["idempotent", "transactional"])
def test_owned_effect_recovery_is_ordered_separate_and_once_per_drain(tmp_path, execution, status, mode):
    orch, calls, control, checkpoints = _engine(tmp_path, [
        _step("owned", effect_mode=mode, effect_recovery="frozen"),
        _step("dependent", depends_on=["owned"]), _step("sibling"),
    ], execution=execution, fire={"owned": "11:00"})
    control["outcome"] = InProgress("uncertain")
    orch.step_once()
    record = orch.state.get_step("phase", "owned")
    record.status = status
    orch.state.set_step("phase", "owned", record)
    owned = deepcopy(record.execution)
    orch.mocks["phase.owned"] = {"outcome": "done"}
    orch.now_local = orch.now_local.replace(hour=10)
    calls.clear()
    selection = _parity(orch)
    assert selection.runnable[0].recovery
    assert selection.runnable[0].step.id == "owned"
    assert not selection.step("phase", "owned").time_ready
    assert not _eligible(orch, TransitionIntent.EXECUTE, "owned").allowed
    assert not _eligible(orch, TransitionIntent.RESERVE, "owned").allowed
    attempted = {"phase.owned"}
    after_attempt = orch.scheduling(attempted=attempted)
    assert all(candidate.step.id != "owned" for candidate in after_attempt.runnable)
    assert attempted == {"phase.owned"}
    attempted.clear()
    assert after_attempt.attempted == frozenset({"phase.owned"})
    assert orch.step_once(attempted).kind == "waiting"
    assert calls == [("owned", "reconcile" if mode == "transactional" else "execute")]
    assert len(checkpoints) == 1
    assert orch.state.get_step("phase", "owned").execution == owned
    assert orch.state.get_step("phase", "dependent").status == "pending"
    assert all(candidate.step.id != "owned" for candidate in orch.scheduling(attempted).runnable)


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
def test_phase_and_dependency_boundaries_stop_recovery_without_losing_ownership(tmp_path, execution):
    orch, calls, control, _ = _engine(tmp_path, [
        _step("source"),
        _step("owned", effect_mode="transactional", effect_recovery="frozen", depends_on=["source"]),
    ], execution=execution, anchor="CCD")
    orch.step_once()
    control["outcome"] = InProgress("owned")
    orch.step_once()
    owned = deepcopy(orch.state.get_step("phase", "owned"))
    calls.clear()
    orch.as_of = orch.as_of.replace(day=11)
    selection = _parity(orch)
    assert not selection.phase("phase").due and not selection.runnable
    assert orch.step_once().kind == "scheduled"
    assert not calls
    orch.as_of = orch.as_of.replace(day=12)
    orch.state.set_step("phase", "source", StepState(status="pending"))
    selection = _parity(orch)
    assert not selection.step("phase", "owned").prerequisites_met
    assert [candidate.step.id for candidate in selection.runnable] == ["source"]
    assert orch.state.get_step("phase", "owned") == owned


def test_refresh_and_refresh_reservation_use_shared_phase_time_not_new_work_completion(tmp_path):
    orch, calls, _, _ = _engine(tmp_path, [
        _step("source", "human_action"),
        _step("observation", "external", repeatable=True, depends_on=["source"]),
        _step("unfinished", "human_action"),
    ], fire={"observation": "11:00"})
    orch.state.set_step("phase", "observation", StepState(status="done"))
    selection = _parity(orch)
    assert selection.step("phase", "observation").complete
    assert not selection.step("phase", "observation").prerequisites_met
    assert not selection.scout_pending
    assert not _eligible(orch, TransitionIntent.PREPARE, "observation").allowed
    for intent in (TransitionIntent.REFRESH, TransitionIntent.RESERVE):
        assert not _eligible(orch, intent, "observation").allowed
    orch.state.set_step("phase", "source", StepState(status="done"))
    for intent in (TransitionIntent.REFRESH, TransitionIntent.RESERVE):
        assert _eligible(orch, intent, "observation").allowed
    orch.now_local = orch.now_local.replace(hour=10)
    for intent in (TransitionIntent.REFRESH, TransitionIntent.RESERVE):
        assert not _eligible(orch, intent, "observation").allowed
    orch.now_local = orch.now_local.replace(hour=12)
    for sid in ("source", "unfinished"):
        orch.state.set_step("phase", sid, StepState(status="done"))
    orch.config["phases"].append({
        "id": "later", "name": "Later", "steps": [_step("later_work", "human_action")],
    })
    adopt_test_revision(orch)
    selection = _parity(orch)
    assert selection.frontier.id == "later"
    for intent in (TransitionIntent.REFRESH, TransitionIntent.RESERVE):
        assert not _eligible(orch, intent, "observation").allowed
    assert not calls


def test_focus_prefers_human_over_external_block_and_wait_without_hiding_scout_pending(tmp_path):
    orch, _, _, _ = _engine(tmp_path, [
        _step("waiting", "external", pollable=True), _step("scout", "external"),
        _step("blocked", "external"), _step("human", "human_action"),
    ])
    orch.state.set_step("phase", "waiting", StepState(status="in_flight"))
    orch.state.set_step("phase", "blocked", StepState(status="blocked"))
    selection = _parity(orch)
    assert selection.focus_hold.step_id == "blocked" and selection.focus_hold.owner == "human"
    assert selection.scout_pending == ("scout",)
    assert _eligible(orch, TransitionIntent.PREPARE, "blocked").allowed
    assert orch.step_once().step == "blocked"


def test_denial_is_not_hidden_by_a_future_phase_window(tmp_path):
    orch, calls, _, _ = _engine(
        tmp_path, [_step("auto"), _step("gate", "approval_gate")], anchor="CCD+1",
    )
    orch.state.gate_decisions = [{"step": "phase.gate", "decision": "denied"}]
    selection = _parity(orch)
    assert not selection.phase("phase").due
    assert selection.suspension == "denied" and selection.focus_hold.kind == "denied"
    assert orch.step_once().kind == "blocked" and not calls


def test_recovery_precedes_new_work_even_when_new_step_is_earlier_in_config(tmp_path):
    orch, calls, control, checkpoints = _engine(tmp_path, [
        _step("new"), _step("owned", effect_mode="transactional", effect_recovery="frozen"),
    ])
    orch.step_once()
    control["outcome"] = InProgress("uncertain")
    orch.step_once()
    owned = deepcopy(orch.state.get_step("phase", "owned"))
    orch.state.set_step("phase", "new", StepState(status="pending"))
    calls.clear()
    selection = _parity(orch)
    assert [(candidate.step.id, candidate.recovery) for candidate in selection.runnable] == [
        ("owned", True), ("new", False),
    ]
    attempted = {"phase.owned"}
    assert [candidate.step.id for candidate in orch.scheduling(attempted).runnable] == ["new"]
    assert orch.step_once(attempted).step == "new"
    assert calls == [("new", "build")] and len(checkpoints) == 1
    assert orch.state.get_step("phase", "owned") == owned


def test_new_poll_requires_owning_phase_frontier(tmp_path):
    orch, _, _, _ = _engine(tmp_path, [_step("poll", "external", pollable=True)])
    orch.state.set_step("phase", "poll", StepState(status="in_flight"))
    orch.config["phases"].insert(0, {
        "id": "earlier", "name": "Earlier", "steps": [_step("human", "human_action")],
    })
    adopt_test_revision(orch)
    orch.state.set_step("phase", "poll", StepState(status="in_flight"))
    selection = _parity(orch)
    assert selection.frontier.id == "earlier"
    assert not selection.scout_pending and not selection.runnable
    assert not _eligible(orch, TransitionIntent.POLL, "poll").allowed
    assert not _eligible(orch, TransitionIntent.PREPARE, "poll").allowed
