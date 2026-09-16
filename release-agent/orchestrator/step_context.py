"""Invocation-local handler inputs. Nothing in this module is serialized as state."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Generic, Mapping, Protocol, TYPE_CHECKING, TypeVar

from .handler_contracts import ApprovalPort, HookRole
from .approvals import ApprovalRequest
from .parameters import NoParameters

if TYPE_CHECKING:
    from .evidence import EvidenceUpdate
    from .services import Services, EffectServices


class FrozenDict(dict):
    """JSON-readable immutable mapping, including every nested collection."""
    def _deny(self, *args, **kwargs):
        raise TypeError("Evidence views are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError("Evidence views are immutable")

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo):
        return self


def freeze(value):
    if isinstance(value, dict):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return FrozenList(freeze(item) for item in value)
    return value


def thaw(value):
    """Explicitly detach ordinary data for pure transforms and provider inputs."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ReleaseView:
    release_id: str
    ccd: str | None
    target_month: str | None
    owner_email: str | None
    owner_name: str | None
    timezone: str | None
    versions: Mapping = field(default_factory=FrozenDict)

    def __post_init__(self):
        object.__setattr__(self, "versions", freeze(dict(self.versions)))


@dataclass(frozen=True)
class StepEvidence:
    status: str = "pending"
    note: str = ""
    data: Mapping = field(default_factory=FrozenDict)
    links: tuple = ()
    execution: Mapping | None = None
    invalidated_at: str | None = None
    invalidation_reason: str | None = None
    completed_at: str | None = None
    by: str | None = None

    def __post_init__(self):
        for name in ("data", "links", "execution"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True)
class EvidenceView:
    steps: Mapping[str, StepEvidence] = field(default_factory=FrozenDict)
    pipeline_runs: Mapping = field(default_factory=FrozenDict)
    resources: Mapping = field(default_factory=FrozenDict)
    notification_deliveries: Mapping = field(default_factory=FrozenDict)

    def __post_init__(self):
        for name in ("steps", "pipeline_runs", "resources", "notification_deliveries"):
            object.__setattr__(self, name, freeze(dict(getattr(self, name))))

    def step(self, phase: str, step: str) -> StepEvidence:
        return self.steps.get(f"{phase}.{step}", StepEvidence())

    def completed(self, phase: str, step: str) -> bool:
        record = self.step(phase, step)
        return record.status in ("done", "skipped")


@dataclass(frozen=True)
class Clock:
    instant: datetime

    def now(self) -> datetime:
        return self.instant

    def utc(self) -> datetime:
        return self.instant.astimezone(timezone.utc)

    def iso(self) -> str:
        return self.utc().isoformat()


class DurableEvidence(Protocol):
    def commit(self, update: EvidenceUpdate) -> EvidenceView:
        """Return a fresh immutable snapshot only after a successful durable save."""
        ...

    def read(self) -> EvidenceView: ...


@dataclass(frozen=True)
class EffectContext:
    execution: Mapping
    commit: DurableEvidence
    services: EffectServices

    def __post_init__(self):
        object.__setattr__(self, "execution", freeze(dict(self.execution)))


@dataclass(frozen=True)
class ApprovalContext:
    execution_id: str
    request: ApprovalRequest
    submit: ApprovalPort | None = None


@dataclass(frozen=True)
class EvidenceCommitter:
    commit: Callable[[EvidenceUpdate], EvidenceView]
    read: Callable[[], EvidenceView]


Parameters = TypeVar("Parameters")


@dataclass(frozen=True)
class StepContext(Generic[Parameters]):
    release: ReleaseView
    evidence: EvidenceView
    clock: Clock
    services: Services
    parameters: Parameters = field(default_factory=NoParameters)
    inputs: Mapping = field(default_factory=FrozenDict)
    effect: EffectContext | None = None
    new_id: Callable[[], str] | None = None
    approval: ApprovalContext | None = None
    role: HookRole = HookRole.BUILD
    step_key: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "inputs", freeze(dict(self.inputs)))

    def input(self, name, default=None):
        return self.inputs.get(name, default)

    def recovery(self) -> EvidenceView:
        if self.effect is None:
            raise ValueError("This invocation has no durable effect authority")
        return self.effect.commit.read()
