"""Checked, preview-first integration/freeze PR writes."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, write_review as W
from orchestrator.git_write_plans import integration_plan, execute_integration
from steps.lib import mockctx
from steps.finalize import integ_prs as S


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.integ_prs", {}) or {}


def cmd_create_integration_prs(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))

    def planner():
        with mockctx.active(_step_mocks(orch)):
            return integration_plan(orch.context("finalize", S.ID), args)

    authorization = None
    try:
        if not args.execute and not getattr(args, "reserve", False):
            print(json.dumps(W.preview(orch, "finalize", S.ID, planner()), indent=2))
            return 0
        W.apply_auto_approval(
            args, orch, "finalize", S.ID, planner,
            approved_by="integration-pr-automation")
        authorization = W.authorize(args, orch, "finalize", S.ID, planner)
        if authorization.reserved_only:
            W.print_reservation(authorization)
            return 0
        summary = execute_integration(authorization)
    except Exception as exc:
        summary = f"integ_prs: {exc}"
        if authorization is not None and not authorization.reserved_only:
            summary += " Earlier operations may have succeeded; resolve the owned execution before retrying."
        print(json.dumps({"error": summary, "permission_to_execute": False}))
        if authorization is None or authorization.reserved_only:
            return 1
        orch.record_scout_step("finalize", S.ID, "attention", summary,
                               execution_id=authorization.execution_id)
        C.save_state(orch.state, args.runs_root, args.release)
        return 2
    orch.record_scout_step("finalize", S.ID, "pass", summary,
                           execution_id=authorization.execution_id)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[integ_prs] {summary}", kind="step", log_text=summary)
    print(summary)
    return 0


def cmd_recover_integration_prs(args):
    from orchestrator import integration_recovery as recovery

    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        if not args.execute:
            print(json.dumps(recovery.preview(orch, args), indent=2))
            return 0
        print(recovery.execute(orch, args))
        return 0
    except Exception as exc:
        # Never save an uncheckpointed attention result after a checkpoint failed.
        print(json.dumps({"error": str(exc), "permission_to_execute": False,
                          "note": "Original owner retained; uncertain operations cannot be blindly replayed."}))
        return 2 if args.execute else 1


def register(sub):
    p = sub.add_parser(
        "create-integration-prs",
        help="Review exact integration/freeze writes; execute only an approved review hash")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true", help="Execute the approved, checked write plan")
    p.add_argument("--repos", nargs="*", default=None,
                   help="Limit to these repo keys (common msal broker authenticator)")
    p.add_argument("--checkout-root", default=None,
                   help="Absolute directory containing separate clean common/msal/broker/authenticator checkouts")
    p.add_argument("--resolutions-file", default=None,
                   help="Absolute version-1 JSON file pinning explicit MSAL/Auth integration conflict choices")
    p.add_argument("--pbi", default=None, help="Reuse this PBI id instead of creating one")
    p.add_argument("--pbi-title", default=None, help="Title for the created PBI")
    p.add_argument("--execution-id", help="Active reviewed reservation execution id")
    W.add_auto_approve_argument(
        p,
        help_text="Integration-PR automation only: compute/checkpoint the current plan "
                  "without human approval, then execute through the normal fenced write path")
    W.add_arguments(p)
    p.set_defaults(func=cmd_create_integration_prs)
    r = sub.add_parser("recover-integration-prs",
                       help="Reconcile the frozen approved plan; manually resume only its owned suffix")
    r.add_argument("--release", required=True)
    r.add_argument("--as-of", default=None)
    r.add_argument("--execution-id", required=True)
    r.add_argument("--executor", required=True)
    r.add_argument("--original-preview", required=True,
                   help="Original exact approved preview JSON, not a new remaining-PR plan")
    r.add_argument("--existing-pbi", required=True,
                   help="Explicit existing PBI to verify; recovery never creates another")
    r.add_argument("--execute", action="store_true")
    r.add_argument("--recovery-hash", help="Exact fresh recovery preview hash manually approved")
    r.add_argument("--broker-target-tip",
                   help="Exact explicitly reviewed fast-forward Broker dev tip; replan only pending RI dependencies")
    r.add_argument("--approved-by", help="Manual reviewer of receipts and displayed content differences")
    r.add_argument("--confirm-no-inflight", action="store_true",
                   help="Owner confirms the original runner and provider operations have stopped")
    r.set_defaults(func=cmd_recover_integration_prs)
