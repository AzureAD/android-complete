"""Module-owned input contracts and role-specific invocation checks, without IO."""
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Mapping

import pytest
import yaml

from orchestrator.commands.step_action import _parse_params, prepare_step
from orchestrator.evidence import RetryDecision
from orchestrator.authority import WriteOperation
from orchestrator.handler_contracts import HandlerContractError, HookRole
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Done, InProgress, NeedsHuman, NeedsSkill
from orchestrator.parameters import NoParameters, ParameterError, ParameterSchema
from orchestrator.workflow import WorkflowConfigError, WorkflowDefinition
from tests.test_handlers import _approval_module, _catalog, _context, _effect_module, _module
from tests.test_handler_execution import _config, _orch


@dataclass(frozen=True)
class Inputs:
    message: str = "original"
    count: int = 1
    enabled: bool = False
    recipients: tuple[str, ...] = ()
    labels: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    mode: Literal["review", "send"] = "review"


def _input_handler(**overrides):
    return _catalog(_module(
        KIND="scout", EFFECT_MODE=None, PARAMETERS={"build": Inputs},
        build=lambda ctx: NeedsSkill("fake_tool", payload={"message": ctx.parameters.message}),
        **overrides), kind="external").get("phase", "work")


def test_new_parameter_model_requires_no_core_option_registry():
    handler = _input_handler()
    parameters = handler.parse_parameters(values={
        "message": "123", "count": "3", "enabled": "true",
        "recipients": '["a@example.invalid"]', "labels": '{"lane":[1,2]}',
    }, cli=True)
    assert isinstance(parameters, Inputs)
    assert parameters.message == "123" and parameters.count == 3 and parameters.enabled is True
    assert parameters.recipients == ("a@example.invalid",)
    assert parameters.labels == {"lane": (1, 2)}
    with pytest.raises(TypeError):
        parameters.labels["lane"] = (3,)
    assert handler.build(replace(_context(handler), parameters=parameters)).payload["message"] == "123"


@pytest.mark.parametrize("values", [
    {"count": True}, {"count": "3"}, {"count": 3.5}, {"enabled": 1},
    {"message": None}, {"recipients": "a@example.invalid"},
    {"recipients": [1]}, {"labels": {"lane": [True]}}, {"mode": "unknown"},
])
def test_strict_parameter_types_including_nested_values(values):
    with pytest.raises(ParameterError, match=r"phase.work build"):
        _input_handler().parse_parameters(values=values)


@pytest.mark.parametrize("values", [
    {"count": '"3"'}, {"enabled": "False"}, {"enabled": "1"},
    {"recipients": "a,b"}, {"recipients": '[1]'}, {"labels": '{"lane":[null]}'},
])
def test_cli_non_text_parameters_require_correct_json_types(values):
    with pytest.raises(ParameterError, match=r"phase.work build"):
        _input_handler().parse_parameters(values=values, cli=True)


@pytest.mark.parametrize("parameter", ["variant", "members_file", "engineer", "oof", "comment", "typo"])
def test_irrelevant_known_and_unknown_names_are_not_global_parameters(parameter):
    handler = _catalog(_module(), effect_mode="read_only").get("phase", "work")
    with pytest.raises(ParameterError, match=f"unknown parameter.*{parameter}"):
        handler.parse_parameters(values={parameter: "x"})


def test_default_model_has_no_fields_and_required_parameters_fail_without_input():
    handler = _catalog(implementation="dummy").get("phase", "work")
    assert isinstance(handler.parse_parameters(), NoParameters)

    @dataclass(frozen=True)
    class Required:
        ticket: str

    handler = _catalog(_module(PARAMETERS={"build": Required}), effect_mode="read_only").get("phase", "work")
    with pytest.raises(ParameterError, match="missing required parameter: ticket"):
        handler.parse_parameters()
    assert handler.parse_parameters(values={"ticket": "I-123"}).ticket == "I-123"


@pytest.mark.parametrize("default", [True, "1", None])
def test_wrong_typed_defaults_fail_catalog_startup(default):
    @dataclass(frozen=True)
    class WrongDefault:
        count: int = default

    with pytest.raises(WorkflowConfigError, match=r"phase.work build.*count"):
        _catalog(_module(PARAMETERS={"build": WrongDefault}), effect_mode="read_only")


def test_malformed_models_and_unsupported_roles_fail_catalog_startup():
    @dataclass
    class Mutable:
        value: str = ""

    @dataclass(frozen=True)
    class MutableCollection:
        values: list[str] = field(default_factory=list)

    for model in (dict, Mutable, MutableCollection, Inputs()):
        with pytest.raises(WorkflowConfigError, match="parameter"):
            _catalog(_module(PARAMETERS={"build": model}), effect_mode="read_only")
    for declaration in ([], {"typo": Inputs}, {"submit_approval": Inputs}):
        with pytest.raises(WorkflowConfigError, match="PARAMETERS"):
            _catalog(_module(PARAMETERS=declaration), effect_mode="read_only")


def test_model_factory_defaults_and_declarations_are_captured_once():
    source_values = ["before"]

    @dataclass(frozen=True)
    class Model:
        recipients: tuple[str, ...] = field(default_factory=lambda: source_values)

    module = _module(PARAMETERS={"build": Model})
    handler = _catalog(module, effect_mode="read_only").get("phase", "work")
    source_values.append("after")
    module.PARAMETERS["build"] = NoParameters
    assert handler.parse_parameters().recipients == ("before",)
    with pytest.raises(TypeError):
        handler.parameters[HookRole.BUILD] = None


def test_direct_model_instances_are_revalidated_before_hook_entry():
    calls = []
    module = _module(PARAMETERS={"build": Inputs}, build=lambda ctx: calls.append(ctx) or Done())
    handler = _catalog(module, effect_mode="read_only").get("phase", "work")
    with pytest.raises(HandlerContractError, match="count"):
        handler.build(replace(_context(handler), parameters=Inputs(count=True)))
    assert calls == []


def test_typed_input_revalidation_does_not_repeat_post_init_transforms():
    @dataclass(frozen=True)
    class Model:
        message: str = "hello"

        def __post_init__(self):
            object.__setattr__(self, "message", self.message + "!")

    schema = ParameterSchema.compile(Model, "Step phase.work build")
    parsed = schema.parse({"message": "reviewed"})
    assert schema.parse(schema.parse(parsed)).message == parsed.message == "reviewed!"


def test_optional_closure_defaults_are_not_public_parameters():
    result = Done("closed over")
    handler = _catalog(_module(build=lambda context, captured=result: captured),
                       effect_mode="read_only").get("phase", "work")
    assert handler.build(_context(handler)) is result
    with pytest.raises(ParameterError, match="unknown parameter.*captured"):
        handler.parse_parameters(values={"captured": "replacement"})


@pytest.mark.parametrize("role", list(HookRole))
def test_wrong_role_or_wrong_step_context_rejected_before_call(role):
    calls = []
    module = _effect_module(
        build=lambda ctx: calls.append("build") or Done(),
        prepare_effect=lambda ctx: calls.append("prepare") or {},
        execute=lambda ctx: calls.append("execute") or Done(),
        reconcile=lambda ctx: calls.append("reconcile") or Done(),
        authorize_retry=lambda ctx: calls.append("retry") or RetryDecision(True, ""),
    )
    if role in (HookRole.APPROVAL, HookRole.APPROVAL_PREPARE, HookRole.APPROVAL_RECONCILE):
        handler = _catalog(_approval_module(
            submit_approval=lambda ctx: calls.append("approval") or (True, ""),
        ), kind="approval_gate", approval_command="approve-orchestrator-gate").get("phase", "work")
        hook = getattr(handler, role.value)
    else:
        handler = _catalog(module, effect_mode="transactional", effect_recovery="frozen",
                           effect_retry=True).get("phase", "work")
        hook = (handler.build if role == HookRole.BUILD else
                getattr(handler.effect, "prepare" if role == HookRole.PREPARE else role.value))
    ctx = _context(handler, role)
    for wrong in (replace(ctx, step_key="another.step"),
                  replace(ctx, role=HookRole.BUILD if role != HookRole.BUILD else HookRole.RETRY),
                  object()):
        with pytest.raises(HandlerContractError, match=f"phase.work {role.value}"):
            hook(wrong)
    assert calls == []


def test_effect_execution_cannot_take_unfrozen_live_parameters():
    with pytest.raises(WorkflowConfigError, match="frozen effect_input"):
        _catalog(_effect_module(PARAMETERS={"execute": Inputs}),
                 effect_mode="transactional", effect_recovery="frozen")


def test_preparation_and_retry_parameters_are_separate_from_execution_and_build():
    @dataclass(frozen=True)
    class Reason:
        reason: str

    handler = _catalog(_effect_module(
        PARAMETERS={"prepare_effect": Inputs, "authorize_retry": Reason},
        prepare_effect=lambda ctx: {"message": ctx.parameters.message},
    ), effect_mode="transactional", effect_recovery="frozen", effect_retry=True).get("phase", "work")
    assert handler.effect.prepare(_context(handler, HookRole.PREPARE, message="frozen")) == {"message": "frozen"}
    assert handler.effect.authorize_retry(_context(handler, HookRole.RETRY, reason="verified")).detail == "verified"
    for role, values in ((HookRole.RETRY, {"comment": "not a reason"}),
                         (HookRole.BUILD, {"reason": "not a build input"}),
                         (HookRole.EXECUTE, {"message": "changed intent"})):
        with pytest.raises(ParameterError, match="unknown parameter"):
            handler.parse_parameters(role, values)


def test_context_cannot_cross_effect_and_read_only_authority_boundaries():
    handler = _catalog(_effect_module(), effect_mode="transactional",
                       effect_recovery="frozen").get("phase", "work")
    build = _context(handler)
    execute = _context(handler, HookRole.EXECUTE)
    with pytest.raises(HandlerContractError, match="read-only hook"):
        handler.build(replace(build, effect=execute.effect))
    with pytest.raises(HandlerContractError, match="owned effect context"):
        handler.effect.execute(replace(execute, effect=None))
    with pytest.raises(HandlerContractError, match="without approval authority"):
        handler.effect.execute(replace(execute, approval=lambda *_: (True, "")))


def test_real_catalog_compiles_models_without_binding_any_provider(monkeypatch):
    import steps
    import orchestrator.service_adapters as adapters

    def no_io(*args, **kwargs):
        pytest.fail("catalog compilation must not bind or invoke IO")

    monkeypatch.setattr(adapters, "production_services", no_io)
    monkeypatch.setattr(adapters, "production_effects", no_io)
    config = Path(__file__).resolve().parents[1] / "config" / "phases.yaml"
    catalog = HandlerCatalog.compile(
        WorkflowDefinition.compile(yaml.safe_load(config.read_text(encoding="utf-8"))),
        steps.get_step,
    )
    assert len(catalog.handler_by_key) == 64
    assert sum(handler.definition.implementation.value == "dummy"
               for handler in catalog.handler_by_key.values()) == 16
    assert catalog.get("preflight", "notice").parse_parameters(values={"variant": "update"}).variant == "update"
    assert catalog.get("bug_bash", "clone_plans_auth").parse_parameters(
        HookRole.RETRY, {"reason": "verified"}).reason == "verified"
    assert catalog.get("finalize", "gate_watch").parse_parameters(
        HookRole.APPROVAL_PREPARE, {"comment": "reviewed"}).comment == "reviewed"
    with pytest.raises(ParameterError, match="unknown parameter.*variant"):
        catalog.get("preflight", "flight_reminder").parse_parameters(values={"variant": "update"})


@pytest.mark.parametrize("value", [
    {}, None, Done(note=1), Done(updates=[]), Done(updates=(object(),)),
    InProgress(poll_in_min=True), NeedsHuman("ok", attest=1),
    NeedsSkill("tool", payload=[]), NeedsSkill("tool", outbound="yes"),
])
def test_bad_build_results_are_step_and_role_specific(value):
    handler = _catalog(_module(KIND="scout", EFFECT_MODE=None, build=lambda ctx, result=value: result),
                       kind="external").get("phase", "work")
    with pytest.raises(HandlerContractError, match="phase.work build: invalid return"):
        handler.build(_context(handler))


@pytest.mark.parametrize("value", [(1, ""), [True, ""], (True,), (True, None), Done()])
def test_bad_approval_results_cannot_approve(value):
    handler = _catalog(_approval_module(
        submit_approval=lambda ctx: value,
    ), kind="approval_gate", approval_command="approve-orchestrator-gate").get("phase", "work")
    with pytest.raises(HandlerContractError, match="submit_approval: invalid return"):
        handler.submit_approval(_context(handler, HookRole.APPROVAL))


@pytest.mark.parametrize("role", [HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE])
def test_approval_attempts_reject_live_parameters(role):
    with pytest.raises(WorkflowConfigError, match="frozen request"):
        _catalog(_approval_module(PARAMETERS={role: Inputs}), kind="approval_gate",
                 approval_command="approve-orchestrator-gate")


@pytest.mark.parametrize("role, result", [
    (HookRole.PREPARE, []), (HookRole.PREPARE, {"bad": float("nan")}),
    (HookRole.EXECUTE, NeedsHuman("cannot write")),
    (HookRole.RECONCILE, NeedsSkill("cannot write")),
    (HookRole.RETRY, (True, "")),
])
def test_effect_capability_return_contracts(role, result):
    handler = _catalog(_effect_module(**{role.value: lambda ctx: result}),
                       effect_mode="transactional", effect_recovery="frozen",
                       effect_retry=True).get("phase", "work")
    hook = getattr(handler.effect, "prepare" if role == HookRole.PREPARE else role.value)
    with pytest.raises(HandlerContractError, match=f"{role.value}: invalid return"):
        hook(_context(handler, role))


def test_invalid_step_action_params_fail_before_services_evidence_and_guard(tmp_path, monkeypatch):
    calls = []
    module = SimpleNamespace(ID="check", KIND="scout", build=lambda ctx: calls.append(ctx) or Done())
    orch = _orch(tmp_path, lambda p, s: module if s == "check" else None, _config("external"))
    monkeypatch.setattr(orch, "step_action_guard", lambda *_: pytest.fail("must validate parameters first"))
    monkeypatch.setattr("orchestrator.service_adapters.production_services",
                        lambda **_: pytest.fail("must validate parameters before binding IO"))
    with pytest.raises(ParameterError, match="unknown parameter.*variant"):
        prepare_step(SimpleNamespace(phase="phase", step="check", release="r",
                                     param=["variant=update"]), orch.state, orch)
    assert calls == [] and orch.state.steps == {} and orch._evidence_sessions == {}


@pytest.mark.parametrize("pairs", [["=value"], ["x=1", "x=2"], ["missing-equals"]])
def test_cli_rejects_empty_duplicate_and_malformed_names(pairs):
    with pytest.raises(ValueError, match="--param"):
        _parse_params(pairs)


def test_changed_parameters_change_the_exact_notification_review_hash(tmp_path):
    module = SimpleNamespace(
        ID="check", KIND="scout", NOTIFICATION=True, PARAMETERS={"build": Inputs},
        build=lambda ctx: NeedsSkill(
            "workiq_send_chat_message", payload={"chatId": "verified-chat", "content": ctx.parameters.message},
            record_as="check", outbound=True),
    )
    orch = _orch(tmp_path, lambda p, s: module if s == "check" else None, _config("external"))
    outputs = [prepare_step(SimpleNamespace(
        phase="phase", step="check", release="r", param=[f"message={message}"]
    ), orch.state, orch) for message in ("first", "different")]
    assert outputs[0]["notifications"][0]["hash"] != outputs[1]["notifications"][0]["hash"]
    assert all(not output["permission_to_send"] for output in outputs)
    assert orch.state.notification_deliveries == {}
