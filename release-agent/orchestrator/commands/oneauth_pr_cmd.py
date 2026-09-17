"""Checked OneAuth ingestion: exact edits only; unreviewable merges are held."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, write_review as W
from orchestrator.git_write_plans import oneauth_plan, execute_oneauth
from steps.lib import mockctx
from steps.finalize import oneauth_common_pr as S
from tools import oneauth as OA


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get("finalize.oneauth_common_pr", {}) or {}


def _record(orch, args, authorization, status, summary):
    orch.record_scout_step("finalize", S.ID, status, summary,
                           execution_id=authorization.execution_id)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[oneauth_common_pr] {summary}", kind="step",
           log_text=summary)


def cmd_create_oneauth_common_pr(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))

    def planner():
        with mockctx.active(_step_mocks(orch)):
            return oneauth_plan(orch.context("finalize", S.ID), args)

    authorization = None
    try:
        if not args.execute and not getattr(args, "reserve", False):
            print(json.dumps(W.preview(orch, "finalize", S.ID, planner()), indent=2))
            return 0
        W.apply_auto_approval(
            args, orch, "finalize", S.ID, planner,
            approved_by="oneauth-common-automation")
        authorization = W.authorize(args, orch, "finalize", S.ID, planner)
        if authorization.reserved_only:
            W.print_reservation(authorization)
            return 0
        summary = execute_oneauth(authorization)
    except Exception as exc:
        summary = f"oneauth_common_pr: {exc}"
        if authorization is not None and not authorization.reserved_only:
            summary += " Earlier operations may have succeeded; resolve the owned execution before retrying."
        print(json.dumps({"error": summary, "permission_to_execute": False}))
        if authorization is None or authorization.reserved_only:
            return 1
        _record(orch, args, authorization, "attention", summary)
        return 2
    _record(orch, args, authorization, "pass", summary)
    print(summary)
    return 0


def register(sub):
    p = sub.add_parser(
        "create-oneauth-common-pr",
        help="Review exact OneAuth edits/PR; execute only an approved review hash")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--execute", action="store_true", help="Execute the approved, checked write plan")
    p.add_argument("--execution-id", help="Active reviewed reservation execution id")
    W.add_auto_approve_argument(
        p,
        help_text="OneAuth Common automation only: compute/checkpoint the current plan "
                  "without human approval, then execute through the normal fenced write path")
    W.add_arguments(p)
    OA.add_review_arguments(p)
    p.set_defaults(func=cmd_create_oneauth_common_pr)
