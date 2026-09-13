"""Immutable, validated capabilities for the configured workflow steps."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import inspect
import re
from types import MappingProxyType
from typing import Callable, Mapping

from orchestrator.effects import EffectHandler, EffectMode
from orchestrator.authority import (
    EvidenceAuthority, OwnStepData, UIFailureContribution,
    WriteCapabilities, WriteOperation,
)
from orchestrator.evidence import BrokerResource, StepData
from orchestrator.handler_contracts import ApprovalHook, ApprovalPrepareHook, BuildHook, HookRole, bind_hook
from orchestrator.outcomes import NeedsHuman
from orchestrator.parameters import NoParameters, ParameterError, ParameterSchema
from orchestrator.workflow import (
    StepDefinition,
    StepImplementation,
    StepKind,
    WorkflowConfigError,
    WorkflowDefinition,
    _freeze,
)


HandlerResolver = Callable[[str, str], object | None]


@dataclass(frozen=True)
class StepHandler:
    definition: StepDefinition
    build: BuildHook
    effect: EffectHandler | None
    submit_approval: ApprovalHook | None
    prepare_approval: ApprovalPrepareHook | None
    reconcile_approval: ApprovalHook | None
    notification: bool
    mockable: Mapping
    fire_at_local: str | None
    parameters: Mapping[HookRole, ParameterSchema]
    evidence: EvidenceAuthority
    writes: WriteCapabilities
    status_email: bool

    def parse_parameters(self, role=HookRole.BUILD, values=None, *, cli=False):
        try:
            role = HookRole(role)
            schema = self.parameters[role]
        except (ValueError, KeyError) as exc:
            raise ParameterError(f"Step {self.definition.key}: unsupported hook role {role!r}") from exc
        return schema.parse(values, cli=cli)

    def mockable_spec(self) -> dict:
        """Return a detached, JSON-ready view for payload overrides and display."""
        return {key: _mutable_metadata(value) for key, value in self.mockable.items()}


def _mutable_metadata(value):
    if isinstance(value, Mapping):
        return {key: _mutable_metadata(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_mutable_metadata(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True)
class HandlerCatalog:
    handler_by_key: Mapping[str, StepHandler]

    @classmethod
    def compile(
        cls, workflow: WorkflowDefinition, resolver: HandlerResolver
    ) -> "HandlerCatalog":
        handlers = {}
        for step in workflow.step_by_key.values():
            handlers[step.key] = _compile_handler(step, resolver(step.phase_id, step.id))
        _validate_evidence_targets(workflow, handlers)
        return cls(handler_by_key=MappingProxyType(handlers))

    def get(self, phase_id: str, step_id: str) -> StepHandler:
        key = f"{phase_id}.{step_id}"
        try:
            return self.handler_by_key[key]
        except KeyError:
            raise WorkflowConfigError(f"No configured step: {key}") from None


def _compile_handler(step: StepDefinition, module: object | None) -> StepHandler:
    if step.implementation == StepImplementation.DUMMY:
        if module is not None:
            raise WorkflowConfigError(
                f"Step {step.key} declares dummy implementation but has a handler module."
            )
        from phases.stub_runner import build as build_dummy

        def build(context):
            return build_dummy(step.raw)

        return _builtin_handler(step, build)

    if module is None:
        if step.kind == StepKind.HUMAN_ACTION or (
            step.kind == StepKind.APPROVAL_GATE and not step.approval_command
        ):
            def build(context):
                return NeedsHuman(step.name)

            return _builtin_handler(step, build)
        raise WorkflowConfigError(
            f"Step {step.key} ({step.kind.value}) requires a handler module."
        )

    module_id = getattr(module, "ID", None)
    if module_id != step.id:
        raise WorkflowConfigError(
            f"Step {step.key} requires module ID {step.id!r}, got {module_id!r}."
        )
    expected_kind = {
        StepKind.AUTO: "agent",
        StepKind.EXTERNAL: "scout",
        StepKind.HUMAN_ACTION: "human",
        StepKind.ATTESTATION: "attest",
        StepKind.APPROVAL_GATE: "gate",
    }[step.kind]
    actual_kind = getattr(module, "KIND", None)
    if actual_kind != expected_kind:
        raise WorkflowConfigError(
            f"Step {step.key} kind {step.kind.value!r} requires module KIND "
            f"{expected_kind!r}, got {actual_kind!r}."
        )
    schemas = _compile_parameters(step, module)
    build = _bound_callable(module, step, HookRole.BUILD, schemas)
    effect = _compile_effect(step, module, schemas)
    submit_approval = None
    prepare_approval = reconcile_approval = None
    if step.approval_command:
        submit_approval = _bound_callable(module, step, HookRole.APPROVAL, schemas)
        prepare_approval = _bound_callable(module, step, HookRole.APPROVAL_PREPARE, schemas)
        reconcile_approval = _bound_callable(module, step, HookRole.APPROVAL_RECONCILE, schemas)
    for name in ("approval_command", "write_command"):
        declared = getattr(step, name)
        capability = getattr(module, name.upper(), None)
        if capability != declared:
            raise WorkflowConfigError(
                f"Step {step.key} {name} {declared!r} does not "
                f"match handler capability {capability!r}."
            )
    evidence, writes = _compile_authority(step, module)

    notification = getattr(module, "NOTIFICATION", False)
    if not isinstance(notification, bool):
        raise WorkflowConfigError(f"Step {step.key} NOTIFICATION must be a boolean.")
    status_email = getattr(module, "STATUS_EMAIL", False)
    if not isinstance(status_email, bool):
        raise WorkflowConfigError(f"Step {step.key} STATUS_EMAIL must be a boolean.")
    mockable = getattr(module, "MOCKABLE", {})
    if not isinstance(mockable, Mapping):
        raise WorkflowConfigError(f"Step {step.key} MOCKABLE must be a mapping.")
    config = getattr(module, "CONFIG", {})
    if not isinstance(config, Mapping):
        raise WorkflowConfigError(f"Step {step.key} CONFIG must be a mapping.")
    fire_at_local = config.get("fire_at_local")
    if "fire_at_local" in config and (
        not isinstance(fire_at_local, str)
        or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", fire_at_local)
    ):
        raise WorkflowConfigError(
            f"Step {step.key} CONFIG.fire_at_local must be a valid HH:MM time."
        )
    return StepHandler(
        definition=step,
        build=build,
        effect=effect,
        submit_approval=submit_approval,
        prepare_approval=prepare_approval,
        reconcile_approval=reconcile_approval,
        notification=notification,
        mockable=_freeze(mockable),
        fire_at_local=fire_at_local,
        parameters=schemas,
        evidence=evidence,
        writes=writes,
        status_email=status_email,
    )


def _builtin_handler(step: StepDefinition, build: BuildHook) -> StepHandler:
    schema = ParameterSchema.compile(NoParameters, f"Step {step.key} build")
    return StepHandler(
        definition=step,
        build=bind_hook(build, key=step.key, role=HookRole.BUILD, schema=schema,
                        auto=step.kind == StepKind.AUTO),
        effect=None,
        submit_approval=None,
        prepare_approval=None,
        reconcile_approval=None,
        notification=False,
        mockable=MappingProxyType({}),
        fire_at_local=None,
        parameters=MappingProxyType({HookRole.BUILD: schema}),
        evidence=EvidenceAuthority(),
        writes=WriteCapabilities(),
        status_email=False,
    )


def _compile_authority(step, module):
    try:
        evidence = EvidenceAuthority(getattr(module, "EVIDENCE", ()))
        writes = WriteCapabilities(getattr(module, "WRITES", ()))
        operations = set(writes.operations)
        approval = WriteOperation.SUBMIT_PIPELINE_APPROVAL
        if step.approval_command:
            if operations != {approval}:
                raise ValueError("external approval requires only SUBMIT_PIPELINE_APPROVAL")
        elif operations:
            if not step.effect_mode or not step.effect_mode.writes_external_state:
                raise ValueError("WRITES requires an effect-capable handler")
            if approval in operations:
                raise ValueError("SUBMIT_PIPELINE_APPROVAL requires an approval hook")
        if WriteOperation.ENSURE_BROKER_PLAN in operations and not evidence.for_update(BrokerResource):
            raise ValueError("ENSURE_BROKER_PLAN requires BrokerPlanEvidence")
        if WriteOperation.CREATE_AUTH_QUERY_SUITE in operations and not evidence.for_update(StepData):
            raise ValueError("CREATE_AUTH_QUERY_SUITE requires OwnStepData")
        if operations.intersection({
            WriteOperation.ENSURE_BROKER_PLAN, WriteOperation.CREATE_AUTH_QUERY_SUITE,
        }) and step.effect_mode != EffectMode.TRANSACTIONAL:
            raise ValueError("Resource creation requires a transactional effect")
        return evidence, writes
    except ValueError as exc:
        raise WorkflowConfigError(f"Step {step.key}: {exc}") from exc


def _validate_evidence_targets(workflow, handlers):
    owners = {}
    for key, handler in handlers.items():
        for scope in handler.evidence.scopes:
            if isinstance(scope, UIFailureContribution):
                target = workflow.step_by_key.get(scope.target)
                if target is None or target.kind != StepKind.HUMAN_ACTION or scope.target == key:
                    raise WorkflowConfigError(
                        f"Step {key}: UI contribution target must be a configured human-review step: {scope.target}")
            # OwnStepData is local; shared evidence has exactly one declared producer.
            if isinstance(scope, OwnStepData):
                continue
            if scope in owners:
                raise WorkflowConfigError(f"Step {key}: evidence scope already owned by {owners[scope]}")
            owners[scope] = key


def _compile_effect(step: StepDefinition, module: object, schemas) -> EffectHandler | None:
    if step.kind == StepKind.AUTO and "effect_mode" not in step.raw:
        raise WorkflowConfigError(
            f"Implemented auto handler {step.key} must declare effect_mode "
            "explicitly in workflow config."
        )
    for name in ("effect_mode", "effect_recovery"):
        declared = getattr(step, name)
        expected = declared.value if declared is not None else None
        capability = getattr(module, name.upper(), None)
        if capability != expected:
            raise WorkflowConfigError(
                f"Step {step.key} {name} {expected!r} does not "
                f"match handler capability {capability!r}."
            )
    if not step.effect_mode or not step.effect_mode.writes_external_state:
        return None
    prepare = _bound_callable(module, step, HookRole.PREPARE, schemas)
    execute = _bound_callable(module, step, HookRole.EXECUTE, schemas)
    reconcile = None
    if step.effect_mode == EffectMode.TRANSACTIONAL:
        reconcile = _bound_callable(module, step, HookRole.RECONCILE, schemas)
    authorize_retry = None
    if step.effect_retry:
        authorize_retry = _bound_callable(module, step, HookRole.RETRY, schemas)
    return EffectHandler(
        step_id=step.id,
        mode=step.effect_mode,
        recovery=step.effect_recovery,
        prepare=prepare,
        execute=execute,
        reconcile=reconcile,
        authorize_retry=authorize_retry,
    )


def _compile_parameters(step, module):
    roles = {HookRole.BUILD}
    if step.effect_mode and step.effect_mode.writes_external_state:
        roles.update((HookRole.PREPARE, HookRole.EXECUTE))
        if step.effect_mode == EffectMode.TRANSACTIONAL:
            roles.add(HookRole.RECONCILE)
        if step.effect_retry:
            roles.add(HookRole.RETRY)
    if step.approval_command:
        roles.update((HookRole.APPROVAL, HookRole.APPROVAL_PREPARE, HookRole.APPROVAL_RECONCILE))
    declaration = getattr(module, "PARAMETERS", {})
    if not isinstance(declaration, Mapping):
        raise WorkflowConfigError(f"Step {step.key} PARAMETERS must map hook roles to frozen dataclass models.")
    unknown = set(declaration) - roles
    if unknown:
        raise WorkflowConfigError(f"Step {step.key} PARAMETERS declares unsupported hook role(s): "
                                  f"{', '.join(sorted(map(str, unknown)))}")
    schemas = {}
    for role in roles:
        try:
            schema = ParameterSchema.compile(
                declaration.get(role, NoParameters), f"Step {step.key} {role.value}"
            )
            if role in (HookRole.EXECUTE, HookRole.RECONCILE) and schema.fields:
                raise ParameterError(f"Step {step.key} {role.value}: effect execution reads only "
                                     "frozen effect_input; declare parameters on prepare_effect instead")
            if role in (HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE) and schema.fields:
                raise ParameterError(f"Step {step.key} {role.value}: approval execution reads only "
                                     "the frozen request; declare parameters on prepare_approval instead")
            schemas[role] = schema
        except ParameterError as exc:
            raise WorkflowConfigError(str(exc)) from exc
    return MappingProxyType(schemas)


def _bound_callable(module, step, role, schemas):
    function = _required_callable(module, step, role.value, ("context",))
    if inspect.iscoroutinefunction(function):
        raise WorkflowConfigError(f"Step handler {step.key} {role.value} must be synchronous.")
    return bind_hook(function, key=step.key, role=role, schema=schemas[role],
                     auto=step.kind == StepKind.AUTO)


def _required_callable(
    module: object, step: StepDefinition, name: str, arguments: tuple[str, ...]
) -> Callable:
    function = getattr(module, name, None)
    contract = f"{name}({', '.join(arguments)})"
    if not callable(function):
        raise WorkflowConfigError(f"Step handler {step.key} must define {contract}.")
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError) as exc:
        raise WorkflowConfigError(
            f"Step handler {step.key} cannot validate {contract}: {exc}"
        ) from exc
    try:
        signature.bind(*([None] * len(arguments)))
    except TypeError as exc:
        raise WorkflowConfigError(
            f"Step handler {step.key} must support {contract}: {exc}"
        ) from exc
    return function
