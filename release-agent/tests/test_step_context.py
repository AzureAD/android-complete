"""Immutable handler inputs and permit-scoped durable evidence boundaries."""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path

import pytest
import yaml
from orchestrator.authority import UIFailureContribution, WriteCapabilities, WriteOperation

import steps
from orchestrator import effects
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.evidence import BrokerResource, PipelineEvidence, ReleaseVersions, StepData, UIFailureReminder
from orchestrator.outcomes import Done, as_dict
from orchestrator.service_adapters import production_effects, production_services
from orchestrator.services import EffectServices
from orchestrator.state import ReleaseState, StepState
from orchestrator.step_context import Clock, thaw
from orchestrator.handler_contracts import HookRole
from orchestrator.parameters import ParameterSchema, ParameterError
from orchestrator.transitions import TransitionIntent
from tests._context import context


NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def engine(tmp_path, phase, step, *, services=None, writers=None):
    module = steps.get_step(phase, step)
    definition = {"id": step, "name": step, "kind": "auto",
                  "effect_mode": module.EFFECT_MODE}
    if hasattr(module, "EFFECT_RECOVERY"):
        definition["effect_recovery"] = module.EFFECT_RECOVERY
    if callable(getattr(module, "authorize_retry", None)):
        definition["effect_retry"] = True
    path = tmp_path / "workflow.yaml"
    definitions = [definition]
    for scope in getattr(module, "EVIDENCE", ()):
        if isinstance(scope, UIFailureContribution):
            target_phase, target = scope.target.split(".")
            assert target_phase == phase
            definitions.append({"id": target, "name": "Human review", "kind": "human_action"})
    path.write_text(yaml.safe_dump({"phases": [{"id": phase, "name": phase,
                                               "steps": definitions}]}), encoding="utf-8")
    state = ReleaseState(release_id="2026-08", ccd="2026-08-13", timezone="UTC",
                         owner_email="owner@example.com")
    orch = Orchestrator(str(path), state, mocks={}, clock=Clock(NOW),
                        services=services, effect_services=writers, now=NOW)
    return orch


def owned(orch, phase, step):
    value = {"target": "fixed"}
    module = steps.get_step(phase, step)
    orch.state.set_step(phase, step, StepState(status="running", execution={
        "id": "execution", "owner": "engine", "started_at": NOW.isoformat(),
        "effect_mode": module.EFFECT_MODE, "effect_recovery": module.EFFECT_RECOVERY,
        "effect_input": value, "operation_key": effects.input_key(step, value),
    }))
    permit = orch.authorize_outcome(TransitionIntent.EFFECT, phase, step, execution_id="execution")
    return permit, orch.context(phase, step, permit=permit, role=HookRole.EXECUTE)


def test_production_adapters_bind_provider_patches_at_invocation(monkeypatch):
    from tools import pipelines, testplans

    calls = []
    read = lambda *_: (True, [{"id": 42}], "")

    def write(*args, **kwargs):
        calls.append("write")
        return True, 45, ""

    monkeypatch.setattr(pipelines, "get_timeline", read)
    monkeypatch.setattr(testplans, "create_auth_query_suite", write)
    services = production_services()
    writers = production_effects(
        WriteCapabilities((WriteOperation.CREATE_AUTH_QUERY_SUITE,)), validate=lambda: calls.append("validate"),
        committer=None, clock=Clock(NOW))
    assert services.pipelines.get_timeline is read
    assert writers.create_auth_query_suite("suite", "query") == (True, 45, "")
    assert calls == ["validate", "write"]


def test_unmocked_adapter_ports_fail_loudly_before_provider_io(monkeypatch):
    import subprocess
    from urllib import request

    def unexpected(*args, **kwargs):
        pytest.fail("adapter bypassed the provider guard")

    monkeypatch.setattr(subprocess, "run", unexpected)
    monkeypatch.setattr(request, "urlopen", unexpected)
    services = production_services()
    auth = production_effects(
        WriteCapabilities((WriteOperation.CREATE_AUTH_QUERY_SUITE,)),
        validate=lambda: None, committer=None, clock=Clock(NOW))
    ui = production_effects(
        WriteCapabilities((WriteOperation.SET_ASSIGNED_TO,)),
        validate=lambda: None, committer=None, clock=Clock(NOW))
    with pytest.raises(RuntimeError, match="REAL ADO/az"):
        services.pipelines.get_timeline("https://example.invalid", "project", 1)
    with pytest.raises(RuntimeError, match="REAL ADO/az"):
        auth.create_auth_query_suite("suite", "query")
    with pytest.raises(RuntimeError, match="REAL ADO/az"):
        ui.set_assigned_to(1, "owner@example.com")


def test_context_has_deeply_immutable_detached_views_and_no_state_escape():
    state = ReleaseState(release_id="r", versions={"broker": "1"})
    state.set_step("p", "s", StepState(data={"nested": [{"id": 1}]}))
    ctx = context(state, inputs={"rows": [{"id": 2}]})
    for value in (ctx.evidence.step("p", "s").data["nested"], ctx.inputs["rows"]):
        with pytest.raises(TypeError):
            value.append({})
        with pytest.raises(TypeError):
            value[0]["id"] = 9
    with pytest.raises(FrozenInstanceError):
        ctx.release.release_id = "other"
    with pytest.raises(TypeError):
        ctx.release.versions["broker"] = "2"
    for name in ("state", "engine", "get_step", "set_step", "checkpoint", "commit"):
        assert not hasattr(ctx, name)
    assert ctx.effect is None and ctx.approval is None
    state.steps["p.s"]["data"]["nested"][0]["id"] = 7
    assert ctx.evidence.step("p", "s").data["nested"][0]["id"] == 1
    selected = ["a@example.com"]
    from steps.bug_bash.distribute_tests import BuildParameters
    params = ParameterSchema.compile(BuildParameters, "distribution build").parse({"oof": selected})
    selected.clear()
    assert params.oof == ("a@example.com",)


def test_read_only_service_and_fixed_clock_return_evidence_without_mutation(tmp_path):
    from steps.build_verify import checker_fired
    services = production_services()
    services = replace(services, pipelines=replace(
        services.pipelines,
        find_checker_runs=lambda *_: (True, [{"id": 42, "queueTime": "2026-08-20"}], ""),
        get_timeline=lambda *_: (True, [{"type": "Job", "name": checker_fired.CONFIG["trigger_job"],
                                       "result": "succeeded"}], "")))
    orch = engine(tmp_path, "build_verify", checker_fired.ID, services=services)
    permit = orch.authorize_outcome(TransitionIntent.EXECUTE, "build_verify", checker_fired.ID)
    ctx = orch.context("build_verify", checker_fired.ID, permit=permit)
    before = deepcopy(orch.state)
    out = checker_fired.build(ctx)
    assert orch.state == before and ctx.effect is None
    assert out.updates[0].values["checker"]["resolved_at"] == NOW.isoformat()
    serialized = as_dict(out)
    assert "updates" not in serialized
    assert json.loads(json.dumps(serialized))["kind"] == "done"
    orch.apply_outcome(permit, out)
    assert orch.state.pipeline_runs["checker"]["run_id"] == "42"


def test_external_payload_uses_injected_asset_read_and_typed_parameters():
    from steps.preflight import notice
    services = production_services()
    services = replace(services, assets=replace(services.assets, template=lambda _: (
        "===UPDATE:SUBJECT===\nUpdated {month}\n===UPDATE:BODY===\nFor {owner}")))
    state = ReleaseState(release_id="2026-08", ccd="2026-08-13", owner_name="Owner")
    ctx = replace(context(state, parameters={"variant": "update"}, model=notice.BuildParameters,
                          now=NOW), services=services)
    result = notice.build(ctx)
    assert result.kind == "needs_skill" and result.payload["subject"].startswith("Updated")
    assert ctx.effect is None and result.updates == ()
    assert "updates" not in json.loads(json.dumps(as_dict(result)))["payload"]


def test_commits_are_durable_scoped_and_reject_stale_ownership(tmp_path):
    orch = engine(tmp_path, "bug_bash", "clone_plans_auth")
    saved = []
    orch.state._checkpoint = lambda: saved.append(deepcopy(orch.state.steps))
    permit, ctx = owned(orch, "bug_bash", "clone_plans_auth")
    assert not hasattr(ctx.effect.commit, "apply")
    snapshot = ctx.effect.commit.commit(StepData({"creation": {"status": "creating"}}))
    assert len(saved) == 1
    assert snapshot.step("bug_bash", "clone_plans_auth").data["creation"]["status"] == "creating"
    with pytest.raises(ValueError, match="does not own"):
        ctx.effect.commit.commit(BrokerResource({"status": "ready"}))
    with pytest.raises(ValueError, match="cross-step"):
        ctx.effect.commit.commit(UIFailureReminder("no", (), 0, ()))
    record = orch.state.get_step("bug_bash", "clone_plans_auth")
    record.execution["id"] = "replacement"
    orch.state.set_step("bug_bash", "clone_plans_auth", record)
    with pytest.raises(ValueError, match="generation"):
        ctx.effect.commit.commit(StepData({"suite_id": 7}))
    assert len(saved) == 1 and "suite_id" not in record.data


def test_checkpoint_failure_stops_auth_post_and_restores_previous_evidence(tmp_path):
    services = production_services()
    services = replace(services, testplans=replace(
        services.testplans, find_auth_query_suite=lambda *a, **kw: (True, None, ""),
        validate_auth_query_suite=lambda *a, **kw: (True, {"name": "suite"}, "")))
    writes, saved = [], []
    def factory(step, validate, session, clock):
        def create(*args, **kwargs):
            validate()
            writes.append(step)
            assert saved[-1]["creation"]["status"] == "creating"
            return True, 45, ""
        return EffectServices(create_auth_query_suite=create)
    orch = engine(tmp_path, "bug_bash", "clone_plans_auth", services=services, writers=factory)
    def checkpoint():
        data = orch.state.get_step("bug_bash", "clone_plans_auth").data
        if data.get("creation"):
            raise OSError("disk unavailable")
        saved.append(deepcopy(data))
    orch.state._checkpoint = checkpoint
    with pytest.raises(OSError, match="disk unavailable"):
        orch.step_once()
    assert writes == []
    assert orch.state.get_step("bug_bash", "clone_plans_auth").data == {}
    assert orch.state.get_step("bug_bash", "clone_plans_auth").execution["id"]
    orch.state._checkpoint = lambda: saved.append(deepcopy(
        orch.state.get_step("bug_bash", "clone_plans_auth").data))
    assert orch.step_once().kind == "ran"
    assert writes == [WriteCapabilities((WriteOperation.CREATE_AUTH_QUERY_SUITE,))]
    assert orch.state.get_step("bug_bash", "clone_plans_auth").data["suite_id"] == 45


def test_pipeline_evidence_cannot_overwrite_another_verifier(tmp_path):
    orch = engine(tmp_path, "build_verify", "checker_fired")
    permit = orch.authorize_outcome(TransitionIntent.EXECUTE, "build_verify", "checker_fired")
    orch.context("build_verify", "checker_fired", permit=permit)
    with pytest.raises(ValueError, match="another producer"):
        orch.apply_evidence(permit, Done(updates=(PipelineEvidence({"rcs": [{"rc": 99}]}),)))
    assert not orch.state.pipeline_runs


def test_version_updates_reject_stale_baseline_and_malformed_outcomes(tmp_path):
    orch = engine(tmp_path, "build_verify", "orchestrator_health")
    permit = orch.authorize_outcome(TransitionIntent.EXECUTE, "build_verify", "orchestrator_health")
    orch.context("build_verify", "orchestrator_health", permit=permit)
    out = Done(updates=(ReleaseVersions({"broker": "1"}),))
    out.note = {}
    with pytest.raises(TypeError):
        orch.apply_outcome(permit, out)
    assert not orch.state.versions
    out.note = ""
    orch.state.versions["broker"] = "2"
    with pytest.raises(ValueError, match="Version evidence"):
        orch.apply_outcome(permit, out)
    assert orch.state.versions["broker"] == "2"
    with pytest.raises(ValueError, match="permit"):
        orch.apply_outcome(object(), Done())


def test_recreating_context_does_not_rebase_effect_evidence(tmp_path):
    orch = engine(tmp_path, "bug_bash", "clone_plans_auth")
    permit, ctx = owned(orch, "bug_bash", "clone_plans_auth")
    record = orch.state.get_step("bug_bash", "clone_plans_auth")
    record.data["suite_id"] = 55
    orch.state.set_step("bug_bash", "clone_plans_auth", record)
    with pytest.raises(ValueError, match="Evidence changed"):
        orch.context("bug_bash", "clone_plans_auth", permit=permit, role=HookRole.EXECUTE)
    with pytest.raises(ValueError, match="Step evidence changed"):
        ctx.effect.commit.commit(StepData({"suite_id": 99}))


def test_suspension_allows_owned_receipts_but_not_new_provider_calls(tmp_path):
    called = []
    def factory(step, validate, session, clock):
        def create(*args):
            validate()
            called.append(args)
        return EffectServices(create_auth_query_suite=create)
    orch = engine(tmp_path, "bug_bash", "clone_plans_auth", writers=factory)
    saved = []
    orch.state._checkpoint = lambda: saved.append(deepcopy(orch.state.steps))
    permit, ctx = owned(orch, "bug_bash", "clone_plans_auth")
    assert orch.cancel("Owner paused").changed
    ctx.effect.commit.commit(StepData({"suite_id": 45}))
    assert len(saved) == 1
    with pytest.raises(ValueError):
        ctx.effect.services.create_auth_query_suite()
    assert called == []
    orch.apply_outcome(permit, Done("Already applied", updates=(StepData({"suite_id": 45}),)))
    assert orch.state.get_step("bug_bash", "clone_plans_auth").status == "done"


def test_broker_adapter_saves_returned_id_before_suspension_stops_suite_writes(tmp_path, monkeypatch):
    from tools import broker_plans
    orch = engine(tmp_path, "bug_bash", "clone_plans_broker")
    saved, writes = [], []
    orch.state._checkpoint = lambda: saved.append(deepcopy(orch.state.resources))
    permit, ctx = owned(orch, "bug_bash", "clone_plans_broker")
    def ensure(release, name, record, checkpoint, **kwargs):
        record["status"] = "creating"
        checkpoint()
        assert saved[-1]["broker_test_plan"]["status"] == "creating"
        writes.append("POST")
        record.update(status="created", plan_id=42)
        assert orch.cancel("Stop after create response").changed
        checkpoint()
        writes.append("suites")
        raise AssertionError("Suspension must prevent suite writes")
    monkeypatch.setattr(broker_plans, "ensure_plan", ensure)
    with pytest.raises(ValueError):
        ctx.effect.services.ensure_broker_plan("release", "name", record={})
    assert writes == ["POST"]
    assert saved[-1]["broker_test_plan"] == {"status": "created", "plan_id": 42}
    assert ctx.recovery().resources["broker_test_plan"]["plan_id"] == 42


def test_ui_producer_preserves_human_content_and_checks_target_generation(tmp_path):
    orch = engine(tmp_path, "bug_bash", "ui_test_status")
    orch.state._checkpoint = lambda: None
    human = StepState(status="done", note="Reviewed by owner", by="human",
                      links=[{"name": "Review", "url": "https://example.test/review"}],
                      data={"review": "keep"})
    orch.state.set_step("bug_bash", "ui_failures", human)
    permit, ctx = owned(orch, "bug_bash", "ui_test_status")
    ctx.effect.commit.commit(UIFailureReminder("🧪 failures", (), 1, ()))
    current = orch.state.get_step("bug_bash", "ui_failures")
    assert current.status == "done" and current.by == "human"
    assert human.note in current.note and current.links == human.links
    assert current.data["review"] == "keep"
    current.status = "pending"
    orch.state.set_step("bug_bash", "ui_failures", current)
    with pytest.raises(ValueError, match="UI reminder generation"):
        ctx.effect.commit.commit(UIFailureReminder("🧪 changed", (), 2, ()))


@pytest.mark.parametrize("phase, step, role, values", [
    ("preflight", "notice", "build", {"variant": 1}),
    ("finalize", "remove_rc_tags_gate", "prepare_approval", {"comment": None}),
    ("bug_bash", "distribute_tests", "build", {"oof": "not-a-list"}),
])
def test_typed_parameters_validate_direct_and_parsed_construction(phase, step, role, values):
    model = steps.get_step(phase, step).PARAMETERS[role]
    schema = ParameterSchema.compile(model, f"{phase}.{step} {role}")
    for value in (values, model(**values)):
        with pytest.raises(ParameterError):
            schema.parse(value)


def test_all_implemented_handlers_have_context_only_contracts():
    modules = steps.discover(force=True)
    assert len(modules) == 39
    for key, module in modules.items():
        for hook in ("build", "prepare_effect", "execute", "reconcile", "authorize_retry",
                     "prepare_approval", "submit_approval", "reconcile_approval"):
            function = getattr(module, hook, None)
            if function:
                assert list(inspect.signature(function).parameters) == ["context"], (key, hook)


def test_handlers_do_not_import_mutable_state_or_invoke_unrestricted_io():
    forbidden_calls = {"get_step", "set_step", "checkpoint", "Orchestrator", "ReleaseState",
                       "urlopen", "open", "_run", "_az_json", "_ado_rest_send"}
    writers = {"create_auth_query_suite", "build_broker_plan", "fill_auth_ui_results",
               "fill_ui_automation_results", "set_assigned_to", "submit_pipeline_approval",
               "create_lightweight_tag", "oneauth_write_access", "ensure_plan"}
    for path in Path(steps.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in ("orchestrator.state", "orchestrator.engine"), path
            if isinstance(node, ast.Call):
                function = ast.unparse(node.func)
                name = function.rsplit(".", 1)[-1]
                if path.name != "__init__.py":
                    assert name not in forbidden_calls, (path, function)
                if name in writers:
                    assert function.startswith("context.effect.services."), (path, function)
