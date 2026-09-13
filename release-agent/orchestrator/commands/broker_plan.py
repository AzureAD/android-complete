"""Inspect/bind an existing Broker plan; never create, delete, or advance the release."""
from __future__ import annotations

import json

from orchestrator import cli_common as C
from orchestrator.engine import Orchestrator
from orchestrator.evidence import BrokerResource
from orchestrator.outcomes import Done
from orchestrator.step_context import thaw
from orchestrator.transitions import TransitionIntent
from steps.bug_bash import clone_plans_broker as step
from tools import broker_plans as B, testplans as T


def cmd_broker_plan(args):
    st = C.load_state(args.runs_root, args.release)
    orch = Orchestrator(getattr(args, "config", C.DEFAULT_CONFIG), st)
    permit = None
    if args.confirm_not_created or (args.plan_id is not None and not args.preview_ui_repair):
        permit = orch.authorize_outcome(
            TransitionIntent.RECOVER_EVIDENCE, "bug_bash", step.ID,
            execution_id=(st.get_step("bug_bash", step.ID).execution or {}).get("id"))
    context = orch.context("bug_bash", step.ID, permit=permit)
    record = thaw(context.evidence.resources.get(B.RESOURCE, {}))
    step_record = context.evidence.step("bug_bash", step.ID)
    effect_input = (step_record.execution or {}).get("effect_input") or {}
    record_identity = (
        record.get("identity") if isinstance(record, dict) else None
    )
    expected = (
        effect_input.get("identity")
        or record_identity
        or B.identity(st.release_id, T.broker_plan_name(st.release_id))
    )
    name = expected["name"]
    try:
        if not isinstance(record, dict):
            raise ValueError("Invalid Broker resource record; owner recovery required")
        if args.area_path is not None and args.plan_id is None:
            raise ValueError("--area-path requires --plan-id")
        if getattr(args, "preview_ui_repair", False):
            from steps.build_verify._common import latest_rc
            pid = args.plan_id or record.get("plan_id") or (
                st.get_step("bug_bash", step.ID).data or {}).get("plan_id")
            ok, preview, detail = B.preview_ui_repair(pid, latest_rc(context))
            result = {"preview": preview, "error": detail or None}
        elif args.confirm_not_created:
            if "plan_id" in (st.get_step("bug_bash", step.ID).data or {}):
                raise ValueError("Step already references a plan; absence cannot authorize another create")
            ok, detail = B.confirm_not_created(
                st.release_id, name, record, lambda: None, args.reason or "",
                expected_identity=thaw(expected), now=context.clock.utc)
            if ok:
                record["retry_review"]["by"] = st.owner_email
                orch.apply_evidence(permit, Done(updates=(BrokerResource(record),)), checkpoint=True)
                C.elog(args.runs_root, args.release).log(
                    "broker_plan_retry_authorized", source="user", reason=args.reason)
            result = {"retry_authorized": ok, "error": detail or None}
        elif args.plan_id is None:
            ok, candidates, detail = B.find_candidates(expected)
            result = {"resource": record,
                      "step_plan_id": (st.get_step("bug_bash", step.ID).data or {}).get("plan_id"),
                      "candidates": candidates, "error": detail or None}
        else:
            if not args.reason or not args.reason.strip():
                raise ValueError("Binding requires --reason from the release owner's explicit selection")
            ok, pid, detail = B.ensure_plan(
                st.release_id, name, record, lambda: None,
                stored_id=(st.get_step("bug_bash", step.ID).data or {}).get("plan_id", B.MISSING_ID),
                selected_id=args.plan_id, reason=args.reason, area_path=args.area_path,
                allow_create=False, expected_identity=thaw(expected),
                prepared_source=thaw(effect_input.get("source")), now=context.clock.utc)
            if ok:
                record["selection"]["by"] = st.owner_email
                data = step.plan_binding(step_record.data, record, pid, name)
                orch.apply_evidence(
                    permit, Done(updates=(BrokerResource(record), data)), checkpoint=True)
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
