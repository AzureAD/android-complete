"""A parallel step's wait must not stop independent work or replay owned effects."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.outcomes import Done, InProgress
from orchestrator.state import ReleaseState, StepState


def _orch(tmp_path, *, execution="parallel", effect_mode=None, recovery="frozen",
          external=False, gate_dependencies=None):
    calls, checkpoints = [], []
    control = {"outcome": InProgress("job still running", poll_in_min=11), "input": 1}
    slow = {"id": "slow", "name": "Slow", "kind": "external" if external else "auto"}
    if external:
        slow["pollable"] = True
    else:
        slow["effect_mode"] = effect_mode or "read_only"
        if effect_mode:
            slow["effect_recovery"] = recovery
    config = {
        "phases": [{
            "id": "phase", "name": "Phase", "execution": execution,
            "steps": [
                slow,
                {"id": "dependent", "name": "Dependent", "kind": "auto",
                 "effect_mode": "read_only", "depends_on": ["slow"]},
                {"id": "fast", "name": "Fast", "kind": "auto", "effect_mode": "read_only"},
                {"id": "fast_child", "name": "Fast child", "kind": "auto",
                 "effect_mode": "read_only", "depends_on": ["fast"]},
                {"id": "gate", "name": "Gate", "kind": "approval_gate",
                 "depends_on": gate_dependencies or ["dependent", "fast_child"]},
            ],
        }],
    }

    def observe_slow(_state):
        calls.append(("slow", "build"))
        return control["outcome"]

    modules = {
        "slow": SimpleNamespace(
            ID="slow", KIND="scout" if external else "agent", build=observe_slow,
            **({} if external else {"EFFECT_MODE": effect_mode or "read_only"}),
        ),
    }
    for sid in ("dependent", "fast", "fast_child"):
        def build(_state, sid=sid):
            calls.append((sid, "build"))
            return Done(f"{sid} complete")
        modules[sid] = SimpleNamespace(ID=sid, KIND="agent", EFFECT_MODE="read_only", build=build)
    if effect_mode:
        def prepare(_state):
            calls.append(("slow", "prepare"))
            return {"input": control["input"]}

        def execute(context):
            owned = context.effect.execution
            assert checkpoints
            assert state.get_step("phase", "slow").execution == owned
            calls.append(("slow", "execute"))
            return control["outcome"]

        def reconcile(context):
            owned = context.effect.execution
            assert owned["id"] == checkpoints[0].execution["id"]
            calls.append(("slow", "reconcile"))
            return control["outcome"]

        modules["slow"].EFFECT_RECOVERY = recovery
        modules["slow"].prepare_effect = prepare
        modules["slow"].execute = execute
        modules["slow"].reconcile = reconcile
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state = ReleaseState(release_id="r", timezone="UTC")
    state._checkpoint = lambda: checkpoints.append(state.get_step("phase", "slow"))
    orch = Orchestrator(
        str(path), state, mocks={},
        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        handler_resolver=lambda _phase, step: modules.get(step),
    )
    return orch, calls, control, checkpoints


def test_parallel_wait_drains_independent_work_once_then_retries_next_pass(tmp_path):
    orch, calls, control, _ = _orch(tmp_path)
    actions = orch.run_until_gate()

    assert calls == [("slow", "build"), ("fast", "build"), ("fast_child", "build")]
    assert [action.kind for action in actions] == ["waiting", "ran", "ran", "waiting"]
    assert actions[0].continue_drain and not actions[-1].continue_drain
    assert actions[-1].step == "slow"
    assert "job still running" in actions[-1].message
    assert orch.state.get_step("phase", "dependent").status == "pending"
    assert orch.state.get_step("phase", "slow").data["poll_in_min"] == 11
    assert not orch.state.gate_decisions

    calls.clear()
    actions = orch.run_until_gate()
    assert calls == [("slow", "build")]
    assert [action.kind for action in actions] == ["waiting", "waiting"]
    control["outcome"] = Done("job complete")
    calls.clear()
    actions = orch.run_until_gate()
    assert calls == [("slow", "build"), ("dependent", "build")]
    assert actions[-1].kind == "gate"
    assert not orch.state.gate_decisions


@pytest.mark.parametrize("mode", ["idempotent", "transactional"])
def test_owned_effect_wait_does_not_starve_siblings_or_replay_in_same_drain(tmp_path, mode):
    orch, calls, _, checkpoints = _orch(tmp_path, effect_mode=mode)
    actions = orch.run_until_gate()
    owned = deepcopy(orch.state.get_step("phase", "slow").execution)

    assert calls == [
        ("slow", "prepare"), ("slow", "execute"), ("fast", "build"), ("fast_child", "build"),
    ]
    assert len(checkpoints) == 1
    assert actions[-1].kind == "waiting"
    assert actions[-1].step == "slow"
    assert not actions[-1].continue_drain
    calls.clear()
    orch.run_until_gate()
    assert calls == [("slow", "reconcile" if mode == "transactional" else "execute")]
    assert len(checkpoints) == 1
    assert orch.state.get_step("phase", "slow").execution == owned
    assert orch.state.get_step("phase", "dependent").status == "pending"


def test_recovery_hold_does_not_stop_independent_work(tmp_path):
    orch, calls, control, _ = _orch(
        tmp_path, effect_mode="idempotent", recovery="match_current",
    )
    orch.step_once()
    owned = deepcopy(orch.state.get_step("phase", "slow").execution)
    control["input"] = 2
    calls.clear()
    actions = orch.run_until_gate()

    assert calls == [("slow", "prepare"), ("fast", "build"), ("fast_child", "build")]
    assert actions[0].kind == "reminder" and actions[0].continue_drain
    assert "inputs changed" in actions[0].message
    assert orch.state.get_step("phase", "slow").execution == owned
    assert orch.state.get_step("phase", "slow").status == "blocked"
    assert not actions[-1].continue_drain


@pytest.mark.parametrize("mode", [None, "transactional"])
def test_sequential_wait_still_stops_immediately(tmp_path, mode):
    orch, calls, _, _ = _orch(tmp_path, execution="sequential", effect_mode=mode)
    actions = orch.run_until_gate()
    assert len(actions) == 1
    assert actions[0].kind == "waiting" and not actions[0].continue_drain
    assert all(sid == "slow" for sid, _hook in calls)
    assert orch.state.get_step("phase", "fast").status == "pending"


def test_ready_gate_surfaces_after_independent_work_without_automatic_approval(tmp_path):
    orch, calls, _, _ = _orch(tmp_path, gate_dependencies=["fast_child"])
    actions = orch.run_until_gate()
    assert ("fast_child", "build") in calls
    assert actions[-1].kind == "gate" and not actions[-1].continue_drain
    assert orch.state.get_step("phase", "slow").status == "in_flight"
    assert not orch.state.gate_decisions


@pytest.mark.parametrize("suspension", ["halted", "cancelled", "denied", "readiness"])
def test_global_holds_prevent_parallel_dispatch(tmp_path, suspension):
    orch, calls, _, _ = _orch(tmp_path, gate_dependencies=["fast_child"])
    if suspension == "halted":
        orch.halt("hold")
    elif suspension == "cancelled":
        orch._transition_kernel().cancel("cancel")
    elif suspension == "readiness":
        orch.gate.config = {"items": [{"id": "required", "text": "Required", "verify": "attest"}]}
    else:
        orch.state.set_step("phase", "fast_child", StepState(status="done"))
        orch.state.gate_decisions = [{
            "step": "phase.gate", "decision": "denied",
            "at": "2026-09-12T12:00:00Z", "by": "human", "comment": "not ready",
        }]
    actions = orch.run_until_gate()
    assert len(actions) == 1 and not actions[0].continue_drain
    assert calls == []


@pytest.mark.parametrize("reserved", [False, True])
def test_external_wait_does_not_execute_or_starve_independent_work(tmp_path, reserved):
    orch, calls, _, _ = _orch(tmp_path, external=True)
    if reserved:
        assert orch._transition_kernel().reserve("phase", "slow", "external-worker").changed
    else:
        orch.state.set_step("phase", "slow", StepState(status="in_flight", note="external job running"))
    before = deepcopy(orch.state.get_step("phase", "slow"))
    actions = orch.run_until_gate()
    assert calls == [("fast", "build"), ("fast_child", "build")]
    assert actions[-1].kind == "waiting" and actions[-1].step == "slow"
    assert not actions[-1].continue_drain
    assert orch.state.get_step("phase", "slow") == before


def test_step_limit_remains_bounded_during_parallel_wait(tmp_path):
    orch, calls, _, _ = _orch(tmp_path)
    actions = orch.run_until_gate(max_steps=1)
    assert len(actions) == 1 and actions[0].continue_drain
    assert calls == [("slow", "build")]
    assert orch.state.get_step("phase", "fast").status == "pending"


def test_simulation_respects_step_local_wait_without_inventing_completion(tmp_path):
    from orchestrator.sim import _fast_forward

    orch, calls, _, _ = _orch(tmp_path)
    result = _fast_forward(orch, orch.config, "phase", "done", set())
    assert result["kind"] == "waiting"
    assert calls == [("slow", "build"), ("fast", "build"), ("fast_child", "build")]
    assert result["forwarded"] == 2
    assert not result["problems"]
    assert orch.state.get_step("phase", "slow").status == "in_flight"
