"""Capability-specific module and hook protocols, checked at invocation boundaries."""
from __future__ import annotations

from dataclasses import replace
from enum import Enum
import json
from typing import Mapping, Protocol, TYPE_CHECKING, get_args

from .parameters import ParameterSchema

if TYPE_CHECKING:
    from .approvals import ApprovalRequest
    from .evidence import RetryDecision
    from .outcomes import AutoOutcome, Blocked, Outcome
    from .step_context import StepContext


class HookRole(str, Enum):
    BUILD = "build"
    PREPARE = "prepare_effect"
    EXECUTE = "execute"
    RECONCILE = "reconcile"
    APPROVAL = "submit_approval"
    APPROVAL_PREPARE = "prepare_approval"
    APPROVAL_RECONCILE = "reconcile_approval"
    RETRY = "authorize_retry"


class BuildHook(Protocol):
    def __call__(self, context: StepContext) -> Outcome: ...


class AutoHook(Protocol):
    def __call__(self, context: StepContext) -> AutoOutcome: ...


class PrepareHook(Protocol):
    def __call__(self, context: StepContext) -> dict | Blocked: ...


class RetryHook(Protocol):
    def __call__(self, context: StepContext) -> RetryDecision: ...


class ApprovalHook(Protocol):
    def __call__(self, context: StepContext) -> tuple[bool, str]: ...


class ApprovalPort(Protocol):
    def __call__(self) -> tuple[bool, str]: ...


class ApprovalPrepareHook(Protocol):
    def __call__(self, context: StepContext) -> ApprovalRequest | Blocked: ...


class HandlerModule(Protocol):
    ID: str
    KIND: str
    build: BuildHook


class ParameterizedModule(Protocol):
    PARAMETERS: Mapping[str, type]


class AutoModule(HandlerModule, Protocol):
    EFFECT_MODE: str
    build: AutoHook


class EffectModule(AutoModule, Protocol):
    EFFECT_RECOVERY: str
    prepare_effect: PrepareHook
    execute: AutoHook


class TransactionalModule(EffectModule, Protocol):
    reconcile: AutoHook


class RetryableModule(TransactionalModule, Protocol):
    authorize_retry: RetryHook


class ApprovalModule(HandlerModule, Protocol):
    APPROVAL_COMMAND: str
    prepare_approval: ApprovalPrepareHook
    submit_approval: ApprovalHook
    reconcile_approval: ApprovalHook


class HandlerContractError(TypeError, ValueError):
    """A configured capability was invoked with incompatible input or output."""


def _updates(value):
    from .evidence import EvidenceUpdate
    updates = value.updates
    if not isinstance(updates, tuple) or any(not isinstance(item, get_args(EvidenceUpdate)) for item in updates):
        raise TypeError("evidence updates must be a tuple of typed EvidenceUpdate values")


def validate_result(value, role, *, auto=False):
    from .evidence import RetryDecision
    from .outcomes import Done, Blocked, InProgress, NeedsHuman, NeedsSkill, require_auto_outcome, as_dict

    if role in (HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE):
        if not isinstance(value, tuple) or len(value) != 2 or type(value[0]) is not bool or not isinstance(value[1], str):
            raise TypeError("approval must return (bool, str)")
        return value
    if role == HookRole.APPROVAL_PREPARE:
        from .approvals import ApprovalRequest
        if isinstance(value, ApprovalRequest):
            return value
        if not isinstance(value, Blocked):
            raise TypeError("prepare_approval must return ApprovalRequest or Blocked")
    if role == HookRole.RETRY:
        if not isinstance(value, RetryDecision) or type(value.allowed) is not bool or not isinstance(value.detail, str):
            raise TypeError("retry must return RetryDecision")
        _updates(value)
        return value
    if role == HookRole.PREPARE:
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("effect input keys must be strings")
            json.dumps(value, allow_nan=False)
            return value
        if not isinstance(value, Blocked):
            raise TypeError("prepare_effect must return dict or Blocked")
    if auto or role in (HookRole.PREPARE, HookRole.EXECUTE, HookRole.RECONCILE):
        require_auto_outcome(value)
    elif isinstance(value, (Done, Blocked, InProgress)):
        require_auto_outcome(value)
    elif isinstance(value, NeedsHuman):
        if value.kind != "needs_human" or not isinstance(value.prompt, str) or type(value.attest) is not bool:
            raise TypeError("NeedsHuman requires canonical kind, text prompt and boolean attest")
    elif isinstance(value, NeedsSkill):
        for name in ("record_as", "summary", "note"):
            if not isinstance(getattr(value, name), str):
                raise TypeError(f"NeedsSkill {name} must be text")
        if (value.kind != "needs_skill" or not isinstance(value.tool, str) or not value.tool
                or not isinstance(value.payload, dict) or not isinstance(value.notification, dict)
                or type(value.outbound) is not bool):
            raise TypeError("NeedsSkill requires canonical kind, tool, payload and typed metadata")
    else:
        raise TypeError(f"expected canonical Outcome, got {type(value).__name__}")
    _updates(value)
    json.dumps(as_dict(value), allow_nan=False)
    return value


def bind_hook(function, *, key, role, schema: ParameterSchema, auto=False):
    """Capture the callable once; validate before entering its IO-capable body."""
    def invoke(context):
        from .step_context import StepContext, ApprovalContext
        try:
            if not isinstance(context, StepContext):
                raise TypeError("expected StepContext")
            if context.step_key != key or context.role != role:
                raise TypeError(f"expected context for {key}/{role.value}, got {context.step_key}/{context.role}")
            if type(context.parameters) is not schema.model:
                raise TypeError(f"expected parameter model {schema.model.__name__}")
            if role in (HookRole.EXECUTE, HookRole.RECONCILE):
                if context.effect is None or context.approval is not None:
                    raise TypeError("effect hook requires owned effect context, without approval authority")
            elif role in (HookRole.APPROVAL, HookRole.APPROVAL_RECONCILE):
                if not isinstance(context.approval, ApprovalContext) or context.effect is not None:
                    raise TypeError("approval hook requires approval authority only")
                if role == HookRole.APPROVAL and not callable(context.approval.submit):
                    raise TypeError("submission requires a live approval permit")
                if role == HookRole.APPROVAL_RECONCILE and context.approval.submit is not None:
                    raise TypeError("approval reconciliation cannot receive write authority")
            elif context.effect is not None or context.approval is not None:
                raise TypeError("read-only hook cannot receive effect or approval authority")
            context = replace(context, parameters=schema.parse(context.parameters))
        except (TypeError, ValueError) as exc:
            raise HandlerContractError(f"Step {key} {role.value}: {exc}") from exc
        value = function(context)
        try:
            return validate_result(value, role, auto=auto)
        except (TypeError, ValueError, AttributeError) as exc:
            raise HandlerContractError(f"Step {key} {role.value}: invalid return: {exc}") from exc
    return invoke
