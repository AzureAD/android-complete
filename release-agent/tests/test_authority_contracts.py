"""Plug-and-play source declarations; no provider access or new durable fields."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace

import pytest

from orchestrator.authority import (
    BrokerPlanEvidence, OwnStepData, PipelineScope, PipelineSlot,
    UIFailureContribution, VersionEvidence, WriteCapabilities, WriteOperation,
)
from orchestrator.evidence import BrokerResource, PipelineEvidence, ReleaseVersions, StepData, UIFailureReminder
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Done
from orchestrator.services import EffectServices
from orchestrator.transitions import TransitionIntent
from orchestrator.workflow import WorkflowConfigError, WorkflowDefinition
from tests.test_handler_execution import _auto, _orch
from tests.test_handlers import _catalog, _effect_module, _module


def test_new_read_only_producer_needs_only_an_own_data_declaration(tmp_path):
    def build(ctx):
        assert ctx.effect is None
        return Done(updates=(StepData({"observed": [1, 2]}),))

    source = _auto(build, EVIDENCE=(OwnStepData(),))
    orch = _orch(tmp_path, lambda _phase, sid: source if sid == source.ID else None)
    source.EVIDENCE = ()
    handler = orch.handler("phase", "check")
    with pytest.raises(FrozenInstanceError):
        handler.evidence.scopes = ()
    assert orch.step_once().kind == "ran"
    assert orch.state.get_step("phase", "check").data == {"observed": [1, 2]}


def test_colliding_step_ids_do_not_share_evidence_or_write_authority(tmp_path, monkeypatch):
    from tools import pipelines
    calls = []
    monkeypatch.setattr(pipelines, "create_lightweight_tag",
                        lambda *args: calls.append(args) or (True, {}, ""))

    def execute(ctx):
        assert [f.name for f in fields(EffectServices)
                if getattr(ctx.effect.services, f.name) is not None] == ["create_lightweight_tag"]
        ctx.effect.services.create_lightweight_tag("new-tag", "commit")
        return Done(updates=(StepData({"tagged": True}),))

    first = SimpleNamespace(
        ID="check", KIND="agent", EFFECT_MODE="idempotent", EFFECT_RECOVERY="frozen",
        EVIDENCE=(OwnStepData(),), WRITES=(WriteOperation.CREATE_LIGHTWEIGHT_TAG,),
        build=lambda ctx: Done(), prepare_effect=lambda ctx: {"tag": "new-tag"},
        execute=execute)
    second = _auto(lambda ctx: Done(updates=(StepData({"stolen": True}),)))
    config = {"phases": [
        {"id": "first", "name": "First", "steps": [
            {"id": "check", "name": "Tag", "kind": "auto",
             "effect_mode": "idempotent", "effect_recovery": "frozen"}]},
        {"id": "second", "name": "Second", "steps": [
            {"id": "check", "name": "Read", "kind": "auto", "effect_mode": "read_only"}]},
    ]}
    orch = _orch(tmp_path, lambda phase, sid: first if phase == "first" else second, config)
    orch.state._checkpoint = lambda: None
    assert orch.step_once().kind == "ran"
    assert calls == [("new-tag", "commit")]
    assert orch.context("second", "check").effect is None
    assert orch.handler("second", "check").writes == WriteCapabilities()
    with pytest.raises(ValueError, match="does not own"):
        orch.step_once()
    assert orch.state.get_step("first", "check").data == {"tagged": True}
    assert not orch.state.get_step("second", "check").data


@pytest.mark.parametrize("attributes,match", [
    ({"EVIDENCE": [OwnStepData()]}, "tuple"),
    ({"EVIDENCE": ("StepData",)}, "unknown"),
    ({"EVIDENCE": (OwnStepData(), OwnStepData())}, "duplicate"),
    ({"EVIDENCE": (PipelineScope("ecs"),)}, "typed PipelineSlot"),
    ({"EVIDENCE": (UIFailureContribution("../other"),)}, "phase.step"),
    ({"EVIDENCE": (UIFailureContribution("phase.work"),)}, "human-review"),
    ({"EVIDENCE": (UIFailureContribution("missing.review"),)}, "human-review"),
    ({"WRITES": ("create_lightweight_tag",)}, "typed WriteOperation"),
    ({"WRITES": (WriteOperation.CREATE_LIGHTWEIGHT_TAG,) * 2}, "unique"),
    ({"WRITES": (WriteOperation.CREATE_LIGHTWEIGHT_TAG,)}, "effect-capable"),
    ({"STATUS_EMAIL": "yes"}, "boolean"),
])
def test_invalid_or_incompatible_declarations_fail_catalog_startup(attributes, match):
    with pytest.raises(WorkflowConfigError, match=match):
        _catalog(_module(**attributes), effect_mode="read_only")


@pytest.mark.parametrize("operation,scopes,mode,match", [
    (WriteOperation.ENSURE_BROKER_PLAN, (), "transactional", "BrokerPlanEvidence"),
    (WriteOperation.CREATE_AUTH_QUERY_SUITE, (), "transactional", "OwnStepData"),
    (WriteOperation.CREATE_AUTH_QUERY_SUITE, (OwnStepData(),), "idempotent", "transactional"),
    (WriteOperation.SUBMIT_PIPELINE_APPROVAL, (), "transactional", "approval hook"),
])
def test_write_capability_prerequisites_are_checked(operation, scopes, mode, match):
    with pytest.raises(WorkflowConfigError, match=match):
        _catalog(_effect_module(mode, WRITES=(operation,), EVIDENCE=scopes),
                 effect_mode=mode, effect_recovery="frozen")


@pytest.mark.parametrize("scope", [
    PipelineScope(PipelineSlot.ECS), VersionEvidence(), BrokerPlanEvidence(),
])
def test_shared_evidence_scope_cannot_have_competing_producers(scope):
    workflow = WorkflowDefinition.compile({"phases": [
        {"id": phase, "name": phase, "steps": [
            {"id": "work", "name": "work", "kind": "auto", "effect_mode": "read_only"}]}
        for phase in ("first", "second")
    ]})
    with pytest.raises(WorkflowConfigError, match="already owned by first.work"):
        HandlerCatalog.compile(workflow, lambda *_: _module(EVIDENCE=(scope,)))


@pytest.mark.parametrize("scope,update,expected", [
    (PipelineScope(PipelineSlot.ECS), PipelineEvidence({"rcs": [{"rc": 1, "ecs": {"run_id": 5}}]}),
     {"rcs": [{"rc": 1, "ecs": {"run_id": 5}}]}),
    (PipelineScope(PipelineSlot.CHECKER), PipelineEvidence({"checker": {"run_id": 5}}),
     {"checker": {"run_id": 5}}),
    (BrokerPlanEvidence(), BrokerResource({"status": "creating"}), {"status": "creating"}),
    (VersionEvidence(), ReleaseVersions({"broker": "1.0"}), {"broker": "1.0"}),
])
def test_existing_typed_evidence_families_can_be_reused_without_core_edits(tmp_path, scope, update, expected):
    source = _auto(lambda ctx: Done(updates=(update,)), EVIDENCE=(scope,))
    orch = _orch(tmp_path, lambda _phase, sid: source if sid == source.ID else None)
    assert orch.step_once().kind == "ran"
    actual = (orch.state.pipeline_runs if isinstance(update, PipelineEvidence) else
              orch.state.versions if isinstance(update, ReleaseVersions) else
              orch.state.resources["broker_test_plan"])
    assert actual == expected


@pytest.mark.parametrize("values", [
    {"rcs": [{"rc": 1, "auth": {"run_id": 9}}]},
    {"checker": {"run_id": 9}},
    {"rcs": [1]},
    {"rcs": []},
])
def test_reused_pipeline_lane_rejects_cross_producer_writes_and_removal(tmp_path, values):
    source = _auto(EVIDENCE=(PipelineScope(PipelineSlot.ECS),))
    orch = _orch(tmp_path, lambda _phase, sid: source if sid == source.ID else None)
    orch.state.pipeline_runs = {"rcs": [{"rc": 1, "local": {"run_id": 4}}]}
    before = deepcopy(orch.state.pipeline_runs)
    permit = orch.authorize_outcome(TransitionIntent.EXECUTE, "phase", "check")
    orch.context("phase", "check", permit=permit)
    with pytest.raises(ValueError):
        orch.apply_evidence(permit, Done(updates=(PipelineEvidence(values),)))
    assert orch.state.pipeline_runs == before


def test_new_ui_producer_targets_only_its_declared_human_contribution(tmp_path):
    from orchestrator.state import StepState
    source = _auto(EVIDENCE=(UIFailureContribution("phase.review"),))
    cfg = {"phases": [{"id": "phase", "name": "Phase", "steps": [
        {"id": "check", "name": "Producer", "kind": "auto", "effect_mode": "read_only"},
        {"id": "review", "name": "Review", "kind": "human_action"},
    ]}]}
    orch = _orch(tmp_path, lambda _phase, sid: source if sid == "check" else None, cfg)
    orch.state.set_step("phase", "review", StepState(
        status="done", by="human", note="Human assessment",
        data={"human": "kept"}, links=[{"name": "Human link", "url": "https://example.invalid"}]))
    permit = orch.authorize_outcome(TransitionIntent.EXECUTE, "phase", "check")
    orch.context("phase", "check", permit=permit)
    orch.apply_evidence(permit, Done(updates=(UIFailureReminder("🧪 Failed tests", (), 1, ()),)))
    record = orch.state.get_step("phase", "review")
    assert record.status == "done" and record.by == "human"
    assert record.note == "🧪 Failed tests\nHuman assessment"
    assert record.data["human"] == "kept" and record.links[0]["name"] == "Human link"


def test_cli_registration_and_workflow_capabilities_share_one_source():
    import argparse
    from orchestrator import cli, command_catalog as catalog
    parser = cli.build_parser()
    choices = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert catalog.APPROVAL_COMMANDS == frozenset(
        verb for item in catalog.COMMAND_MODULES for verb in item.approvals)
    assert catalog.EXTERNAL_WRITE_COMMANDS == frozenset(
        verb for item in catalog.COMMAND_MODULES for verb in item.external_writes)
    for item in catalog.COMMAND_MODULES:
        for verb in item.approvals + item.external_writes:
            assert choices[verb].get_default("func").__module__ == f"orchestrator.commands.{item.name}"


def test_misdeclared_command_capability_fails_registration(monkeypatch):
    import argparse
    from orchestrator import command_catalog as catalog
    monkeypatch.setattr(catalog, "COMMAND_MODULES", (
        catalog.CommandModule("release", approvals=("not-a-command",)),))
    with pytest.raises(ValueError, match="did not register declared"):
        catalog.register_commands(argparse.ArgumentParser().add_subparsers())


@pytest.mark.parametrize("fail_checkpoint", [False, True])
def test_reused_broker_adapter_preserves_durable_boundaries(tmp_path, monkeypatch, fail_checkpoint):
    from tools import broker_plans
    events = []

    def ensure(release_id, name, record, checkpoint, *, now):
        record["status"] = "creating"
        assert "broker_test_plan" not in orch.state.resources
        checkpoint()
        assert orch.state.resources["broker_test_plan"]["status"] == "creating"
        events.append("create")
        record.update(status="created", plan_id=22)
        assert "plan_id" not in orch.state.resources["broker_test_plan"]
        checkpoint()
        assert orch.state.resources["broker_test_plan"]["plan_id"] == 22
        events.append("suites")

    monkeypatch.setattr(broker_plans, "ensure_plan", ensure)

    def execute(ctx):
        ctx.effect.services.ensure_broker_plan("r", "Release plan", record={})
        return Done()

    source = SimpleNamespace(
        ID="check", KIND="agent", EFFECT_MODE="transactional", EFFECT_RECOVERY="frozen",
        EVIDENCE=(BrokerPlanEvidence(),), WRITES=(WriteOperation.ENSURE_BROKER_PLAN,),
        build=lambda ctx: Done(), prepare_effect=lambda ctx: {"plan": "Release plan"},
        execute=execute, reconcile=execute)
    cfg = {"phases": [{"id": "phase", "name": "Phase", "steps": [
        {"id": "check", "name": "Plan", "kind": "auto",
         "effect_mode": "transactional", "effect_recovery": "frozen"}]}]}
    orch = _orch(tmp_path, lambda *_: source, cfg)

    def persist():
        status = orch.state.resources.get("broker_test_plan", {}).get("status")
        if status:
            if fail_checkpoint:
                raise OSError("disk full")
            events.append(status)

    orch.state._checkpoint = persist
    if fail_checkpoint:
        with pytest.raises(OSError, match="disk full"):
            orch.step_once()
        assert not events and not orch.state.resources
        assert orch.state.get_step("phase", "check").execution
    else:
        orch.step_once()
        assert events[:4] == ["creating", "create", "created", "suites"]


def test_injected_write_bundle_cannot_grant_undeclared_ports():
    with pytest.raises(ValueError, match="Undeclared write port"):
        WriteCapabilities().validate_services(EffectServices(set_assigned_to=lambda: None))
    with pytest.raises(ValueError, match="Declared write port is missing"):
        WriteCapabilities((WriteOperation.CREATE_LIGHTWEIGHT_TAG,)).validate_services(EffectServices())


@pytest.mark.parametrize("eligible,hours", [(True, 2), (True, 8), (False, 8)])
def test_rc_poll_uses_shared_eligibility_for_unfamiliar_step_ids(
        tmp_path, monkeypatch, capsys, eligible, hours):
    import json
    from datetime import timedelta
    from orchestrator.commands import rc_poll
    from orchestrator.outcomes import InProgress
    from orchestrator.state import StepState

    calls = []
    def build(ctx):
        calls.append(ctx.step_key)
        return InProgress("Still running")

    source = _auto(build)
    cfg = {"phases": [{"id": "build_verify", "name": "RC", "steps": [
        {"id": "prerequisite", "name": "Review", "kind": "human_action"},
        {"id": "check", "name": "Additional RC verifier", "kind": "auto", "effect_mode": "read_only"},
    ]}]}
    orch = _orch(tmp_path, lambda _phase, sid: source if sid == "check" else None, cfg)
    since = (orch.now_local - timedelta(hours=hours)).isoformat()
    orch.state.owner_email = "test@example.com"
    if eligible:
        orch.state.set_step("build_verify", "prerequisite", StepState(status="done"))
    orch.state.set_step("build_verify", "check", StepState(
        status="in_flight", data={"in_flight_since": since}))
    monkeypatch.setattr(rc_poll.C, "load_orch", lambda *_: (orch.state, orch))
    monkeypatch.setattr(rc_poll.C, "save_state", lambda *_: None)
    args = SimpleNamespace(runs_root=str(tmp_path), release="r", config="", now=orch.now_local.isoformat())
    assert rc_poll.cmd_poll_rc(args) == 0
    result = json.loads(capsys.readouterr().out)
    if eligible:
        assert calls == ["build_verify.check"]
        assert result["step"] == "check"
        assert result["decision"] == ("nudge" if hours == 8 else "waiting")
        if hours == 8:
            assert "Additional RC verifier" in result["nudge"]["email"]["body"]
    else:
        assert not calls and not result.get("notifications")
        assert "step" not in result
