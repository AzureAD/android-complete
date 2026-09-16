"""Catalog binding at the engine, projection, and external-action boundaries."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
import json

import pytest
import yaml

import orchestrator.engine as engine_module
from orchestrator.commands.step_action import prepare_step
from tests._context import fresh_orchestrator as Orchestrator, adopt_test_revision
from orchestrator.outcomes import Done, NeedsSkill
from orchestrator.state import ReleaseState, StepState
from orchestrator.workflow import WorkflowConfigError


def _config(kind="auto"):
    step = {"id": "check", "name": "Check", "kind": kind}
    if kind == "auto":
        step["effect_mode"] = "read_only"
    return {
        "phases": [{
            "id": "phase", "name": "Phase", "anchor": "CCD",
            "steps": [
                step,
                {"id": "gate", "name": "Approval", "kind": "approval_gate"},
            ],
        }],
    }


def _orch(tmp_path, resolver, config=None, mocks=None):
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config or _config()), encoding="utf-8")
    return Orchestrator(
        str(path),
        ReleaseState(release_id="r", ccd="2026-09-12", timezone="UTC"),
        now=datetime(2026, 9, 12, 12, tzinfo=timezone.utc),
        mocks=mocks or {},
        handler_resolver=resolver,
    )


def _auto(build=None, **extra):
    return SimpleNamespace(
        ID="check", KIND="agent", EFFECT_MODE="read_only",
        build=build or (lambda _state: Done("bound outcome")), **extra,
    )


def test_injected_catalog_is_resolved_once_and_binds_auto_callable(tmp_path, monkeypatch):
    module = _auto()
    resolutions = []

    def resolve(phase, step):
        resolutions.append((phase, step))
        return module if step == "check" else None

    monkeypatch.setattr(
        engine_module.steps, "get_step",
        lambda *_args: pytest.fail("Injected execution must not use global discovery"),
    )
    orch = _orch(tmp_path, resolve)
    descriptor = orch.handler("phase", "check")
    module.build = lambda _state: pytest.fail("Must use the bound callable")

    assert orch.handler("phase", "check") is descriptor
    assert orch.status_report()["current_phase"] == "phase"
    assert orch.step_once().kind == "ran"
    assert orch.state.get_step("phase", "check").note == "bound outcome"
    assert resolutions == [("phase", "check"), ("phase", "gate")]


def test_failed_recompile_keeps_workflow_and_catalog_together(tmp_path):
    modules = {"check": _auto()}
    orch = _orch(tmp_path, lambda _phase, step: modules.get(step))
    old_workflow, old_catalog = orch.workflow, orch.handlers
    before = deepcopy(orch.state.steps)
    orch.config["phases"][0]["steps"].insert(
        1, {"id": "added", "name": "Added", "kind": "auto", "effect_mode": "read_only"}
    )

    with pytest.raises(WorkflowConfigError, match="requires a handler module"):
        orch.step_once()

    assert orch.workflow is old_workflow
    assert orch.handlers is old_catalog
    assert orch.state.steps == before

    modules["added"] = SimpleNamespace(
        ID="added", KIND="agent", EFFECT_MODE="read_only",
        build=lambda _state: Done("new step"),
    )
    adopt_test_revision(orch)
    assert [action.kind for action in orch.run_until_gate()] == ["ran", "ran", "gate"]
    assert orch.workflow is not old_workflow
    assert orch.handlers is not old_catalog
    assert orch.state.get_step("phase", "added").note == "new step"


def test_timing_uses_bound_catalog_metadata(tmp_path):
    module = _auto(CONFIG={"fire_at_local": "13:00"})
    orch = _orch(tmp_path, lambda _phase, step: module if step == "check" else None)
    module.CONFIG["fire_at_local"] = "01:00"

    assert orch.status_report()["status"] == "scheduled"
    action = orch.step_once()
    assert action.kind == "scheduled"
    assert "13:00" in action.message
    assert orch.state.steps == {}


def test_outcome_mock_does_not_hide_missing_real_handler(tmp_path):
    with pytest.raises(WorkflowConfigError, match="requires a handler module"):
        _orch(
            tmp_path, lambda *_args: None,
            mocks={"phase.check": {"outcome": "done"}},
        )


def test_uninspectable_handler_fails_before_execution(tmp_path):
    module = _auto()
    module.build.__signature__ = "invalid signature"
    with pytest.raises(WorkflowConfigError, match=r"phase.check.*cannot validate build"):
        _orch(tmp_path, lambda _phase, step: module if step == "check" else None)


def test_external_step_action_uses_bound_builder_and_metadata(tmp_path, monkeypatch):
    calls = []

    def build(context):
        variant = context.parameters.variant
        calls.append((context.release.release_id, variant))
        return NeedsSkill(
            tool="external_tool", payload={"variant": variant},
            record_as="check", outbound=False,
        )

    @dataclass(frozen=True)
    class Parameters:
        variant: str | None = None

    module = SimpleNamespace(ID="check", KIND="scout", build=build,
                             PARAMETERS={"build": Parameters})
    orch = _orch(
        tmp_path, lambda _phase, step: module if step == "check" else None,
        config=_config("external"),
    )
    module.build = lambda *_args, **_kwargs: pytest.fail("Must use bound builder")
    module.ID = "different"
    monkeypatch.setattr(
        engine_module.steps, "get_step",
        lambda *_args: pytest.fail("Step-action must not rediscover handlers"),
    )

    result = prepare_step(
        SimpleNamespace(phase="phase", step="check", release="r", param=["variant=review"]),
        orch.state, orch,
    )
    assert result["kind"] == "needs_skill"
    assert result["step"] == "check"
    assert result["payload"] == {"variant": "review"}
    assert calls == [("r", "review")]


def test_frozen_mock_aliases_become_detached_json_payloads(tmp_path, monkeypatch):
    from orchestrator import mocks
    from orchestrator.commands.step_action import _catalog

    module = SimpleNamespace(
        ID="check", KIND="scout",
        build=lambda _state: NeedsSkill(
            tool="external_tool", payload={"to": []}, record_as="check",
        ),
        MOCKABLE={
            "send_to": {
                "kind": "payload", "sets": "to", "as": "list",
                "aliases": {"team": ["one@example.invalid", "two@example.invalid"]},
            },
        },
    )
    resolver = lambda _phase, step: module if step == "check" else None
    orch = _orch(tmp_path, resolver, config=_config("external"))
    monkeypatch.setattr(
        mocks, "load_mocks", lambda: {"phase.check": {"send_to": "team"}},
    )
    monkeypatch.setattr(engine_module.steps, "get_step", resolver)

    result = prepare_step(
        SimpleNamespace(phase="phase", step="check", release="r"),
        orch.state, orch,
    )
    assert result["payload"]["to"] == ["one@example.invalid", "two@example.invalid"]
    json.dumps(result)
    spec = _catalog(str(tmp_path / "phases.yaml"))
    json.dumps(spec)
    assert spec["phase.check"]["implementation"] == "handler"
    result["payload"]["to"].append("different@example.invalid")
    assert orch.handler("phase", "check").mockable["send_to"]["aliases"]["team"] == (
        "one@example.invalid", "two@example.invalid"
    )


@pytest.mark.parametrize("kind", ["auto", "external"])
@pytest.mark.parametrize("suspension", ["halt", "cancel"])
def test_invoked_result_settles_during_suspension_without_continuing(tmp_path, kind, suspension):
    calls = []

    def build(context):
        calls.append("build")
        if suspension == "halt":
            orch.state.halt = {"reason": "incident"}
        else:
            orch.state.cancellation = {"reason": "cancelled"}
        return Done("received")

    module = _auto(build) if kind == "auto" else SimpleNamespace(
        ID="check", KIND="scout", build=build)
    orch = _orch(tmp_path, lambda _p, s: module if s == "check" else None, _config(kind))
    if kind == "auto":
        actions = orch.run_until_gate()
        assert len(actions) == 1 and not actions[0].continue_drain
    else:
        out = prepare_step(SimpleNamespace(phase="phase", step="check", release="r"), orch.state, orch)
        assert out["state_changed"]
    assert calls == ["build"]
    assert orch.state.get_step("phase", "check").status == "done"
    assert not orch.scheduling().runnable


@pytest.mark.parametrize("kind", ["auto", "external"])
def test_handler_generation_change_cannot_be_completed_by_old_result(tmp_path, kind):
    def build(context):
        record = orch.state.get_step("phase", "check")
        record.invalidated_at = "2026-09-12T12:01:00+00:00"
        orch.state.set_step("phase", "check", record)
        return Done("stale result")

    module = _auto(build) if kind == "auto" else SimpleNamespace(ID="check", KIND="scout", build=build)
    orch = _orch(tmp_path, lambda _p, s: module if s == "check" else None, _config(kind))
    if kind == "auto":
        action = orch.step_once()
        assert action.kind == "idle" and "generation" in action.message
    else:
        with pytest.raises(ValueError, match="generation"):
            prepare_step(SimpleNamespace(phase="phase", step="check", release="r"), orch.state, orch)
    assert orch.state.get_step("phase", "check").status == "pending"


@pytest.mark.parametrize("route,reserved", [
    ("prepare", False), ("poll", False), ("refresh", False), ("poll", True),
])
def test_external_canonical_result_uses_matching_invocation_permission(tmp_path, route, reserved):
    module = SimpleNamespace(ID="check", KIND="scout", build=lambda _state: Done("canonical"))
    config = _config("external")
    config["phases"][0]["steps"][0].update(repeatable=True, pollable=True)
    orch = _orch(tmp_path, lambda _p, s: module if s == "check" else None, config)
    execution_id = None
    if reserved:
        assert orch.reserve_execution("phase", "check", "worker").changed
        execution_id = orch.step_execution("phase", "check")["id"]
    record = orch.state.get_step("phase", "check")
    if route != "prepare":
        record.status = "in_flight" if route == "poll" else "done"
    orch.state.set_step("phase", "check", record)
    result = prepare_step(
        SimpleNamespace(phase="phase", step="check", release="r", execution_id=execution_id),
        orch.state, orch,
    )
    assert result["kind"] == "done" and result["state_changed"]
    assert orch.state.get_step("phase", "check").execution is None


def test_action_preview_cannot_offer_work_after_handler_changes_generation(tmp_path):
    def build(context):
        orch.state.set_step("phase", "check", StepState(invalidated_at="2026-09-12T12:01:00Z"))
        return NeedsSkill(tool="external_tool", payload={}, record_as="check", outbound=True)

    module = SimpleNamespace(ID="check", KIND="scout", build=build)
    orch = _orch(tmp_path, lambda _p, s: module if s == "check" else None, _config("external"))
    with pytest.raises(ValueError, match="generation"):
        prepare_step(SimpleNamespace(phase="phase", step="check", release="r"), orch.state, orch)
    assert orch.state.get_step("phase", "check").execution is None


def test_auto_result_cannot_complete_reconfigured_human_step(tmp_path):
    def build(_state):
        orch.config["phases"][0]["steps"][0].update(kind="human_action")
        orch.config["phases"][0]["steps"][0].pop("effect_mode")
        modules.pop("check")
        return Done("old definition")

    modules = {"check": _auto(build)}
    orch = _orch(tmp_path, lambda _p, s: modules.get(s))
    action = orch.step_once()
    assert action.kind == "idle" and "Workflow revision mismatch" in action.message
    assert not orch.state.is_done("phase", "check")
