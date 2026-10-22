"""Transient reviewed plans and opt-in, exactly reconciled compound progress."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
from urllib.parse import urlsplit

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
    result = {
        "version": 1, "release": orch.state.release_id,
        "step": definition.key,
        "generation": record.invalidated_at or record.completed_at or "initial",
        "workflow_revision": identity, **plan.as_dict(),
    }
    execution = record.execution or {}
    if execution.get("write_progress"):
        result["continuation"] = {
            "execution_id": execution["id"],
            "scope": execution["write_progress"]["scope"],
            "operations": revision.digest(execution["write_progress"]["operations"]),
        }
    return result


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


def add_recovery_arguments(parser):
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--manual-result", help="Record an owner-completed commit, PR or PBI; no provider writes")
    modes.add_argument("--manual-completion",
                       help="Owner confirms the writer stopped and ALL remaining work is complete; supply evidence URL")
    modes.add_argument("--confirm-not-applied", action="store_true",
                       help="Owner confirms the previous writer stopped without applying its push")
    parser.add_argument("--confirmed-by", help="Release owner explicitly confirming recovery")
    parser.add_argument("--recovery-reason", help="Owner's explanation of the manual completion or non-application")


def record_recovery(args, progress):
    manual = getattr(args, "manual_result", None)
    completion = getattr(args, "manual_completion", None)
    absent = getattr(args, "confirm_not_applied", False)
    actor = getattr(args, "confirmed_by", None)
    reason = getattr(args, "recovery_reason", None)
    if manual is None and completion is None and not absent and actor is None and reason is None:
        return None
    if sum((manual is not None, completion is not None, bool(absent))) != 1:
        raise ValueError("Choose --manual-result, --manual-completion or --confirm-not-applied")
    if any(getattr(args, key, None) for key in (
            "execute", "auto_approve", "review_hash", "approved_by", "executor", "execution_id")):
        raise ValueError("Recovery is result-only; do not combine it with execution/approval options")
    if completion is not None:
        if any(getattr(args, key, None) for key in ("repos", "pbi", "pbi_title", "repo_dir")):
            raise ValueError("Manual completion covers the original entire step; omit repository/PBI/path selectors")
        owner = progress.recovery_authority(args, actor, reason)
        confirmation = {"confirmed_by": owner, "reason": reason.strip(), "evidence": completion}
        before = deepcopy(asdict(progress.orch.state))
        try:
            result = progress.orch._transition_kernel().complete(
                progress.phase, progress.step, "Owner-attested manual completion: " + reason.strip(),
                manual_confirmation=confirmation)
            if not result.changed:
                raise ValueError(result.message)
            progress.orch.state.checkpoint()
        except BaseException:
            for name, value in before.items():
                setattr(progress.orch.state, name, value)
            raise
        return {"recovery": "manual_completion", "execution_id": progress.execution_id,
                "permission_to_execute": False, "machine_verified": False,
                "message": result.message}
    return progress.recover(args, manual, actor, reason)


def recovery_guidance():
    return ("Do not loop or clear ownership. Complete the scoped action manually and record "
            "--manual-result <commit-or-ID-or-URL> --confirmed-by <release-owner> "
            "--recovery-reason <owner-explanation>. For an unapplied push, the owner may instead "
            "confirm the previous writer has stopped using --confirm-not-applied with the same "
            "owner/reason; readback must still prove the source branch is unchanged. "
            "If verification is unavailable, the owner can instead attest completion of ALL remaining "
            "work with --manual-completion <evidence-url> and the same owner/reason, confirming the "
            "previous writer has stopped. This is owner-attested, not machine-verified. "
            "These options never send provider writes or approve another gate.")


@dataclass(frozen=True)
class WriteAuthorization:
    plan: WritePlan
    execution_id: str
    _orch: object = field(repr=False)
    _permit: object = field(repr=False)
    progress: object = field(default=None, repr=False)

    def validate(self):
        self._orch.validate_outcome_permit(self._permit)


def authorize(
    args, orch, phase: str, step: str, planner: Callable[[], WritePlan],
    *, auto_approved_by: str | None = None, progress=None,
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
    if progress is not None:
        progress.prepare(policy={"automatic": automatic, "reviewer": reviewer})
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
        kernel = orch._transition_kernel()
        if progress is not None and progress.existing:
            permit = kernel.continue_reviewed_write(
                phase, step, progress.execution_id,
                write_review={"hash": expected, "approved_by": reviewer},
                progress=progress.authorized(expected, reviewer))
        else:
            permit = kernel.start_reviewed_write(
                phase, step, getattr(args, "executor", None) or reviewer,
                write_review={"hash": expected, "approved_by": reviewer})
            if progress is not None and not isinstance(permit, TransitionResult):
                kernel.checkpoint_write_progress(
                    permit, progress.authorized(expected, reviewer), checkpoint=False)
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
    result = WriteAuthorization(current, execution_id, orch, permit, progress)
    if progress is not None:
        progress.authorization = result
    return result


def _sealed(value):
    result = deepcopy(value)
    result.pop("digest", None)
    return {**result, "digest": revision.digest(result)}


def validate_progress(value):
    """Fail closed on malformed/edited receipts, including when loading saved state."""
    if (not isinstance(value, dict) or set(value) != {
            "scope", "policy", "reviews", "operations", "finished", "manual_completion", "digest"}
            or not revision.is_hash(value.get("scope"))
            or not isinstance(value.get("policy"), dict)
            or set(value["policy"]) != {"automatic", "reviewer"}
            or type(value["policy"]["automatic"]) is not bool
            or not isinstance(value["policy"]["reviewer"], str)
            or not value["policy"]["reviewer"].strip()
            or type(value.get("finished")) is not bool
            or not isinstance(value.get("reviews"), list)
            or not value["reviews"]
            or not isinstance(value.get("operations"), list)
            or _sealed(value) != value):
        raise ValueError("Invalid verified write progress; held for inspection")
    for review in value["reviews"]:
        if (not isinstance(review, dict) or set(review) != {"hash", "approved_by"}
                or not revision.is_hash(review["hash"])
                or review["approved_by"] != value["policy"]["reviewer"]):
            raise ValueError("Invalid compound review history")
    keys = set()
    for op in value["operations"]:
        if (not isinstance(op, dict) or set(op) != {
                "key", "descriptor", "receipt", "candidate", "resolutions"}
                or not revision.is_hash(op["key"]) or op["key"] in keys
                or not isinstance(op["descriptor"], dict)
                or set(op["descriptor"]) != {"kind", "target", "expected", "before"}
                or not isinstance(op["descriptor"]["kind"], str)
                or not isinstance(op["descriptor"]["target"], dict)
                or not revision.is_hash(op["descriptor"]["expected"])
                or op["descriptor"]["before"] is not None and (
                    not isinstance(op["descriptor"]["before"], str) or not op["descriptor"]["before"])
                or op["receipt"] is not None and not isinstance(op["receipt"], dict)
                or op["candidate"] is not None and not isinstance(op["candidate"], str)
                or not isinstance(op["resolutions"], list)):
            raise ValueError("Invalid compound operation receipt")
        for resolution in op["resolutions"]:
            if (not isinstance(resolution, dict) or set(resolution) != {
                    "mode", "confirmed_by", "reason", "previous", "receipt"}
                    or resolution["mode"] not in ("manual", "not_applied")
                    or any(not isinstance(resolution[key], str) or not resolution[key].strip()
                           for key in ("confirmed_by", "reason"))
                    or not revision.is_hash(resolution["previous"])
                    or (resolution["receipt"] is None) != (resolution["mode"] == "not_applied")
                    or resolution["receipt"] is not None and not isinstance(resolution["receipt"], dict)):
                raise ValueError("Invalid owner-confirmed operation resolution")
        if op["resolutions"] and op["resolutions"][-1]["receipt"] != op["receipt"]:
            raise ValueError("Owner resolution does not match its recorded result")
        keys.add(op["key"])
    if value["finished"] and any(not _resolved(op) for op in value["operations"]):
        raise ValueError("Unverified compound execution cannot be finished")
    if value["manual_completion"] is not None:
        validate_manual_completion(value["manual_completion"])
        if value["finished"]:
            raise ValueError("Owner-attested completion cannot claim machine-verified completion")
    return value


def validate_manual_completion(value):
    if (not isinstance(value, dict) or set(value) != {"confirmed_by", "reason", "evidence"}
            or any(not isinstance(text, str) or not text.strip() for text in value.values())):
        raise ValueError("Manual completion requires the owner's identity, reason and evidence URL")
    url = urlsplit(value["evidence"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(ord(c) < 32 for c in value["evidence"])):
        raise ValueError("Manual completion requires a credential-free HTTPS evidence URL")
    return value


def _retired(operation):
    return bool(operation["resolutions"] and operation["resolutions"][-1]["mode"] == "not_applied")


def _resolved(operation):
    return operation["receipt"] is not None or _retired(operation)


def continuation(orch, phase, step):
    """Expose the configured adapter, not a generic retry/override authority."""
    from orchestrator.outcomes import Blocked, NeedsSkill
    record = orch.state.get_step(phase, step)
    progress = (record.execution or {}).get("write_progress")
    if progress is None:
        return None
    kernel = orch._transition_kernel()
    if rejected := kernel._owned_outcome_check(phase, step, record.execution["id"]):
        return Blocked(rejected.message)
    selection = orch._projection().scheduling()
    if (selection.suspension or not selection.phase(phase).due
            or not selection.step(phase, step).time_ready):
        return Blocked("Checked continuation waits for the release/prerequisite/time frontier.")
    command = orch.workflow.step(phase, step).write_command
    automatic = progress["policy"]["automatic"]
    followup = f"{command} --release {orch.state.release_id}"
    if automatic:
        followup += " --execute --auto-approve"
    return NeedsSkill(
        tool=command, record_as=step, outbound=True,
        payload={"release": orch.state.release_id, "followup_command": followup,
                 "continuation": True,
                 "execution_instructions": (
                     "Use the same original repository/PBI/path selections. The checked command "
                     "verifies saved receipts and reviews only remaining actions. Unknown results "
                     "remain held; never use done, skip, reopen, or an execution ID. "
                     + recovery_guidance() + " "
                     + ("The original fixed automation policy applies." if automatic else
                        "Preview the remaining plan and obtain its fresh exact review hash."))},
        summary="Continue verified compound work through its checked command; unknown outcomes remain held.",
    )


class VerifiedProgress:
    """Only explicitly equipped adapters may reconcile and continue owned writes.

    Descriptors contain identities and expected result digests, never write payloads.
    No failed checkpoint grants another call, even if the caller catches the error.
    """
    def __init__(self, orch, phase, step, scope, reconcile, resolve=None):
        self.orch, self.phase, self.step = orch, phase, step
        self.reconcile = reconcile
        self.resolve = resolve
        execution = orch.state.get_step(phase, step).execution or {}
        self.execution_id = execution.get("id")
        self.existing = bool(execution)
        self.value = deepcopy(execution.get("write_progress"))
        self.scope = revision.digest(scope) if scope is not None else (self.value or {}).get("scope")
        self.authorization = None
        self.failed = False

    def prepare(self, policy=None):
        if self.existing or self.value and self.value.get("reviews"):
            validate_progress(self.value)
            if self.value["scope"] != self.scope:
                raise ValueError("Compound write scope changed; original execution remains owned")
            if policy is not None and self.value["policy"] != policy:
                raise ValueError("Compound approval policy/reviewer changed; original execution remains owned")
            operations = self.value["operations"]
            for index, op in enumerate(operations):
                if _retired(op):
                    continue
                # A later checked push may supersede this branch's own earlier output.
                if op["receipt"] is not None and op["descriptor"]["kind"].endswith((".push", ".bump")) and any(
                        later["descriptor"]["kind"] == op["descriptor"]["kind"]
                        and later["descriptor"]["target"] == op["descriptor"]["target"]
                        and not _retired(later) for later in operations[index + 1:]):
                    continue
                receipt = self.reconcile(deepcopy(op))
                if not isinstance(receipt, dict) or (op["receipt"] is not None
                                                     and receipt != op["receipt"]):
                    raise ValueError("Verified write receipt missing or mismatched; held")
                op["receipt"] = receipt
            self.value = _sealed(self.value)
        else:
            self.value = _sealed({
                "scope": self.scope, "policy": policy, "reviews": [],
                "operations": [], "finished": False, "manual_completion": None})

    def authorized(self, digest, reviewer):
        self.value["reviews"].append({"hash": digest, "approved_by": reviewer})
        self.value["finished"] = False
        self.value = _sealed(self.value)
        return self.value

    def has(self, key):
        return any(op["key"] == key and op["receipt"] is not None
                   for op in (self.value or {}).get("operations", []))

    def manual_push(self, target, head, base):
        return any(
            op["descriptor"]["target"] == target
            and op["descriptor"]["kind"].endswith((".push", ".bump"))
            and op["resolutions"] and op["resolutions"][-1]["mode"] == "manual"
            and op["receipt"]["commit"] == head and op["receipt"]["base_tip"] == base
            for op in (self.value or {}).get("operations", []))

    def recovery_authority(self, args, actor, reason):
        from orchestrator import cli_common as C
        revision.assert_current(self.orch)
        transaction = C._LOCKED_STATE.get()
        if (transaction is None or transaction[0] != C.os.path.abspath(
                C.state_path(args.runs_root, args.release)) or args.release != self.orch.state.release_id):
            raise ValueError("Recovery requires the owning release transaction lock")
        owner = self.orch.state.owner_email
        if (not isinstance(owner, str) or not owner.strip() or not isinstance(actor, str)
                or actor.strip().casefold() != owner.strip().casefold()):
            raise ValueError("--confirmed-by must identify this release's configured owner")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("The owner's --recovery-reason is required")
        if not self.existing:
            raise ValueError("No owned compound attempt supports manual recovery")
        validate_progress(self.value)
        if self.value["scope"] != self.scope:
            raise ValueError("Recovery cannot change the original write scope")
        return owner.strip()

    def recover(self, args, manual, actor, reason):
        owner = self.recovery_authority(args, actor, reason)
        if self.resolve is None:
            raise ValueError("This adapter does not support operation-level recovery")
        chosen = None
        for op in self.value["operations"]:
            if _retired(op):
                continue
            if op["receipt"] is None:
                chosen = op
                break
            try:
                observed = self.reconcile(deepcopy(op))
            except ValueError:
                # This failed proof is the action to resolve, not permission to ignore it.
                chosen = op
                break
            if observed != op["receipt"]:
                chosen = op
                break
        if chosen is None:
            raise ValueError("No unresolved operation; continue normally to reuse manual work already present")
        receipt = self.resolve(deepcopy(chosen), manual)
        if (manual is None) != (receipt is None):
            raise ValueError("Recovery adapter did not prove the requested result")
        chosen["resolutions"].append({
            "mode": "not_applied" if manual is None else "manual",
            "confirmed_by": owner, "reason": reason.strip(),
            "previous": revision.digest(chosen), "receipt": receipt})
        chosen["receipt"] = receipt
        self.value["finished"] = False
        self.value = _sealed(self.value)
        result = self.orch._transition_kernel().record_write_recovery(
            self.phase, self.step, self.execution_id, self.value)
        if not result.changed:
            raise ValueError(result.message)
        return {"recovery": chosen["resolutions"][-1]["mode"],
                "execution_id": self.execution_id, "operation": chosen["descriptor"]["kind"],
                "target": chosen["descriptor"]["target"], "receipt": receipt,
                "permission_to_execute": False,
                "message": "Owner-confirmed result recorded; no provider writes. Continue with the same checked command."}

    def outputs(self):
        result = {}
        for op in (self.value or {}).get("operations", []):
            result.update((op["receipt"] or {}).get("outputs", {}))
        return result

    def result_note(self):
        count = sum(bool(op["resolutions"] and op["resolutions"][-1]["mode"] == "manual")
                    for op in (self.value or {}).get("operations", []))
        return f"; owner-confirmed manual results: {count}" if count else ""

    def _save(self):
        if self.failed or self.authorization is None:
            raise ValueError("Write progress has no durable invocation authority")
        try:
            self.value = _sealed(self.value)
            self.orch._transition_kernel().checkpoint_write_progress(
                self.authorization._permit, self.value)
        except BaseException:
            self.failed = True
            raise

    def attempt(self, key, descriptor):
        if self.failed:
            raise ValueError("Previous checkpoint failed; no further writes")
        self.authorization.validate()
        if any(op["key"] == key and not _retired(op) for op in self.value["operations"]):
            raise ValueError("An attempted operation must be reconciled, never replayed")
        if any(not _resolved(op) for op in self.value["operations"]):
            raise ValueError("Unknown earlier operation must be reconciled before any writes")
        if any(op["key"] == key for op in self.value["operations"]):
            key = revision.digest({"operation": key, "attempt": len(self.value["operations"])})
        self.value["operations"].append({
            "key": key, "descriptor": descriptor, "receipt": None, "candidate": None, "resolutions": []})
        self._save()
        return key

    def candidate(self, key, identity):
        op = next(op for op in self.value["operations"] if op["key"] == key)
        op["candidate"] = str(identity)
        self._save()

    def acknowledge(self, key):
        op = next(op for op in self.value["operations"] if op["key"] == key)
        receipt = self.reconcile(deepcopy(op))
        if not isinstance(receipt, dict):
            raise ValueError("Provider result is unknown; original execution remains held")
        op["receipt"] = receipt
        self._save()
        return receipt

    def finish(self):
        # Re-read all receipts; a successful API response alone cannot certify progress.
        self.prepare()
        self.value["finished"] = True
        self._save()
