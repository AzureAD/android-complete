"""Generic workflow compilation and pure state projection tests."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from tests._context import bind_model_state, fresh_orchestrator as Orchestrator
from orchestrator.handlers import HandlerCatalog
from orchestrator.projection import StateProjection
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import (
    StepKind,
    WorkflowConfigError,
    WorkflowDefinition,
)


def _config():
    return {
        "version": 1,
        "phases": [
            {
                "id": "build",
                "name": "Build",
                "steps": [
                    {
                        "id": "compile",
                        "name": "Compile",
                        "kind": "auto",
                        "owner": "agent",
                    },
                    {
                        "id": "approve",
                        "name": "Approve",
                        "kind": "approval_gate",
                        "owner": "human",
                        "gate": True,
                    },
                ],
            },
            {
                "id": "hotfix",
                "name": "Hotfix",
                "conditional": True,
                "steps": [
                    {
                        "id": "patch",
                        "name": "Patch",
                        "kind": "auto",
                        "owner": "agent",
                    },
                ],
            },
        ],
    }


def _runtime_config():
    config = _config()
    for phase in config["phases"]:
        for step in phase["steps"]:
            if step["kind"] == "auto":
                step["implementation"] = "dummy"
    return config


def _projection(config, state):
    workflow = WorkflowDefinition.compile(config)
    bind_model_state(state, workflow)
    return StateProjection(
        state,
        workflow,
        date(2026, 9, 11),
        datetime(2026, 9, 11, 12, tzinfo=timezone.utc),
    )


def test_compile_classifies_steps_and_builds_indexes():
    workflow = WorkflowDefinition.compile(_config())
    assert workflow.phase("build").execution == "sequential"
    assert workflow.step("build", "compile").kind == StepKind.AUTO
    assert workflow.step("build", "approve").kind == StepKind.APPROVAL_GATE
    assert workflow.step("hotfix", "patch").key == "hotfix.patch"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda cfg: cfg["phases"].append(deepcopy(cfg["phases"][0])),
            "Duplicate phase id",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(
                depends_on=["missing"]
            ),
            "depends on unknown step",
        ),
        (
            lambda cfg: (
                cfg["phases"][0].update(execution="parallel"),
                cfg["phases"][0]["steps"][0].update(depends_on=["approve"]),
                cfg["phases"][0]["steps"][1].update(depends_on=["compile"]),
            ),
            "Dependency cycle",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][1].update(attest=True),
            "attest contradicts",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(
                depends_on=["approve"]
            ),
            "cannot depend on later step",
        ),
        (
            lambda cfg: cfg["phases"][0].update(id="build.release"),
            "must contain only letters",
        ),
        (
            lambda cfg: cfg["phases"][0].update(id=" build"),
            "surrounding whitespace",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(source="typo"),
            "source contradicts",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].pop("name"),
            "requires a non-empty string name",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(
                kind="human_action", owner="human", source="scout"
            ),
            "source contradicts",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][1].update(gate="false"),
            "gate contradicts",
        ),
        (
            lambda cfg: cfg["phases"][1].update(conditional="false"),
            "conditional must be a boolean",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(depends_on=""),
            "invalid depends_on",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].pop("kind"),
            "requires an explicit kind",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][0].update(depend_on=[]),
            "Unknown step build.compile field",
        ),
        (
            lambda cfg: cfg["phases"][0]["steps"][1].update(
                approval_command="unknown-approval"
            ),
            "unregistered approval_command",
        ),
    ],
)
def test_compile_rejects_invalid_workflows(mutate, message):
    config = _config()
    mutate(config)
    with pytest.raises(WorkflowConfigError, match=message):
        WorkflowDefinition.compile(config)


def test_unapproved_terminal_gate_is_projected_pending_without_mutation():
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.set_step(
        "build",
        "approve",
        StepState(status="skipped", completed_at="legacy", note="old override"),
    )
    before = deepcopy(state.steps)

    projection = _projection(_config(), state)
    gate = projection.workflow.step("build", "approve")
    assert not projection.step_complete(gate)
    assert projection.frontier_phase().id == "build"
    assert projection.release_status() == "holding_gate"
    assert projection.current_hold().step_id == "approve"
    assert state.steps == before


def test_parallel_runnable_auto_step_projects_running():
    config = {
        "phases": [
            {
                "id": "parallel",
                "name": "Parallel",
                "execution": "parallel",
                "steps": [
                    {
                        "id": "auto",
                        "name": "Auto",
                        "kind": "auto",
                        "owner": "agent",
                    }
                ],
            }
        ]
    }
    state = ReleaseState(release_id="2026-09")
    projection = _projection(config, state)
    assert projection.current_hold() is None
    assert projection.release_status() == "running"


def test_parallel_denied_gate_has_priority_and_blocks_dispatch(tmp_path, monkeypatch):
    import yaml
    import orchestrator.engine as engine_module

    class ExternalHandler:
        ID = "auto"
        KIND = "scout"

        @staticmethod
        def build(_state):
            raise AssertionError("Denied gate must block external work.")

    monkeypatch.setattr(
        engine_module.steps,
        "get_step",
        lambda phase, step: ExternalHandler
        if (phase, step) == ("parallel", "auto")
        else None,
    )

    config = {
        "phases": [
            {
                "id": "parallel",
                "name": "Parallel",
                "execution": "parallel",
                "steps": [
                    {
                        "id": "pending_gate",
                        "name": "Pending gate",
                        "kind": "approval_gate",
                        "owner": "human",
                        "gate": True,
                    },
                    {
                        "id": "auto",
                        "name": "Auto",
                        "kind": "external",
                        "owner": "agent",
                        "source": "scout",
                    },
                    {
                        "id": "denied_gate",
                        "name": "Denied gate",
                        "kind": "approval_gate",
                        "owner": "human",
                        "gate": True,
                    },
                ],
            }
        ]
    }
    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    state.gate_decisions = [
        {
            "step": "parallel.denied_gate",
            "decision": "denied",
            "comment": "Evidence missing.",
        }
    ]
    orch = Orchestrator(str(config_path), state, mocks={})
    projection = orch._projection()
    assert projection.current_hold().step_id == "denied_gate"
    assert projection.release_status() == "blocked"
    action = orch.step_once()
    assert action.kind == "blocked"
    assert action.step == "denied_gate"
    assert state.get_step("parallel", "auto").status == "pending"
    assert orch.scout_pending_steps() == []
    assert orch.step_action_guard("parallel", "auto").kind == "blocked"


def test_parameterless_done_uses_projected_hold_not_stale_cursor(tmp_path):
    import yaml

    config = {
        "phases": [
            {
                "id": "parallel",
                "name": "Parallel",
                "execution": "parallel",
                "steps": [
                    {
                        "id": "first",
                        "name": "First",
                        "kind": "human_action",
                        "owner": "human",
                    },
                    {
                        "id": "second",
                        "name": "Second",
                        "kind": "human_action",
                        "owner": "human",
                    },
                ],
            }
        ]
    }
    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    orch = Orchestrator(str(config_path), state, mocks={})
    action = orch.complete_step(note="completed projected hold")
    assert action.step == "first"
    assert state.get_step("parallel", "first").status == "done"
    assert state.get_step("parallel", "second").status == "pending"


def test_latest_gate_decision_is_authoritative():
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.set_step("build", "approve", StepState(status="done"))
    state.gate_decisions = [
        {"step": "build.approve", "decision": "approved"},
        {"step": "build.approve", "decision": "denied"},
    ]
    projection = _projection(_config(), state)
    assert not projection.step_complete(projection.workflow.step("build", "approve"))

    state.gate_decisions.append(
        {"step": "build.approve", "decision": "approved"}
    )
    projection = _projection(_config(), state)
    assert projection.step_complete(projection.workflow.step("build", "approve"))


def test_denied_gate_projects_blocked_until_a_reconsider_transition_exists():
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.gate_decisions = [{"step": "build.approve", "decision": "denied"}]
    projection = _projection(_config(), state)
    assert projection.current_hold().kind == "denied"
    assert projection.release_status() == "blocked"


def test_gate_decision_is_rejected_while_release_is_suspended(tmp_path):
    import yaml

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(_runtime_config()), encoding="utf-8")
    state = ReleaseState(
        release_id="2026-09",
        halt={"reason": "test", "at": "2026-09-12T00:00:00Z"},
    )
    state.set_step("build", "compile", StepState(status="done"))
    orch = Orchestrator(str(config_path), state, mocks={})
    assert orch.approve_gate("unsafe").kind == "idle"
    assert orch.deny_gate("unsafe").kind == "idle"

    state.halt = None
    orch.gate.config = {
        "items": [{"id": "required", "text": "Required", "verify": "attest"}]
    }
    assert orch.approve_gate("unsafe").kind == "idle"
    assert orch.deny_gate("unsafe").kind == "idle"
    assert state.gate_decisions == []


def test_denied_gate_report_preserves_reason_and_blocked_step(tmp_path):
    import yaml

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(_runtime_config()), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.gate_decisions = [
        {
            "step": "build.approve",
            "decision": "denied",
            "comment": "Quality evidence is incomplete.",
        }
    ]
    report = Orchestrator(str(config_path), state, mocks={}).status_report()
    assert report["status"] == "blocked"
    assert report["gate"]["kind"] == "denied"
    assert report["gate"]["reason"] == "Quality evidence is incomplete."
    assert report["current_steps"][1]["state"] == "blocked"
    from orchestrator import render

    status = render.status_view(report).lower()
    assert "gate denied" in status
    assert "quality evidence is incomplete" in status


def test_conditional_activation_reopens_projected_complete_release():
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.set_step("build", "approve", StepState(status="done"))
    state.gate_decisions = [{"step": "build.approve", "decision": "approved"}]
    assert _projection(_config(), state).release_status() == "complete"

    state.active_conditionals.append("hotfix")
    projection = _projection(_config(), state)
    assert projection.frontier_phase().id == "hotfix"
    assert projection.release_status() == "running"


def test_delivery_scope_uses_projected_conditional_activation(tmp_path):
    import yaml
    from orchestrator import delivery

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(_runtime_config()), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.set_step("build", "approve", StepState(status="done"))
    state.gate_decisions = [{"step": "build.approve", "decision": "approved"}]
    state.active_conditionals.append("hotfix")
    orch = Orchestrator(str(config_path), state, mocks={})
    assert orch._projection().release_status() == "running"
    assert delivery.scope_reason(orch, {"kind": "release"}) == ""


def test_status_report_uses_projection_without_mutating_legacy_state(tmp_path):
    import yaml

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(_runtime_config()), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    state.set_step("build", "compile", StepState(status="done"))
    state.set_step("build", "approve", StepState(status="skipped", note="legacy"))
    before = deepcopy(state.steps)

    report = Orchestrator(str(config_path), state, mocks={}).status_report()
    assert report["status"] == "holding_gate"
    assert report["current_phase"] == "build"
    assert report["current_step"] == "approve"
    assert report["pending_human"] == ["build.approve"]
    assert report["current_steps"][1]["state"] == "gate"
    assert state.steps == before

    approved = Orchestrator(str(config_path), state, mocks={}).approve_gate("reviewed")
    assert approved.kind == "ran"
    assert state.get_step("build", "approve").status == "done"


def test_orchestrator_recompiles_after_in_memory_config_edit(tmp_path):
    import yaml

    config_path = tmp_path / "phases.yaml"
    config_path.write_text(yaml.safe_dump(_runtime_config()), encoding="utf-8")
    state = ReleaseState(release_id="2026-09")
    orch = Orchestrator(str(config_path), state, mocks={})
    original = orch.workflow.fingerprint

    orch.config["phases"][0]["steps"][1]["depends_on"] = ["compile"]
    refreshed = orch._workflow_definition()
    assert refreshed.fingerprint != original
    assert refreshed.step("build", "approve").depends_on == ("compile",)


def test_handler_contracts_validate_kind_and_approval_capability():
    external_config = {
        "phases": [
            {
                "id": "phase",
                "name": "Phase",
                "steps": [
                    {
                        "id": "external",
                        "name": "External",
                        "kind": "external",
                    }
                ],
            }
        ]
    }
    workflow = WorkflowDefinition.compile(external_config)
    with pytest.raises(WorkflowConfigError, match="requires a handler module"):
        HandlerCatalog.compile(workflow, lambda *_: None)

    class WrongHandler:
        ID = "external"
        KIND = "agent"

        @staticmethod
        def build(_state):
            return None

    with pytest.raises(WorkflowConfigError, match="requires module KIND 'scout'"):
        HandlerCatalog.compile(workflow, lambda *_: WrongHandler)

    gate_config = {
        "phases": [
            {
                "id": "phase",
                "name": "Phase",
                "steps": [
                    {
                        "id": "gate",
                        "name": "Gate",
                        "kind": "approval_gate",
                        "approval_command": "approve-orchestrator-gate",
                    }
                ],
            }
        ]
    }
    gate_workflow = WorkflowDefinition.compile(gate_config)

    class GateHandler:
        ID = "gate"
        KIND = "gate"
        APPROVAL_COMMAND = "wrong-command"

        @staticmethod
        def build(_state):
            return None

        @staticmethod
        def submit_approval(context=""):
            return True, "ok"

    with pytest.raises(WorkflowConfigError, match="does not match handler capability"):
        HandlerCatalog.compile(gate_workflow, lambda *_: GateHandler)


def test_schema_v3_loader_rejects_malformed_nested_facts(tmp_path):
    import json

    base = ReleaseState(release_id="r")
    path = tmp_path / "state.json"
    base.save(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["gate_decisions"] = ["bad"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid gate decisions"):
        ReleaseState.load(str(path))
