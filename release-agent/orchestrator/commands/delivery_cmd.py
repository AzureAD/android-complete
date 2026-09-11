"""One prepare/claim/result protocol for notifications; never executes transports."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, delivery as D


def finish(orch, notification_id):
    record = orch.state.notification_deliveries[notification_id]
    D.validate_record(orch, record)
    if record["status"] != "sent":
        raise ValueError("Completion requires a confirmed successful delivery")
    if record.get("completion"):
        return False
    item, st = record["descriptor"], orch.state
    scope, completion = item["scope"], item["completion"]
    reason = D.scope_reason(orch, scope, acknowledgement=True)
    if reason in ("release suspended or unsigned",
                  "owner timezone unavailable; repair configuration or tzdata"):
        return False
    if reason:
        record["completion"] = {"status": "suppressed", "reason": reason, "at": D.now_iso()}
        return True
    sent_at = record["attempts"][-1]["acknowledged_at"]
    kind = completion.get("kind")
    if kind in ("step", "step_result", "step_data"):
        pid = scope["phase"]
        sid = completion["record_as"] if kind == "step" else scope["step"]
        step = st.get_step(pid, sid)
        if step.status not in ("done", "skipped"):
            if kind in ("step", "step_result"):
                execution_id = record["attempts"][-1]["id"] if kind == "step" else None
                orch.record_scout_step(pid, sid, completion.get("status", "pass"),
                                       completion.get("note", "Notification delivered"),
                                       execution_id=execution_id)
                step = st.get_step(pid, sid)
                step.by = "scout"
            step.data.update(completion.get("data", {}))
            for key in completion.get("stamp", []):
                step.data.setdefault(key, sent_at)
            if "links" in completion:
                step.links = completion["links"]
            st.set_step(pid, sid, step)
        elif kind == "step_data" and scope["kind"] == "phase" and step.status == "done":
            # A recurring phase-owned worker can outlive its initial trigger step.
            step.data.update(completion.get("data", {}))
            st.set_step(pid, sid, step)
    if completion.get("release_field"):
        field = completion["release_field"]
        if (getattr(st, field, None) or "") < completion["date"]:
            setattr(st, field, completion["date"])
    if completion.get("checkpoint"):
        st.escalation_checkpoints.setdefault(completion["checkpoint"],
                                             {"sent_at": sent_at, "target": item["target"]})
    record["completion"] = {"status": "applied", "at": D.now_iso()}
    return True


def prepare(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    if args.source == "step":
        from orchestrator.commands.step_action import prepare_step
        out = prepare_step(args, st, orch)
        items = out.get("notifications", [])
    elif args.source == "digest":
        from orchestrator.commands.notify import _notify_payload
        out = _notify_payload(args, args.release, advance=False)
        items = out.get("notifications", [])
    elif args.source == "status-email":
        from orchestrator.commands.status_email_cmd import prepare_status_email
        out = prepare_status_email(args, st, orch)
        items = out.get("notifications", [])
    else:
        out, items = {}, []
    for item in items:
        D.offer(orch, item)
    if items:
        C.save_state(st, args.runs_root, args.release)
    return {"release": args.release, "notifications": [
        D.preview(orch, r) for key, r in st.notification_deliveries.items()
        if (not args.id or args.id == key)
        and (args.source == "pending" or key in {i["id"] for i in items})],
        **({"reason": out["reason"]} if out.get("reason") else {})}


def cmd_notification(args):
    try:
        if args.operation == "prepare":
            print(json.dumps(prepare(args)))
            return 0
        if getattr(args, "as_of", None):
            raise ValueError("Notification claims/results/finalization require the trusted current clock; --as-of is preview-only")
        st, orch = C.load_orch(args.runs_root, args.release, args.config)
        if args.operation == "claim":
            out = D.claim(orch, args.id, args.hash, args.executor)
            # Permission leaves this process only AFTER durable persistence succeeds.
            C.save_state(st, args.runs_root, args.release)
        elif args.operation == "result":
            receipt = None
            if args.receipt_file:
                with open(args.receipt_file, encoding="utf-8") as fh:
                    receipt = json.load(fh)
            changed = D.result(orch, args.id, args.execution_id, args.outcome,
                               args.evidence, receipt, args.owner_review)
            if changed:
                C.save_state(st, args.runs_root, args.release)
            # Persist delivery before attempting any domain completion. If this fails,
            # finalize is retryable; transport is never replayed.
            if args.outcome == "sent" and finish(orch, args.id):
                C.save_state(st, args.runs_root, args.release)
            out = {"recorded": changed, "status": st.notification_deliveries[args.id]["status"]}
        else:
            changed = finish(orch, args.id)
            if changed:
                C.save_state(st, args.runs_root, args.release)
            out = {"finalized": changed}
        print(json.dumps(out))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "permission_to_send": False}))
        return 1


def register(sub):
    parser = sub.add_parser("notification", help="Durable per-channel notification delivery")
    operations = parser.add_subparsers(dest="operation", required=True)
    for op in ("prepare", "claim", "result", "finalize"):
        p = operations.add_parser(op)
        p.add_argument("--release", required=True)
        p.add_argument("--id", required=op != "prepare")
        if op == "prepare":
            p.add_argument("--as-of", default=None, help="Preview clock only; cannot authorize a send")
            p.add_argument("--source", choices=("pending", "digest", "status-email", "step"),
                           default="pending")
            p.add_argument("--phase", default="preflight")
            p.add_argument("--step")
            p.add_argument("--param", action="append", default=[])
            p.add_argument("--force", action="store_true", help="Cadence only; never stop/dedup bypass")
            p.add_argument("--send-to", default=None)
        if op == "claim":
            p.add_argument("--hash", required=True)
            p.add_argument("--executor", required=True)
        if op == "result":
            p.add_argument("--execution-id", required=True)
            p.add_argument("--outcome", choices=("sent", "not_sent", "uncertain"), required=True)
            p.add_argument("--evidence", required=True)
            p.add_argument("--receipt-file")
            p.add_argument("--owner-review", action="store_true",
                           help="Explicit owner review after original runner stopped; include evidence")
        p.set_defaults(func=cmd_notification)
