"""Source-only handler authority. These declarations are never serialized."""
from dataclasses import dataclass
from enum import Enum
import re

from .evidence import BrokerResource, PipelineEvidence, ReleaseVersions, StepData, UIFailureReminder


@dataclass(frozen=True)
class OwnStepData:
    """Replace only the invoking step's evidence data, never its lifecycle."""


class PipelineSlot(str, Enum):
    CHECKER = "checker"
    ORCHESTRATOR = "orchestrator"
    FINAL = "final"
    ECS = "ecs"
    LOCAL = "local"
    AUTH = "auth"

    @property
    def rc_lane(self):
        return self in (self.ECS, self.LOCAL, self.AUTH)


@dataclass(frozen=True)
class PipelineScope:
    slot: PipelineSlot


@dataclass(frozen=True)
class VersionEvidence:
    pass


@dataclass(frozen=True)
class BrokerPlanEvidence:
    """The existing broker_test_plan resource, not arbitrary resource paths."""


@dataclass(frozen=True)
class UIFailureContribution:
    """Only the UI producer contribution on a configured human-review step."""
    target: str


_SCOPE_TYPES = {
    OwnStepData: StepData,
    PipelineScope: PipelineEvidence,
    VersionEvidence: ReleaseVersions,
    BrokerPlanEvidence: BrokerResource,
    UIFailureContribution: UIFailureReminder,
}


@dataclass(frozen=True)
class EvidenceAuthority:
    scopes: tuple = ()

    def __post_init__(self):
        if not isinstance(self.scopes, tuple):
            raise ValueError("EVIDENCE must be a tuple of typed evidence scopes")
        seen = set()
        for scope in self.scopes:
            if type(scope) not in _SCOPE_TYPES or type(scope) in seen:
                raise ValueError("EVIDENCE contains an unknown or duplicate scope")
            seen.add(type(scope))
            if isinstance(scope, PipelineScope) and not isinstance(scope.slot, PipelineSlot):
                raise ValueError("PipelineScope requires a typed PipelineSlot")
            if isinstance(scope, UIFailureContribution) and (
                not isinstance(scope.target, str)
                or not re.fullmatch(r"[\w-]+\.[\w-]+", scope.target)
            ):
                raise ValueError("UIFailureContribution requires a phase.step target")

    def for_update(self, update_type):
        return next((scope for scope in self.scopes
                     if _SCOPE_TYPES[type(scope)] is update_type), None)


class WriteOperation(str, Enum):
    ONEAUTH_WRITE_ACCESS = "oneauth_write_access"
    CREATE_LIGHTWEIGHT_TAG = "create_lightweight_tag"
    ENSURE_BROKER_PLAN = "ensure_broker_plan"
    CREATE_AUTH_QUERY_SUITE = "create_auth_query_suite"
    FILL_AUTH_UI_RESULTS = "fill_auth_ui_results"
    FILL_UI_AUTOMATION_RESULTS = "fill_ui_automation_results"
    SET_ASSIGNED_TO = "set_assigned_to"
    SUBMIT_PIPELINE_APPROVAL = "submit_pipeline_approval"


@dataclass(frozen=True)
class WriteCapabilities:
    operations: tuple[WriteOperation, ...] = ()

    def __post_init__(self):
        if (not isinstance(self.operations, tuple)
                or any(not isinstance(op, WriteOperation) for op in self.operations)
                or len(set(self.operations)) != len(self.operations)):
            raise ValueError("WRITES must be a tuple of unique typed WriteOperation values")

    def validate_services(self, services):
        from dataclasses import fields
        from .services import EffectServices

        if not isinstance(services, EffectServices):
            raise TypeError("Write capabilities require typed EffectServices")
        allowed = {operation.value for operation in self.operations}
        for field in fields(EffectServices):
            port = getattr(services, field.name)
            if field.name in allowed:
                if not callable(port):
                    raise ValueError(f"Declared write port is missing: {field.name}")
            elif port is not None:
                raise ValueError(f"Undeclared write port: {field.name}")
        return services
