"""Inspect/bind an existing Broker plan; never create, delete, or advance the release."""
from __future__ import annotations

import json

from orchestrator import cli_common as C
from steps.bug_bash import clone_plans_broker as step
from tools import broker_plans as B, testplans as T


def cmd_broker_plan(args):
    st = C.load_state(args.runs_root, args.release)
    name = T.broker_plan_name(st.release_id)
    record = st.resources.get(B.RESOURCE, {})
    try:
        if not isinstance(record, dict):
            raise ValueError("Invalid Broker resource record; owner recovery required")
        if args.area_path is not None and args.plan_id is None:
            raise ValueError("--area-path requires --plan-id")
        if getattr(args, "preview_ui_repair", False):
            from steps.build_verify._common import latest_rc
            pid = args.plan_id or record.get("plan_id") or (
                st.get_step("bug_bash", step.ID).data or {}).get("plan_id")
            ok, preview, detail = B.preview_ui_repair(pid, latest_rc(st))
            result = {"preview": preview, "error": detail or None}
        elif args.confirm_not_created:
            if "plan_id" in (st.get_step("bug_bash", step.ID).data or {}):
                raise ValueError("Step already references a plan; absence cannot authorize another create")
            ok, detail = B.confirm_not_created(
                st.release_id, name, record, st.checkpoint, args.reason or "")
            if ok:
                record["retry_review"]["by"] = st.owner_email
                st.checkpoint()
                C.elog(args.runs_root, args.release).log(
                    "broker_plan_retry_authorized", source="user", reason=args.reason)
            result = {"retry_authorized": ok, "error": detail or None}
        elif args.plan_id is None:
            ok, candidates, detail = B.find_candidates(B.identity(st.release_id, name))
            result = {"resource": record,
                      "step_plan_id": (st.get_step("bug_bash", step.ID).data or {}).get("plan_id"),
                      "candidates": candidates, "error": detail or None}
        else:
            if not args.reason or not args.reason.strip():
                raise ValueError("Binding requires --reason from the release owner's explicit selection")
            st.resources[B.RESOURCE] = record
            ok, pid, detail = B.ensure_plan(
                st.release_id, name, record, st.checkpoint,
                stored_id=(st.get_step("bug_bash", step.ID).data or {}).get("plan_id", B.MISSING_ID),
                selected_id=args.plan_id, reason=args.reason, area_path=args.area_path, allow_create=False)
            if ok:
                record["selection"]["by"] = st.owner_email
                step.record_plan(st, pid, name)
                st.checkpoint()
                C.elog(args.runs_root, args.release).log(
                    "broker_plan_bound", source="user", plan_id=pid, reason=args.reason)
            result = {"plan_id": pid, "bound": ok, "error": detail or None}
    except ValueError as exc:
        ok, result = False, {"error": str(exc)}
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


def register(sub):
    p = sub.add_parser("broker-plan", help="Inspect or bind an existing Broker release plan (no ADO writes)")
    p.add_argument("--release", required=True)
    action = p.add_mutually_exclusive_group()
    action.add_argument("--plan-id", type=int, help="Owner-selected existing plan; cannot replace a bound plan")
    action.add_argument("--confirm-not-created", action="store_true",
                        help="Owner confirmed interrupted create did not occur; verifies no candidate exists")
    p.add_argument("--reason", help="Owner-confirmed reason for selecting this existing plan")
    p.add_argument("--area-path", help="Explicitly reviewed existing plan area if different from configuration")
    p.add_argument("--preview-ui-repair", action="store_true",
                   help="Read-only source/old/new point preview; no bind, cleanup, state save or ADO writes")
    p.set_defaults(func=cmd_broker_plan)
