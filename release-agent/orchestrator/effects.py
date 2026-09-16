"""Execution policy for in-process auto handlers that touch external systems."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import json

from orchestrator.outcomes import AutoOutcome, Blocked
from orchestrator.handler_contracts import AutoHook, PrepareHook, RetryHook

class EffectMode(str, Enum):
    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    TRANSACTIONAL = "transactional"

    @property
    def writes_external_state(self) -> bool:
        return self is not EffectMode.READ_ONLY


class EffectRecovery(str, Enum):
    FROZEN = "frozen"
    MATCH_CURRENT = "match_current"


@dataclass(frozen=True)
class EffectHandler:
    """Validated effect hooks bound for one configured step."""
    step_id: str
    mode: EffectMode
    recovery: EffectRecovery
    prepare: PrepareHook
    execute: AutoHook
    reconcile: AutoHook | None = None
    authorize_retry: RetryHook | None = None


def prepare(handler: EffectHandler, context, frozen_mocks=None):
    """Resolve and validate the immutable provider input before reserving a write."""
    value = handler.prepare(context)
    if isinstance(value, Blocked):
        return value
    if not isinstance(value, dict):
        raise ValueError("Effect handler prepare_effect() must return a mapping or Blocked")
    frozen_mocks = {
        key: deepcopy(item)
        for key, item in (frozen_mocks or {}).items()
        if key not in ("outcome", "note", "reason")
    }
    if frozen_mocks:
        value = deepcopy(value)
        value["__effect_mocks__"] = frozen_mocks
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Effect input must be JSON serializable") from exc
    key = input_key(handler.step_id, value, encoded=encoded)
    return {"operation_key": key, "input": value}


def input_key(step_id: str, value: dict, *, encoded: str = None) -> str:
    if encoded is None:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    return hashlib.sha256(f"{step_id}:{encoded}".encode("utf-8")).hexdigest()


def execution_input_is_valid(step_id: str, execution: dict) -> bool:
    value = execution.get("effect_input")
    if not isinstance(value, dict):
        return False
    try:
        return execution.get("operation_key") == input_key(step_id, value)
    except (TypeError, ValueError):
        return False


def invoke(
    handler: EffectHandler, context, *, recovering: bool
) -> AutoOutcome:
    """Execute once, or use the mode's declared recovery strategy after interruption."""
    if recovering and handler.mode is EffectMode.TRANSACTIONAL:
        if handler.reconcile is None:
            raise ValueError(f"Transactional effect {handler.step_id} has no reconcile hook")
        return handler.reconcile(context)
    return handler.execute(context)
