"""Typed evidence mutations; lifecycle and execution ownership are never writable."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from .step_context import freeze, thaw


def _values(value):
    value = dict(value)
    json.dumps(thaw(value), allow_nan=False)
    return freeze(value)


@dataclass(frozen=True)
class StepData:
    values: Mapping

    def __post_init__(self):
        object.__setattr__(self, "values", _values(self.values))


@dataclass(frozen=True)
class PipelineEvidence:
    values: Mapping

    def __post_init__(self):
        object.__setattr__(self, "values", _values(self.values))


@dataclass(frozen=True)
class ReleaseVersions:
    values: Mapping

    def __post_init__(self):
        object.__setattr__(self, "values", _values(self.values))


@dataclass(frozen=True)
class BrokerResource:
    values: Mapping

    def __post_init__(self):
        object.__setattr__(self, "values", _values(self.values))


@dataclass(frozen=True)
class UIFailureReminder:
    """The UI producer's contribution, not a replacement for the human's record."""
    note: str
    links: tuple
    broker_count: int
    failed_ids: tuple
    auth_failures: tuple = ()

    def __post_init__(self):
        for name in ("links", "failed_ids", "auth_failures"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


EvidenceUpdate = StepData | PipelineEvidence | ReleaseVersions | BrokerResource | UIFailureReminder


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    detail: str
    updates: tuple[EvidenceUpdate, ...] = ()

    def __post_init__(self):
        if type(self.allowed) is not bool or not isinstance(self.detail, str):
            raise TypeError("Effect retry verification requires a boolean and text")
        if not isinstance(self.updates, tuple):
            raise TypeError("Effect retry evidence must be a tuple")
