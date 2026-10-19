"""Explicit isolated source-plan preview/creation, not workflow adoption or advancement."""
from argparse import ArgumentParser
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import time

from orchestrator import cli_common as C
from orchestrator.state import ReleaseState
from tools import broker_plans as B, broker_scenarios as S, broker_failure_triage as F, testplans as T


_WINDOWS = os.name == "nt"
_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4)


def _replace_receipt(pending, path):
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            os.replace(pending, path)
            return
        except OSError as exc:
            # Windows may report sharing interference as access denied (5).
            # Persistent permission errors still raise the final original failure.
            if (not _WINDOWS or getattr(exc, "winerror", None) not in (5, 32, 33)
                    or attempt == len(_REPLACE_DELAYS)):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])


def run(args):
    repair = getattr(args, "repair_membership", False)
    metadata = getattr(args, "repair_metadata", False)
    correction_key = getattr(args, "correct_stress_category", None)
    recovery_key = getattr(args, "recover_stress_category", None)
    correction = correction_key or recovery_key
    if correction_key and recovery_key:
        raise ValueError("Select correction preview/apply OR result-only correction recovery")
    if correction and (repair or metadata or args.recover_create
                       or getattr(args, "approval_manifest", None)):
        raise ValueError("Stress classification correction must be a separate isolated operation")
    correction_confirmation = getattr(args, "confirm_correction_sha256", None)
    if not correction and (correction_confirmation or getattr(args, "correction_preview", None)):
        raise ValueError("Correction digest/export requires an exact Stress correction scenario key")
    repair_confirmation = getattr(args, "confirm_repair_sha256", None)
    if correction and repair_confirmation is not None:
        raise ValueError("Stress correction requires its own --confirm-correction-sha256")
    if repair and metadata:
        raise ValueError("Membership and metadata restoration must be separate operations")
    if repair or metadata or correction:
        if args.recover_create or args.resource_id is not None or args.reason is not None:
            raise ValueError("Repair cannot be combined with create recovery")
    elif repair_confirmation is not None:
        raise ValueError("--confirm-repair-sha256 requires --repair-membership or --repair-metadata")
    if args.recover_create:
        if not args.execute or not args.resource_id or not args.reason or not args.reason.strip():
            raise ValueError("Exact receipt recovery requires --execute, --resource-id and the owner's --reason")
    elif args.resource_id is not None or args.reason is not None:
        raise ValueError("--resource-id/--reason are only valid with --recover-create")
    snapshot = Path(args.snapshot).resolve()
    root = Path(args.isolated_root).resolve()
    if root == snapshot.parent or root.is_relative_to(snapshot.parent):
        raise ValueError("The isolated receipt directory must be outside the source release directory")
    if not args.name.startswith("TEST ONLY - ") or args.name != args.name.strip():
        raise ValueError("Isolated plan names must start with 'TEST ONLY - '")
    raw = snapshot.read_bytes()
    state = ReleaseState.load(str(snapshot))  # Also validates/hydrates file-backed Phase-2 evidence.
    release = state.release_id
    if not isinstance(release, str) or not re.fullmatch(r"\d{4}-\d{2}", release):
        raise ValueError("Snapshot requires an explicit monthly release identity")
    instance = release + "-scenario-" + S.digest(args.name)[:16]
    path = root / instance / "broker-scenario-resource.json"
    pending = path.with_suffix(".pending")
    rc = state.pipeline_runs["rcs"][-1]
    record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if (repair or metadata or correction) and record.get("identity") != B.identity(instance, args.name):
        raise ValueError("Repair requires this exact isolated instance identity")
    if record:
        if record.get("snapshot_sha256") != S.sha256(raw).hexdigest():
            raise ValueError("Collected snapshot changed; retain the existing isolated resource for recovery")
        frozen = record["source"]
    else:
        ok, frozen, detail = B.prepare_source(rc=rc)
        if not ok:
            raise ValueError(detail)
    preview = {"name": args.name, "instance": instance, "receipt": str(path),
               "read_only": not args.execute, "workflow_adoption": False,
               "execution_scope": "isolated plan, suites, owned cases, membership and source outcomes only",
               **S.preview(frozen)}
    if record.get("classification_amendment"):
        F.checked_record(record, recovery=bool(recovery_key))
        preview["classification_policy"] = F.CLASSIFICATION_POLICY
        preview["effective_counts"] = F._correction_body(
            record["classification_amendment"]["intent"],
            record["classification_amendment"]["history"],
            record["classification_amendment"]["before"], recovery=bool(recovery_key))["effective_counts"]
        preview["historical_source_retained"] = True
    if getattr(args, "approval_manifest", None):
        if args.execute or frozen.get("schema_version") != F.VERSION:
            raise ValueError("Failure-only approval export is read-only and requires source v4")
        export = Path(args.approval_manifest).resolve()
        if export == snapshot or export == path or export.is_relative_to(snapshot.parent):
            raise ValueError("Approval export must be separate from protected source and resource receipts")
        with export.open("x", encoding="utf-8") as stream:
            json.dump(F.approval_manifest(frozen, instance, args.name), stream, indent=2, sort_keys=True)
        preview["approval_manifest"] = str(export)
    if not args.execute:
        if correction:
            reviewed = F.preview_stress_correction(record, correction, recovery=bool(recovery_key))
            artifact = getattr(args, "correction_preview", None)
            if artifact:
                export = Path(artifact).resolve()
                if export in (snapshot, path, pending) or export.is_relative_to(snapshot.parent):
                    raise ValueError("Correction export must be separate from protected source/receipts")
                with export.open("x", encoding="utf-8") as stream:
                    json.dump(reviewed, stream, indent=2, sort_keys=True)
                preview["correction_preview"] = str(export)
            preview["execution_scope"] = "One source-bound Local Stress category rename; no creates/outcomes"
            return {"preview": preview, "classification_correction": reviewed}
        if metadata:
            preview["execution_scope"] = "Restore original frozen isolated plan metadata only"
            return {"preview": preview, "metadata_repair": B.preview_metadata_repair(record)}
        return ({"preview": preview, "membership_repair": S.preview_membership_repair(record)}
                if repair else {"preview": preview})
    if args.confirm_source_sha256 != preview["source_sha256"]:
        raise ValueError("Execution requires the exact --confirm-source-sha256 from the reviewed preview")

    with C.state_lock(str(root), instance):
        if pending.exists():
            exc = ValueError(
                f"Uncommitted isolated receipt at {pending}; retain both receipt files "
                "for owner recovery before execution; no provider writes attempted")
            exc.receipt_checkpoint = {"stage": "receipt_pending", "receipt": str(path),
                                      "pending_receipt": str(pending)}
            raise exc
        # Re-read under the same existing cross-process lock used by release resources.
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if current != record:
            raise ValueError("Isolated receipt changed during preparation; preview again")

        def checkpoint():
            record.setdefault("snapshot_sha256", S.sha256(raw).hexdigest())
            stage = "receipt_directory"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                stage = "receipt_write"
                with pending.open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(record, stream, indent=2, sort_keys=True)
                    stage = "receipt_flush"
                    stream.flush()
                    stage = "receipt_fsync"
                    os.fsync(stream.fileno())
                    stage = "receipt_close"
                stage = "receipt_replace"
                _replace_receipt(pending, path)
            except OSError as exc:
                exc.receipt_checkpoint = {"stage": stage, "receipt": str(path),
                                          "pending_receipt": str(pending)}
                raise

        if metadata:
            return {**B.repair_metadata(record, repair_confirmation, checkpoint),
                    "receipt": str(path)}
        if correction:
            if getattr(args, "correction_preview", None):
                raise ValueError("--correction-preview is a read-only export; omit it when applying/recovering")
            return {**F.correct_stress_category(record, correction, correction_confirmation, checkpoint,
                                               recovery=bool(recovery_key)), "receipt": str(path)}
        if repair:
            return {**S.repair_membership(record, repair_confirmation, checkpoint),
                    "receipt": str(path)}
        if args.recover_create:
            S.recover_create(record, args.recover_create, args.resource_id, args.reason, checkpoint)
            return {"recovered_create": args.recover_create, "resource_id": args.resource_id,
                    "receipt": str(path), "provider_writes": False}
        # The instance marker is distinct from the real release, even with identical inputs.
        ok, pid, detail = B.ensure_plan(instance, args.name, record, checkpoint,
                                        prepared_source=deepcopy(frozen))
        if not ok:
            raise ValueError(detail)
        ok, projection, detail = S.project(
            rc, tracked=record["scenario_bindings"], version=frozen["schema_version"],
            **({"classification_policy": F.policy_for_record(record)}
               if frozen["schema_version"] == F.VERSION else {}))
        if not ok:
            raise ValueError(detail)
        bound = S.bind_projection(record, projection)

        def fill_checkpoint(summary):
            record["fill"] = summary
            checkpoint()

        prior = record.get("fill")
        target = F.target(record) if frozen["schema_version"] == F.VERSION else {
            "plan_id": pid, "suite_id": record["ui_suite_id"]}
        if prior:
            from steps.bug_bash.ui_results import applied_record
            applied_record(prior, [r["case_id"] for r in record["scenario_bindings"]],
                           "broker", target, complete=False)
        ok, summary, detail = T.fill_ui_automation_results(
            pid, bound["verdicts"],
            **({"automation_target": target} if frozen["schema_version"] == F.VERSION else
               {"suite_id": record["ui_suite_id"]}),
            scenario_resource=record, scenario_bindings=bound["scenario_bindings"],
            prior_points=prior["applied_points"] if prior else [], checkpoint=fill_checkpoint)
        if not ok:
            raise ValueError(detail)
        record["fill_status"] = "complete"
        checkpoint()
        return {"preview": preview, "plan_id": pid, "receipt": str(path),
                "workflow_adoption": False, "status": record["status"],
                "applied_points": len(summary["applied_points"]), "fill_status": record["fill_status"]}


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="Read-only collected release-state JSON")
    parser.add_argument("--isolated-root", required=True, help="Separate local creation receipt directory")
    parser.add_argument("--name", required=True, help="Explicit TEST ONLY - destination")
    parser.add_argument("--approval-manifest", help="Read-only v4 export of exact new-case and outcome payloads to a new local JSON file")
    parser.add_argument("--execute", action="store_true", help="Execute only the reviewed isolated operation")
    parser.add_argument("--confirm-source-sha256", help="Exact reviewed source digest; no blanket approvals")
    parser.add_argument("--recover-create", help="Exact uncertain operation key, from its receipt")
    parser.add_argument("--resource-id", type=int, help="Owner-supplied actual provider receipt ID")
    parser.add_argument("--reason", help="Owner's reason/evidence for exact receipt recovery")
    parser.add_argument("--repair-membership", action="store_true",
                        help="Preview/explicitly normalize only unpublished default-expanded point assignments")
    parser.add_argument("--confirm-repair-sha256", help="Exact reviewed membership/metadata repair preview digest")
    parser.add_argument("--repair-metadata", action="store_true",
                        help="Preview/restore only the frozen isolated subarea after a completion-PATCH reset")
    parser.add_argument("--correct-stress-category", help="Exact legacy-v4 Local Stress scenario key; preview/one rename")
    parser.add_argument("--recover-stress-category", help="Exact attempted correction key; result-only readback, never PATCH")
    parser.add_argument("--confirm-correction-sha256", help="Exact freshly reviewed classification-correction digest")
    parser.add_argument("--correction-preview", help="Read-only correction export to a new local JSON artifact")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "workflow_adoption": False,
                          **getattr(exc, "receipt_checkpoint", {})}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
