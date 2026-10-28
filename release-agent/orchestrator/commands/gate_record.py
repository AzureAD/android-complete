"""Record an already-approved external gate; no submit, ownership reservation or drain."""
from __future__ import annotations

import json

from orchestrator import cli_common as C


def cmd_record_orchestrator_approval(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        if args.preview:
            result = orch.preview_manual_gate_approval(
                args.phase, args.step, approval_id=args.approval_id)
        else:
            result = orch.record_manual_gate_approval(
                args.phase, args.step, approval_id=args.approval_id,
                review_hash=args.review_hash, recorded_by=args.recorded_by)
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def register(sub):
    sp = sub.add_parser(
        "record-orchestrator-approval",
        help="Import exact proof of an already-approved manual ADO gate, without provider writes or advancing.")
    sp.add_argument("--release", required=True)
    sp.add_argument("--phase", required=True)
    sp.add_argument("--step", required=True)
    sp.add_argument("--approval-id", required=True, help="Exact already-approved provider UUID; never discovered")
    action = sp.add_mutually_exclusive_group(required=True)
    action.add_argument("--preview", action="store_true", help="Read-only exact target, proof, impact and review hash")
    action.add_argument("--review-hash", help="Confirm the exact current manual import preview")
    sp.add_argument("--recorded-by", help="Human importing this evidence; not asserted to be its provider approver")
    sp.set_defaults(func=cmd_record_orchestrator_approval)
