"""Transient deterministic write plans and durable, single-attempt authorizations."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json

from orchestrator import revision
from orchestrator.step_context import freeze, thaw
from orchestrator.transitions import TransitionResult


def _object(value):
    if not isinstance(value, Mapping):
        raise ValueError("Write plan fields must be mappings")
    value = thaw(value)
    json.dumps(value, sort_keys=True, allow_nan=False)
    return freeze(value)


@dataclass(frozen=True)
class WriteOperation:
    kind: str
    target: Mapping
    content: Mapping
    preconditions: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("Write operation kind is required")
        for name in ("target", "content", "preconditions"):
            object.__setattr__(self, name, _object(getattr(self, name)))

    def as_dict(self):
        return {"kind": self.kind, **{
            name: thaw(getattr(self, name))
            for name in ("target", "content", "preconditions")
        }}


@dataclass(frozen=True)
class WritePlan:
    command: str
    parameters: Mapping
    operations: tuple[WriteOperation, ...]
    preconditions: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("Write command is required")
        object.__setattr__(self, "parameters", _object(self.parameters))
        object.__setattr__(self, "preconditions", _object(self.preconditions))
        object.__setattr__(self, "operations", tuple(self.operations))
        if any(not isinstance(item, WriteOperation) for item in self.operations):
            raise ValueError("Write plans require typed deterministic operations")

    def as_dict(self):
        return {
            "command": self.command, "parameters": thaw(self.parameters),
            "operations": [item.as_dict() for item in self.operations],
            "preconditions": thaw(self.preconditions),
        }


def envelope(orch, phase: str, step: str, plan: WritePlan):
    definition = orch.workflow.step(phase, step)
    if definition is None or definition.write_command != plan.command:
        raise ValueError("Write plan does not match the configured step capability")
    record = orch.state.get_step(phase, step)
    identity = revision.revision_id(orch.state.workflow_revision)
    if identity is None:
        raise ValueError("Write review requires an explicitly bound workflow revision")
    return {
        "version": 1, "release": orch.state.release_id,
        "step": definition.key,
        "generation": record.invalidated_at or record.completed_at or "initial",
        "workflow_revision": identity, **plan.as_dict(),
    }


def review_hash(orch, phase: str, step: str, plan: WritePlan):
    return revision.digest(envelope(orch, phase, step, plan))


def preview(orch, phase: str, step: str, plan: WritePlan):
    return {
        "plan": envelope(orch, phase, step, plan),
        "review_hash": review_hash(orch, phase, step, plan),
        "permission_to_execute": False,
    }


def add_arguments(parser):
    parser.add_argument("--review-hash", help="Exact sha256 digest of the approved write preview")
    parser.add_argument("--approved-by", help="Reviewer who approved this exact write plan")
    parser.add_argument("--executor", help="Claiming session identity (defaults to reviewer)")


def add_auto_approve_argument(parser, *, help_text=None):
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help=help_text
        or "Compute and checkpoint the current reviewed plan without human approval, "
        "then execute through the normal fenced write path",
    )


@dataclass(frozen=True)
class WriteAuthorization:
    plan: WritePlan
    execution_id: str
    _orch: object = field(repr=False)
    _permit: object = field(repr=False)

    def validate(self):
        self._orch.validate_outcome_permit(self._permit)


def authorize(
    args, orch, phase: str, step: str, planner: Callable[[], WritePlan],
    *, auto_approved_by: str | None = None,
) -> WriteAuthorization:
    """Plan once, then atomically persist review and attempt before any provider work.

    Failed planning leaves the step untouched; uncertain attempts are never replayed.
    """
    from orchestrator import cli_common as C

    revision.assert_current(orch)
    transaction = C._LOCKED_STATE.get()
    if transaction is None or transaction[0] != C.os.path.abspath(
            C.state_path(args.runs_root, args.release)):
        raise ValueError("Write authorization requires the existing release transaction lock")
    if args.release != orch.state.release_id:
        raise ValueError("Write review release identity mismatch")
    if getattr(args, "reserve", False):
        raise ValueError("Reserve-only writes are not supported; preview then execute")
    if getattr(args, "execution_id", None):
        raise ValueError("An existing execution cannot authorize another write; reconcile its result")
    expected = getattr(args, "review_hash", None)
    reviewer = getattr(args, "approved_by", None)
    automatic = getattr(args, "auto_approve", False)
    if automatic:
        if not getattr(args, "execute", False) or getattr(args, "dry_run", False):
            raise ValueError("--auto-approve requires --execute without --dry-run")
        if expected or reviewer:
            raise ValueError("--auto-approve cannot be combined with --review-hash or --approved-by")
        if not isinstance(auto_approved_by, str) or not auto_approved_by.strip():
            raise ValueError("This writer does not permit automatic approval")
        reviewer = auto_approved_by
    elif not revision.is_hash(expected) or not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("An exact --review-hash and nonempty --approved-by are required; no writes")
    reviewer = reviewer.strip()
    current = planner()
    actual_hash = review_hash(orch, phase, step, current)
    if automatic:
        expected = args.review_hash = actual_hash
        args.approved_by = reviewer
        args.executor = getattr(args, "executor", None) or reviewer
    elif actual_hash != expected:
        raise ValueError("Write review hash is stale; review the fresh plan; no writes")
    revision.assert_current(orch)
    before = deepcopy(asdict(orch.state))
    permit = None
    try:
        permit = orch._transition_kernel().start_reviewed_write(
            phase, step, getattr(args, "executor", None) or reviewer,
            write_review={"hash": expected, "approved_by": reviewer})
        if isinstance(permit, TransitionResult):
            raise ValueError(permit.message)
        orch.state.checkpoint()
    except BaseException:
        for name, value in before.items():
            setattr(orch.state, name, value)
        if permit is not None and not isinstance(permit, TransitionResult):
            orch._transition_kernel()._outcome_permits.pop(permit, None)
        raise
    execution_id = orch.step_execution(phase, step)["id"]
    args.execution_id = execution_id
    return WriteAuthorization(current, execution_id, orch, permit)
