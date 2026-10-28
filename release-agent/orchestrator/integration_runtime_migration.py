"""Bounded runtime-only migrations of an owned integration recovery attempt.

No invocation permit is issued. Both the original review envelope and its runtime
remain immutable evidence; recovery needs a separate fresh manual authorization.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from orchestrator import cli_common as C, revision
from orchestrator.invariants import validate_snapshot
from orchestrator.state import ReleaseState, _now


DATA_KEY = "integration_runtime_migration"


def receipts(record):
    value = record.data.get(DATA_KEY)
    if value is None:
        return []
    if isinstance(value, dict) and set(value) == {"version", "receipts"} and value["version"] == 2:
        if not isinstance(value["receipts"], list) or not 2 <= len(value["receipts"]) <= 8:
            raise ValueError("Invalid bounded integration runtime migration chain")
        return value["receipts"]
    return [value]


def latest_hash(record):
    history = receipts(record)
    return history[-1]["hash"] if history else None


def authenticate_original(orch, record, original):
    """Authenticate a source-runtime envelope after this specific migration."""
    history = receipts(record)
    if not history:
        return False
    binding = None
    for index, receipt in enumerate(history):
        if not _authenticate_receipt(orch, record, original, receipt, index, history, binding):
            return False
        binding = deepcopy(receipt["plan"]["target_binding"])
        binding["last_adoption"] = {
            "from_revision": receipt["plan"]["source_revision"], "at": receipt["at"], "by": receipt["by"],
            "reason": "Owned integration recovery runtime migration: " + receipt["reason"]}
    return binding == orch.state.workflow_revision


def _authenticate_receipt(orch, record, original, receipt, index, history, binding):
    if not isinstance(receipt, dict) or set(receipt) != {"plan", "hash", "at", "by", "reason"}:
        return False
    plan = receipt["plan"]
    if not isinstance(plan, dict) or receipt["hash"] != revision.digest(plan):
        return False
    if any(not isinstance(receipt.get(key), str) or not receipt[key].strip()
           for key in ("at", "by", "reason")):
        return False
    source, target = plan.get("source_binding"), plan.get("target_binding")
    revision.validate_binding(source)
    revision.validate_binding(target)
    return bool(
        source and target and source["phases"] == target["phases"]
        and plan.get("version") == (1 if index == 0 else 2)
        and (index == 0 or (source == binding
             and plan.get("previous_receipts_hash") == revision.digest(history[:index])))
        and plan.get("command") == "migrate-integration-recovery-runtime"
        and plan.get("release") == orch.state.release_id
        and plan.get("step") == "finalize.integ_prs"
        and plan.get("execution") == record.execution
        and plan.get("generation") == (record.invalidated_at or record.completed_at or "initial")
        and plan.get("original_review") == record.execution.get("write_review")
        and plan.get("original_review", {}).get("hash") == revision.digest(original)
        and plan.get("source_revision") == revision.revision_id(source)
        and (index != 0 or plan["source_revision"] == original.get("workflow_revision"))
        and plan.get("target_revision") == revision.revision_id(target)
        and plan.get("source_runtime_hash") == source["runtime_hash"]
        and plan.get("target_runtime_hash") == target["runtime_hash"]
        and source["runtime_hash"] != target["runtime_hash"])


def preview(orch, args, *, registry_entries=()):
    from orchestrator import integration_recovery as recovery

    record = recovery._record(orch, args)
    if any(key in record.execution for key in ("effect_mode", "approval", "notification_id")):
        raise ValueError("Runtime migration supports only the original owned integration writer")
    original = recovery.load_original(orch, args)
    history = receipts(record)
    if len(history) >= 8:
        raise ValueError("Integration migration history is full")
    journal = record.data.get(recovery.DATA_KEY)
    if journal and (journal.get("execution_id") != record.execution["id"]
                    or journal.get("original_hash") != record.execution["write_review"]["hash"]
                    or any(r.get("status") not in ("pending", "applied") for r in journal["receipts"])):
        raise ValueError("Runtime migration forbids uncertain recovery operations")
    original_journal = record.data.get("integration_write_journal", {})
    if original_journal and (
            original_journal.get("execution_id") != record.execution["id"]
            or original_journal.get("original_hash") != record.execution["write_review"]["hash"]):
        raise ValueError("Original operation journal belongs to another execution/review")
    if any(r.get("status") not in ("pending", "applied")
           for r in original_journal.get("operations", {}).values()):
        raise ValueError("Runtime migration forbids uncertain original operations")
    source = deepcopy(orch.state.workflow_revision)
    current, imported_match = revision._current_revision(orch)
    revision.validate_binding(source)
    if (not imported_match or current["runtime_hash"] != orch._loaded_runtime_hash):
        raise ValueError("Runtime source changed during this process; restart before migration review")
    if source["phases"] != current["phases"]:
        raise ValueError("Integration runtime migration cannot change workflow phase definitions")
    if source["runtime_hash"] == current["runtime_hash"]:
        raise ValueError("Integration runtime is already current; no migration is needed")
    for flag, actual in (("source_runtime_hash", source["runtime_hash"]),
                         ("target_runtime_hash", current["runtime_hash"])):
        supplied = getattr(args, flag, None)
        if supplied is not None and supplied != actual:
            raise ValueError("Exact " + flag + " does not match migration review")
    # Reuse every ordinary adoption blocker, excluding only this exact preserved
    # execution. Never grant a general exception to workflow-adopt.
    adoption = revision.adoption_preview(orch, registry_entries=registry_entries)
    allowed = "steps.finalize.integ_prs: owned execution"
    blockers = [blocker for blocker in adoption["blockers"] if blocker != allowed]
    if blockers:
        raise ValueError("Integration runtime migration blocked: " + "; ".join(blockers))
    errors = [issue.message for issue in validate_snapshot(orch.state, orch.workflow)
              if issue.severity == "error"]
    if errors:
        raise ValueError("Invalid integration migration source state: " + "; ".join(errors))
    if any(adoption["invalidation"][key] for key in (
            "step_keys", "new_step_keys", "removed_step_keys", "gate_decisions", "notification_offers")):
        raise ValueError("Integration runtime migration requires an exact zero-reset revision")
    # Snapshot every field, not just the owner: unrelated progress makes the hash
    # stale rather than being overwritten by migration.
    plan = {
        "version": 1, "command": "migrate-integration-recovery-runtime",
        "release": args.release, "step": "finalize.integ_prs",
        "execution": deepcopy(record.execution),
        "generation": original["generation"],
        "original_review": deepcopy(record.execution["write_review"]),
        "source_binding": source, "target_binding": deepcopy(current),
        "source_revision": revision.revision_id(source), "target_revision": revision.revision_id(current),
        "source_runtime_hash": source["runtime_hash"], "target_runtime_hash": current["runtime_hash"],
        "state_hash": revision.digest(asdict(orch.state)),
        "registry_hash": revision.digest(registry_entries),
        "impact": {"step_status_changes": [], "execution_changes": [], "invalidations": [],
                   "gate_decisions_removed": [], "notification_ownership_changes": [],
                   "provider_operations": [], "permission_to_execute": False},
    }
    if history:
        plan.update(version=2, previous_receipts_hash=revision.digest(history))
    return {"plan": plan, "migration_hash": revision.digest(plan),
            "permission_to_migrate": False, "permission_to_execute": False}


def apply(orch, args, *, registry_entries=()):
    """Caller holds release then registry lock, through this atomic checkpoint."""
    transaction = C._LOCKED_STATE.get()
    path = C.os.path.abspath(C.state_path(args.runs_root, args.release))
    if transaction is None or transaction[0] != path:
        raise ValueError("Integration runtime migration requires the existing release transaction lock")
    if (not revision.is_hash(getattr(args, "approve_hash", None))
            or not revision.is_hash(getattr(args, "source_runtime_hash", None))
            or not revision.is_hash(getattr(args, "target_runtime_hash", None))
            or not getattr(args, "by", None) or not args.by.strip()
            or not getattr(args, "reason", None) or not args.reason.strip()
            or not getattr(args, "confirm_no_inflight", False)):
        raise ValueError("Migration requires manual hash/by/reason, both runtime hashes and --confirm-no-inflight")
    reviewed = preview(orch, args, registry_entries=registry_entries)
    if reviewed["migration_hash"] != args.approve_hash:
        raise ValueError("Stale integration runtime migration hash; preview again")
    # No cached runtime/review/state snapshot is sufficient to apply a migration.
    if (preview(orch, args, registry_entries=registry_entries) != reviewed
            or revision.digest(asdict(ReleaseState.load(path))) != reviewed["plan"]["state_hash"]):
        raise ValueError("Runtime, approved artifact or persisted state changed during migration")
    before = deepcopy(vars(orch.state))
    plan, stamp = reviewed["plan"], _now()
    try:
        receipt = {"plan": deepcopy(plan), "hash": args.approve_hash,
                   "at": stamp, "by": args.by.strip(), "reason": args.reason.strip()}
        record = orch.state.get_step("finalize", "integ_prs")
        history = receipts(record)
        record.data[DATA_KEY] = ({"version": 2, "receipts": deepcopy(history) + [receipt]}
                                 if history else receipt)
        orch.state.set_step("finalize", "integ_prs", record)
        target = deepcopy(plan["target_binding"])
        target["last_adoption"] = {
            "from_revision": plan["source_revision"], "at": stamp,
            "by": args.by.strip(), "reason": "Owned integration recovery runtime migration: " + args.reason.strip()}
        orch.state.workflow_revision = target
        errors = [issue.message for issue in validate_snapshot(orch.state, orch.workflow)
                  if issue.severity == "error"]
        if errors:
            raise ValueError("Invalid integration migration target state: " + "; ".join(errors))
        orch.state.checkpoint()
    except BaseException:
        vars(orch.state).clear()
        vars(orch.state).update(before)
        raise
    # A pre-migration invocation permit must never survive the changed runtime.
    orch._transition_kernel()._outcome_permits.clear()
    return {**reviewed, "migrated": True, "permission_to_execute": False}
