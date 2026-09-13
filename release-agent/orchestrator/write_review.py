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
    parser.add_argument("--reserve", action="store_true",
                        help="Approve and reserve only; do not start provider operations")


def _checkpoint(orch, before):
    try:
        orch.state.checkpoint()
    except BaseException:
        for name, value in before.items():
            setattr(orch.state, name, value)
        raise


@dataclass(frozen=True)
class WriteAuthorization:
    plan: WritePlan
    execution_id: str
    reserved_only: bool
    _orch: object = field(repr=False)
    _permit: object = field(repr=False)

    def validate(self):
        if self.reserved_only:
            raise ValueError("Reservation is not permission to execute")
        self._orch.validate_outcome_permit(self._permit)


def authorize(
    args, orch, phase: str, step: str, planner: Callable[[], WritePlan],
) -> WriteAuthorization:
    """Replan under the release lock, checkpoint review, replan and fence one attempt.

    Payloads are never stored. An in-flight attempt cannot be replayed, even with
    the same review hash; the owner must resolve uncertain provider results first.
    """
    from orchestrator import cli_common as C

    revision.assert_current(orch)
    transaction = C._LOCKED_STATE.get()
    if transaction is None or transaction[0] != C.os.path.abspath(
            C.state_path(args.runs_root, args.release)):
        raise ValueError("Write authorization requires the existing release transaction lock")
    if args.release != orch.state.release_id:
        raise ValueError("Write review release identity mismatch")
    expected = getattr(args, "review_hash", None)
    reviewer = getattr(args, "approved_by", None)
    if not revision.is_hash(expected) or not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("An exact --review-hash and nonempty --approved-by are required; no writes")
    reviewer = reviewer.strip()
    current = planner()
    if review_hash(orch, phase, step, current) != expected:
        raise ValueError("Write review hash is stale; review the fresh plan; no writes")
    execution_id = getattr(args, "execution_id", None)
    record = orch.state.get_step(phase, step)
    if execution_id:
        if ((record.execution or {}).get("id") != execution_id
                or (record.execution or {}).get("write_review") !=
                {"hash": expected, "approved_by": reviewer}):
            raise ValueError("Write review does not belong to this execution/reviewer")
        if record.status != "running":
            raise ValueError("Write attempt is already started or uncertain; owner resolution required")
    else:
        before = deepcopy(asdict(orch.state))
        result = orch._transition_kernel().reserve(
            phase, step, getattr(args, "executor", None) or reviewer,
            write_review={"hash": expected, "approved_by": reviewer})
        if not result.changed:
            raise ValueError(result.message)
        _checkpoint(orch, before)
        execution_id = orch.step_execution(phase, step)["id"]
    args.execution_id = execution_id
    if getattr(args, "reserve", False):
        return WriteAuthorization(current, execution_id, True, orch, None)

    current = planner()
    if review_hash(orch, phase, step, current) != expected:
        raise ValueError("Write plan changed after reservation; owner retained; no provider writes")
    revision.assert_current(orch)
    before = deepcopy(asdict(orch.state))
    permit = orch._transition_kernel().begin_reviewed_write(phase, step, execution_id)
    if isinstance(permit, TransitionResult):
        raise ValueError(permit.message)
    try:
        _checkpoint(orch, before)
    except BaseException:
        orch._transition_kernel()._outcome_permits.pop(permit, None)
        raise
    return WriteAuthorization(current, execution_id, False, orch, permit)


def print_reservation(authorization):
    print(json.dumps({
        "kind": "reserved", "execution_id": authorization.execution_id,
        "permission_to_execute": False,
        "note": "Re-run the same checked command with --execute and --execution-id; omit --reserve.",
    }))
