"""Catalog contracts using only in-memory handlers; no provider or global mocks."""
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace

import pytest

from orchestrator.effects import EffectMode
from orchestrator.authority import WriteOperation
from orchestrator.handlers import HandlerCatalog
from orchestrator.outcomes import Done, NeedsHuman, NeedsSkill
from orchestrator.evidence import RetryDecision
from orchestrator.step_context import ApprovalContext, Clock, EffectContext, EvidenceView, ReleaseView, StepContext
from orchestrator.approvals import ApprovalRequest
from orchestrator.handler_contracts import HookRole
from orchestrator.services import EffectServices
from orchestrator.workflow import (
    StepImplementation,
    WorkflowConfigError,
    WorkflowDefinition,
)


def _config(**step):
    return {
        "phases": [{
            "id": "phase",
            "name": "Test phase",
            "steps": [{"id": "work", "name": "Test operation", "kind": "auto", **step}],
        }],
    }


def _workflow(**step):
    return WorkflowDefinition.compile(_config(**step))


def _module(**attributes):
    return SimpleNamespace(
        **{
            "ID": "work",
            "KIND": "agent",
            "EFFECT_MODE": "read_only",
            "build": lambda state: Done("original"),
            **attributes,
        }
    )


def _approval_module(**attributes):
    return _module(**{
        "KIND": "gate", "EFFECT_MODE": None, "APPROVAL_COMMAND": "approve-orchestrator-gate",
        "WRITES": (WriteOperation.SUBMIT_PIPELINE_APPROVAL,),
        "prepare_approval": lambda _: _approval_request(),
        "submit_approval": lambda ctx: ctx.approval.submit(),
        "reconcile_approval": lambda _: (False, "pending"),
        **attributes,
    })


def _approval_request():
    return ApprovalRequest("https://dev.azure.com/example", "project", 1, "Publish", "approval", "approved")


def _catalog(module=None, **step):
    return HandlerCatalog.compile(_workflow(**step), lambda phase, sid: module)


@dataclass(frozen=True)
class VariantParameters:
    variant: str | None = None


@dataclass(frozen=True)
class CommentParameters:
    comment: str = ""


@dataclass(frozen=True)
class RetryParameters:
    reason: str = ""


def _context(handler, role=HookRole.BUILD, **parameters):
    effect = (EffectContext({}, None, EffectServices())
              if role in (HookRole.EXECUTE, HookRole.RECONCILE) else None)
    return StepContext(
        ReleaseView("r", None, None, None, None, "UTC"),
        EvidenceView(), Clock(datetime.now(timezone.utc)), None,
        parameters=handler.parse_parameters(role, parameters), role=role,
        step_key=handler.definition.key, effect=effect,
        approval=(ApprovalContext("execution", _approval_request(),
                                  (lambda: (True, "")) if role == HookRole.APPROVAL else None)
                  if role in (HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE) else None),
    )


def _effect_module(mode="transactional", **attributes):
    return _module(**{
        "EFFECT_MODE": mode,
        "EFFECT_RECOVERY": "frozen",
        "prepare_effect": lambda state: {"original": True},
        "execute": lambda context: Done("executed"),
        "reconcile": lambda context: Done("reconciled"),
        "authorize_retry": lambda context: RetryDecision(True, context.parameters.reason),
        **attributes,
    })


def test_missing_real_auto_handler_fails_closed():
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*requires a handler module"):
        _catalog(effect_mode="read_only")


@pytest.mark.parametrize("kind", ["external", "attestation", "approval_gate"])
def test_missing_external_attestation_or_external_gate_handler_rejected(kind):
    step = {"kind": kind}
    if kind == "approval_gate":
        step["approval_command"] = "approve-orchestrator-gate"
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*requires a handler module"):
        _catalog(**step)


def test_dummy_is_explicit_labeled_noop():
    handler = _catalog(implementation="dummy").get("phase", "work")
    outcome = handler.build(_context(handler))
    assert handler.definition.implementation == StepImplementation.DUMMY
    assert handler.definition.effect_mode == EffectMode.READ_ONLY
    assert isinstance(outcome, Done)
    assert "[DUMMY]" in outcome.note and "Test operation" in outcome.note
    assert "no operation performed" in outcome.note
    assert handler.effect is None and handler.submit_approval is None


@pytest.mark.parametrize("kind", ["approval_gate", "external", "attestation", "human_action"])
def test_dummy_cannot_replace_human_or_external_steps(kind):
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*dummy.*read_only auto"):
        _workflow(kind=kind, implementation="dummy")


@pytest.mark.parametrize("mode", ["idempotent", "transactional"])
def test_dummy_cannot_declare_external_effects(mode):
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*dummy.*read_only auto"):
        _workflow(implementation="dummy", effect_mode=mode, effect_recovery="frozen")


@pytest.mark.parametrize("value", ["stub", "", None, True, 1])
def test_invalid_implementation_is_rejected(value):
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*implementation"):
        _workflow(implementation=value)


def test_dummy_with_module_is_contradictory():
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*dummy.*handler module"):
        _catalog(_module(), implementation="dummy")


@pytest.mark.parametrize("kind", ["human_action", "approval_gate"])
def test_builtin_human_and_local_gate_only_prompt(kind):
    handler = _catalog(kind=kind).get("phase", "work")
    outcome = handler.build(_context(handler))
    assert isinstance(outcome, NeedsHuman)
    assert outcome.prompt == "Test operation"
    assert not outcome.attest
    assert handler.effect is None and handler.submit_approval is None
    assert handler.definition.implementation == StepImplementation.HANDLER


@pytest.mark.parametrize(
    "attributes, message",
    [
        ({"ID": "different"}, "module ID"),
        ({"ID": None}, "module ID"),
        ({"KIND": "scout"}, "module KIND"),
        ({"build": None}, "build"),
        ({"build": lambda: Done()}, "build"),
        ({"EFFECT_MODE": "transactional"}, "effect_mode"),
        ({"EFFECT_RECOVERY": "frozen"}, "effect_recovery"),
        ({"APPROVAL_COMMAND": "approve-orchestrator-gate"}, "approval_command"),
        ({"WRITE_COMMAND": "create-payload-wiki"}, "write_command"),
    ],
)
def test_module_contract_mismatch_rejected(attributes, message):
    with pytest.raises(WorkflowConfigError, match=message) as error:
        _catalog(_module(**attributes), effect_mode="read_only")
    assert "phase.work" in str(error.value)


def test_real_auto_requires_explicit_effect_mode():
    with pytest.raises(WorkflowConfigError, match="declare effect_mode explicitly"):
        _catalog(_module())


@pytest.mark.parametrize(
    "hook, value",
    [
        ("prepare_effect", None),
        ("execute", None),
        ("reconcile", None),
        ("authorize_retry", None),
        ("prepare_effect", lambda: {}),
        ("execute", lambda context, execution: Done()),
        ("reconcile", lambda context, execution: Done()),
        ("authorize_retry", lambda state, execution: (True, "")),
    ],
)
def test_required_effect_and_retry_hooks_are_validated(hook, value):
    with pytest.raises(WorkflowConfigError, match=hook):
        _catalog(
            _effect_module(**{hook: value}),
            effect_mode="transactional",
            effect_recovery="frozen",
            effect_retry=True,
        )


def test_idempotent_effect_does_not_require_transactional_hooks():
    handler = _catalog(
        _effect_module("idempotent", reconcile=None, authorize_retry=None),
        effect_mode="idempotent",
        effect_recovery="frozen",
    ).get("phase", "work")
    assert handler.effect.mode == EffectMode.IDEMPOTENT
    assert handler.effect.reconcile is None
    assert handler.effect.authorize_retry is None


def test_effect_recovery_must_match_module():
    with pytest.raises(WorkflowConfigError, match="effect_recovery"):
        _catalog(
            _effect_module(),
            effect_mode="transactional",
            effect_recovery="match_current",
        )


@pytest.mark.parametrize("submit", [None, lambda context, comment: (True, "")])
def test_external_gate_requires_approval_hook(submit):
    module = _module(
        KIND="gate", EFFECT_MODE=None,
        APPROVAL_COMMAND="approve-orchestrator-gate", submit_approval=submit,
    )
    with pytest.raises(WorkflowConfigError, match="submit_approval"):
        _catalog(
            module, kind="approval_gate", approval_command="approve-orchestrator-gate"
        )


def test_declared_external_write_command_requires_matching_capability():
    with pytest.raises(WorkflowConfigError, match="write_command"):
        _catalog(
            _module(KIND="scout", EFFECT_MODE=None),
            kind="external", write_command="create-payload-wiki",
        )


def test_external_build_reads_typed_parameters():
    def build(context):
        return NeedsSkill("test_tool", payload={"variant": context.parameters.variant})

    handler = _catalog(
        _module(KIND="scout", EFFECT_MODE=None, build=build,
                PARAMETERS={"build": VariantParameters}), kind="external"
    ).get("phase", "work")
    assert handler.build(_context(handler, variant="custom")).payload == {"variant": "custom"}


def test_catalog_only_resolves_configured_steps_and_rejects_unknown_get():
    modules = {"phase.work": _module(), "unconfigured.unused": object()}
    calls = []

    def resolve(phase_id, step_id):
        key = f"{phase_id}.{step_id}"
        calls.append(key)
        return modules.get(key)

    catalog = HandlerCatalog.compile(_workflow(effect_mode="read_only"), resolve)
    assert calls == ["phase.work"]
    assert tuple(catalog.handler_by_key) == ("phase.work",)
    with pytest.raises(WorkflowConfigError, match="No configured step: phase.missing"):
        catalog.get("phase", "missing")
    with pytest.raises(TypeError):
        catalog.handler_by_key["phase.other"] = catalog.get("phase", "work")
    with pytest.raises(FrozenInstanceError):
        catalog.handler_by_key = {}


def test_bound_build_and_nested_metadata_are_captured_once():
    class Source:
        ID = "work"
        KIND = "agent"
        EFFECT_MODE = "read_only"
        NOTIFICATION = True

        def build(self, state):
            return Done("original bound method")

    source = Source()
    nested = {"kind": "payload", "sets": "to", "values": [{"emails": ["one"]}]}
    source.MOCKABLE = MappingProxyType({"send_to": nested})
    source.CONFIG = {"fire_at_local": "09:30"}
    handler = _catalog(source, effect_mode="read_only").get("phase", "work")
    source.build = lambda state: Done("replacement")
    source.NOTIFICATION = False
    source.CONFIG["fire_at_local"] = "22:00"
    nested["kind"] = "input"
    nested["values"][0]["emails"].append("two")
    source.MOCKABLE = {}
    assert handler.build(_context(handler)).note == "original bound method"
    assert handler.notification is True
    assert handler.fire_at_local == "09:30"
    assert handler.mockable["send_to"]["kind"] == "payload"
    assert handler.mockable["send_to"]["values"][0]["emails"] == ("one",)
    with pytest.raises(TypeError):
        handler.mockable["send_to"]["kind"] = "input"
    with pytest.raises(FrozenInstanceError):
        handler.build = source.build
    assert not hasattr(handler, "module")


def test_effect_hooks_are_captured_once():
    source = _effect_module(PARAMETERS={"authorize_retry": RetryParameters})
    handler = _catalog(
        source, effect_mode="transactional", effect_recovery="frozen", effect_retry=True
    ).get("phase", "work")
    effect = handler.effect
    source.prepare_effect = lambda state: {"replacement": True}
    source.execute = lambda state, execution: Done("replacement")
    source.reconcile = lambda state, execution: Done("replacement")
    source.authorize_retry = lambda state, execution, reason: (False, "replacement")
    source.ID = "replacement"
    source.EFFECT_MODE = "read_only"
    assert effect.step_id == "work"
    assert effect.mode == EffectMode.TRANSACTIONAL
    assert effect.prepare(_context(handler, HookRole.PREPARE)) == {"original": True}
    assert effect.execute(_context(handler, HookRole.EXECUTE)).note == "executed"
    assert effect.reconcile(_context(handler, HookRole.RECONCILE)).note == "reconciled"
    assert effect.authorize_retry(_context(handler, HookRole.RETRY, reason="retry")) == RetryDecision(True, "retry")
    with pytest.raises(FrozenInstanceError):
        effect.execute = source.execute
    assert not hasattr(effect, "module")


def test_approval_hook_is_captured_once():
    source = _approval_module(
        submit_approval=lambda context: (True, context.approval.request.comment),
        PARAMETERS={"prepare_approval": CommentParameters},
    )
    handler = _catalog(
        source, kind="approval_gate", approval_command="approve-orchestrator-gate"
    ).get("phase", "work")
    source.submit_approval = lambda state, comment: (False, "replacement")
    assert handler.submit_approval(_context(handler, HookRole.APPROVAL)) == (True, "approved")


@pytest.mark.parametrize("hook", ["prepare_approval", "submit_approval", "reconcile_approval"])
def test_external_gate_requires_each_recovery_hook(hook):
    with pytest.raises(WorkflowConfigError, match=hook):
        _catalog(_approval_module(**{hook: None}), kind="approval_gate",
                 approval_command="approve-orchestrator-gate")


def test_workflow_metadata_is_detached_and_recursively_frozen():
    config = _config(implementation="dummy", maps_to=["one"])
    workflow = WorkflowDefinition.compile(config)
    catalog = HandlerCatalog.compile(workflow, lambda phase, step: None)
    config["phases"][0]["steps"][0]["name"] = "Replacement"
    config["phases"][0]["steps"][0]["maps_to"].append("two")
    handler = catalog.get("phase", "work")
    assert "Test operation" in handler.build(_context(handler)).note
    assert handler.definition.raw["maps_to"] == ("one",)
    assert workflow.phases[0].raw["steps"][0]["name"] == "Test operation"
    with pytest.raises(TypeError):
        handler.definition.raw["name"] = "Replacement"


@pytest.mark.parametrize("time", [None, "", "9:00", "24:00", "23:60", "09:00:00", "09:00\n", 900])
def test_declared_malformed_fire_time_is_rejected(time):
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*fire_at_local"):
        _catalog(_module(CONFIG={"fire_at_local": time}), effect_mode="read_only")


@pytest.mark.parametrize("time", ["00:00", "09:00", "23:59"])
def test_valid_fire_time(time):
    handler = _catalog(
        _module(CONFIG=MappingProxyType({"fire_at_local": time})),
        effect_mode="read_only",
    ).get("phase", "work")
    assert handler.fire_at_local == time


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_notification_flag_must_be_boolean(value):
    with pytest.raises(WorkflowConfigError, match=r"phase\.work.*NOTIFICATION"):
        _catalog(_module(NOTIFICATION=value), effect_mode="read_only")


def _discovery_package(tmp_path, monkeypatch, modules):
    import steps

    phase_dir = tmp_path / "phase"
    phase_dir.mkdir()
    (phase_dir / "__init__.py").touch()
    for name in modules:
        (phase_dir / f"{name}.py").touch()
    monkeypatch.setattr(steps, "_PKG_DIR", str(tmp_path))
    monkeypatch.setattr(steps, "_CACHE", None)
    monkeypatch.setattr(
        steps, "import_module",
        lambda name: modules[name.rsplit(".", 1)[-1]],
    )
    return steps


def test_discovery_reports_duplicate_ids(tmp_path, monkeypatch):
    steps = _discovery_package(tmp_path, monkeypatch, {
        "one": _module(__name__="steps.phase.one"),
        "two": _module(__name__="steps.phase.two"),
    })
    with pytest.raises(RuntimeError, match=r"duplicate.*phase\.work"):
        steps.discover()


def test_discovery_rejects_id_bearing_module_without_build(tmp_path, monkeypatch):
    steps = _discovery_package(tmp_path, monkeypatch, {
        "broken": SimpleNamespace(ID="work", __name__="steps.phase.broken"),
    })
    with pytest.raises(RuntimeError, match=r"phase\.work.*build"):
        steps.discover()


def test_discovery_ignores_helpers_without_id(tmp_path, monkeypatch):
    module = _module(__name__="steps.phase.work")
    steps = _discovery_package(tmp_path, monkeypatch, {
        "helper": SimpleNamespace(__name__="steps.phase.helper"),
        "work": module,
    })
    assert steps.discover() == {"phase.work": module}
