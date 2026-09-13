"""Canonical auto outcomes across direct handlers, mocks, and dummy shells."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

import orchestrator.engine as engine_module
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.outcomes import Blocked, Done, InProgress, NeedsHuman, NeedsSkill
from orchestrator.state import ReleaseState
from steps.lib import mockctx


def _orchestrator(tmp_path, monkeypatch, build, execution="sequential", mocks=None):
    config = {
        "phases": [{
            "id": "phase",
            "name": "Phase",
            "execution": execution,
            "steps": [
                {
                    "id": "check", "name": "Check", "kind": "auto",
                    "effect_mode": "read_only",
                },
                {
                    "id": "gate", "name": "Approval", "kind": "approval_gate",
                    "depends_on": ["check"],
                },
            ],
        }],
    }
    if build is None:
        config["phases"][0]["steps"][0]["implementation"] = "dummy"
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    handler = (
        SimpleNamespace(ID="check", KIND="agent", EFFECT_MODE="read_only", build=build)
        if build is not None else None
    )
    monkeypatch.setattr(
        engine_module.steps, "get_step",
        lambda phase, step: handler if (phase, step) == ("phase", "check") else None,
    )
    return Orchestrator(
        str(path), ReleaseState(release_id="r"), mocks=mocks or {},
        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("execution", ["sequential", "parallel"])
@pytest.mark.parametrize(
    "outcome,status,actor,action",
    [
        (Done("observed", by="observer"), "done", "observer", "ran"),
        (Blocked("unavailable"), "blocked", "agent", "reminder"),
        (InProgress("still running", poll_in_min=7), "in_flight", "agent", "waiting"),
    ],
)
def test_direct_build_outcomes_preserve_evidence(
        tmp_path, monkeypatch, execution, outcome, status, actor, action):
    outcome = deepcopy(outcome)
    outcome.links = [{"name": "Evidence", "url": "https://example.invalid/evidence"}]
    calls = []

    def build(state):
        calls.append(state)
        return outcome

    orch = _orchestrator(tmp_path, monkeypatch, build, execution)
    result = orch.step_once()
    record = orch.state.get_step("phase", "check")

    assert len(calls) == 1
    assert calls[0].release.release_id == orch.state.release_id
    assert not hasattr(calls[0], "set_step")
    assert result.kind == ("ran" if status == "blocked" and execution == "parallel" else action)
    assert record.status == status
    assert record.note == (outcome.reason if isinstance(outcome, Blocked) else outcome.note)
    assert record.by == actor
    assert record.links == outcome.links
    assert record.execution is None
    assert bool(record.completed_at) is (status == "done")
    assert orch.state.gate_decisions == []
    if isinstance(outcome, InProgress):
        assert record.data["poll_in_min"] == 7
        assert record.data["in_flight_since"]


@pytest.mark.parametrize(
    "value",
    [
        None, True, {"kind": "done"},
        SimpleNamespace(ok=True, action="legacy success", by="agent"),
        NeedsSkill(tool="workiq_send_email"),
        NeedsHuman("Confirm"),
    ],
)
def test_invalid_auto_result_does_not_apply_lifecycle_state(
        tmp_path, monkeypatch, value):
    orch = _orchestrator(tmp_path, monkeypatch, lambda _state: value)
    before = deepcopy(orch.state.steps)

    with pytest.raises(TypeError, match="expected Done/Blocked/InProgress"):
        orch.step_once()

    assert orch.state.steps == before
    assert orch.state.gate_decisions == []


@pytest.mark.parametrize("status", ["done", "blocked"])
def test_outcome_mocks_skip_handler_and_keep_mock_attribution(
        tmp_path, monkeypatch, status):
    def build(_state):
        pytest.fail("An outcome mock must not invoke the handler")

    orch = _orchestrator(
        tmp_path, monkeypatch, build,
        mocks={"phase.check": {"outcome": status, "note": "injected result"}},
    )
    orch.step_once()
    record = orch.state.get_step("phase", "check")

    assert record.status == status
    assert record.note == "injected result"
    assert record.by == "mock"


def test_input_mock_reaches_direct_build(tmp_path, monkeypatch):
    def build(context):
        return Done(context.input("message"))

    orch = _orchestrator(
        tmp_path, monkeypatch, build,
        mocks={"phase.check": {"message": "injected input"}},
    )
    orch.step_once()

    assert orch.state.get_step("phase", "check").note == "injected input"
    assert mockctx.mock_input("message", None) is None


def test_dummy_shell_advances_without_approving_gate(tmp_path, monkeypatch):
    orch = _orchestrator(tmp_path, monkeypatch, None)
    actions = orch.run_until_gate()
    record = orch.state.get_step("phase", "check")

    assert [action.kind for action in actions] == ["ran", "gate"]
    assert record.status == "done"
    assert record.note == "[DUMMY] Check: no operation performed; implementation deferred."
    assert record.links == []
    assert record.data == {}
    assert record.execution is None
    assert orch.state.gate_decisions == []
    assert orch.state.get_step("phase", "gate").status == "pending"
