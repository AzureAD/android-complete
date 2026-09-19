"""Checked pipeline-397224 Release Sign Off stage start."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from orchestrator import cli_common as C, write_review as W
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.step_context import thaw
from steps.rollout_start import signoff_start as S
from tools import pipelines as P

PHASE = "rollout_start"


def _step_mocks(orch):
    return (getattr(orch, "mocks", {}) or {}).get(f"{PHASE}.{S.ID}", {}) or {}


def _run_data(info, *, checked_at=None):
    return {
        key: info.get(key)
        for key in (
            "build_id", "build_number", "source_branch", "source_version",
            "stage_id", "stage_name", "stage_ref", "stage_state", "stage_result",
            "match_basis", "linked_auth_build_ids", "url",
        )
        if info.get(key) is not None
    } | {"last_checked": checked_at or datetime.now(timezone.utc).isoformat(),
         "poll_in_min": S.CONFIG["poll_interval_min"]}


def plan_signoff_start(orch):
    ok, info, detail = S.resolve_target(orch.context(PHASE, S.ID, inputs=_step_mocks(orch)))
    if not ok:
        raise ValueError(f"Could not inspect pipeline 397224: {detail}")
    if not info:
        raise ValueError(f"No Android Build Release run is ready for signoff: {detail}")
    if S.stage_failed(info):
        raise ValueError(
            "Release Sign Off already ran but did not pass "
            f"(result {info.get('stage_result') or 'unknown'}) on build {info.get('build_id')}")
    if S.stage_started(info):
        raise ValueError(f"Release Sign Off is already started on build {info.get('build_id')}")
    build_id = str(info.get("build_id") or "").strip()
    stage_ref = str(info.get("stage_ref") or "").strip()
    if not build_id or not stage_ref:
        raise ValueError("Signoff target is missing build id or stage reference")
    preconditions = {
        "source_branch": info.get("source_branch"),
        "source_version": info.get("source_version"),
        "build_number": info.get("build_number"),
        "status": info.get("status"),
        "result": info.get("result"),
        "stage_id": info.get("stage_id"),
        "stage_name": info.get("stage_name"),
        "stage_state": info.get("stage_state"),
        "stage_result": info.get("stage_result"),
        "match_basis": info.get("match_basis"),
        "linked_auth_build_ids": info.get("linked_auth_build_ids") or [],
    }
    return W.WritePlan(
        S.WRITE_COMMAND,
        {
            "build_id": build_id,
            "stage": info.get("stage_name") or P.AUTH_SIGNOFF_STAGE_NAME,
            "url": info.get("url"),
            "match_basis": info.get("match_basis"),
        },
        (W.WriteOperation(
            "run_build_stage",
            {
                "org": P.AUTH_ORG,
                "project": P.AUTH_PROJECT,
                "definition_id": P.AUTH_SIGNOFF_DEF,
                "build_id": build_id,
                "stage_ref": stage_ref,
            },
            {"state": "pending", "stage": info.get("stage_name") or P.AUTH_SIGNOFF_STAGE_NAME},
            preconditions,
        ),),
    )


def _apply(operation):
    target = thaw(operation.target)
    ok, detail = P.start_auth_signoff_stage(
        target["build_id"],
        target["stage_ref"],
    )
    if not ok:
        raise ValueError(detail)
    ok, current, detail = P.read_auth_signoff_run(target["build_id"])
    if not ok or not current:
        raise ValueError(f"could not verify Release Sign Off start after PATCH: {detail}")
    if current.get("stage_id") != operation.preconditions.get("stage_id"):
        raise ValueError("Release Sign Off stage identity changed during start")
    if S.stage_failed(current):
        raise ValueError(
            "Release Sign Off started but has already failed "
            f"(result {current.get('stage_result')!r})")
    if not (S.stage_running(current) or S.stage_succeeded(current)):
        raise ValueError(
            "Release Sign Off did not enter a started state after PATCH "
            f"(state {current.get('stage_state')!r}, result {current.get('stage_result')!r})")
    return current


def cmd_start_release_signoff(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    authorization = None
    try:
        if args.dry_run and (args.execute or args.reserve):
            raise ValueError("--dry-run cannot be combined with --execute/--reserve.")
        if not (args.execute or args.reserve):
            print(json.dumps(W.preview(orch, PHASE, S.ID, plan_signoff_start(orch)), indent=2))
            return 0
        W.apply_auto_approval(
            args, orch, PHASE, S.ID,
            lambda: plan_signoff_start(orch),
            approved_by="release-signoff-automation")
        authorization = W.authorize(args, orch, PHASE, S.ID, lambda: plan_signoff_start(orch))
        if authorization.reserved_only:
            W.print_reservation(authorization)
            return 0
        args.execution_id = authorization.execution_id
        authorization.validate()
        current = _apply(authorization.plan.operations[0])
    except Exception as exc:
        message = f"signoff_start: {exc}"
        print(json.dumps({"error": message, "permission_to_execute": False}))
        if authorization is None or authorization.reserved_only:
            return 1
        orch.settle_execution(PHASE, S.ID, authorization.execution_id, Blocked(
            message + " Earlier operations may have succeeded; inspect the run before retrying."))
        C.save_state(orch.state, args.runs_root, args.release)
        return 2
    links = [{"name": "Release Sign Off run", "url": current.get("url")}] if current.get("url") else []
    if S.stage_succeeded(current):
        summary = f"signoff_start: Release Sign Off completed on build {current.get('build_id')}."
        outcome = Done(summary, by="scout", links=links)
    else:
        summary = (f"signoff_start: Release Sign Off started on build {current.get('build_id')} "
                   f"({current.get('stage_state') or 'pending'}); Scout will poll until it finishes.")
        outcome = InProgress(summary, links=links, poll_in_min=S.CONFIG["poll_interval_min"])
    orch.settle_execution(PHASE, S.ID, authorization.execution_id, outcome, data=_run_data(current))
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[{S.ID}] {summary}", kind="step", log_text=summary)
    print(summary)
    return 0


def register(sub):
    p = sub.add_parser(
        "start-release-signoff",
        help="Start the Release Sign Off stage on the matching pipeline-397224 run")
    p.add_argument("--release", required=True)
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.add_argument("--dry-run", action="store_true", help="Preview only (default behavior)")
    p.add_argument("--execute", action="store_true", help="Execute the approved, checked stage start")
    p.add_argument("--execution-id", help="Active reviewed reservation execution id")
    W.add_auto_approve_argument(
        p,
        help_text="Release signoff automation only: compute/checkpoint the current plan "
                  "without human approval, then execute through the normal fenced write path")
    W.add_arguments(p)
    p.set_defaults(func=cmd_start_release_signoff)
