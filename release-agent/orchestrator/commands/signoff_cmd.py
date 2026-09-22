"""Checked pipeline-397224 Release Sign Off stage start."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, schedule, write_review as W
from orchestrator.outcomes import Blocked
from orchestrator.step_context import thaw
from steps.rollout_start import beta_play_store, signoff_start, upload_alpha, upload_whats_new
from tools import pipelines as P
from tools import checks

PHASE = "rollout_start"
S = signoff_start

STAGE_SPECS = {
    signoff_start.WRITE_COMMAND: {
        "step": signoff_start,
        "description": "Start the Release Sign Off stage on the matching pipeline-397224 run",
    },
    upload_whats_new.WRITE_COMMAND: {
        "step": upload_whats_new,
        "description": "Start the Upload What's New stage on the matching pipeline-397224 run",
    },
    upload_alpha.WRITE_COMMAND: {
        "step": upload_alpha,
        "description": "Start the Upload Alpha stage on the matching pipeline-397224 run",
    },
    beta_play_store.WRITE_COMMAND: {
        "step": beta_play_store,
        "description": "Start the 100% Beta - Play Store stage after release-owner approval",
    },
}


class StageStartError(ValueError):
    def __init__(self, message, *, may_have_written):
        super().__init__(message)
        self.may_have_written = may_have_written


class VerifiedStageFailure(ValueError):
    pass


def _step_mocks(orch, step):
    return (getattr(orch, "mocks", {}) or {}).get(f"{PHASE}.{step.ID}", {}) or {}


def _record(orch, args, step, status, summary, *, url=None):
    orch.record_scout_step(
        PHASE, step.ID, status, summary,
        execution_id=getattr(args, "execution_id", None))
    if url:
        orch.annotate_step(PHASE, step.ID, links=[{"name": f"{step.STAGE_NAME} run", "url": url}], by="scout")
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[{step.ID}] {summary}", kind="step", log_text=summary)


def _plan_stage_start(
    orch,
    step,
    *,
    manager_approved_by=None,
    policy_now=None,
):
    manager_approved_by = (
        step.manager_approval(
            orch.context(PHASE, step.ID), manager_approved_by,
            now=policy_now)
        if getattr(step, "REQUIRES_OWNER_APPROVAL", False)
        else None
    )
    ok, info, detail = step.resolve_target(orch.context(PHASE, step.ID, inputs=_step_mocks(orch, step)))
    if not ok:
        raise ValueError(f"Could not inspect pipeline 397224: {detail}")
    if not info:
        raise ValueError(f"No Android Build Release run is ready for {step.STAGE_NAME}: {detail}")
    if step.stage_failed(info):
        raise ValueError(
            f"{step.STAGE_NAME} already ran but did not pass "
            f"(result {info.get('stage_result') or 'unknown'}) on build {info.get('build_id')}")
    if step.stage_started(info):
        raise ValueError(f"{step.STAGE_NAME} is already started on build {info.get('build_id')}")
    build_id = str(info.get("build_id") or "").strip()
    stage_ref = str(info.get("stage_ref") or "").strip()
    if not build_id or not stage_ref:
        raise ValueError(f"{step.STAGE_NAME} target is missing build id or stage reference")
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
    parameters = {
        "build_id": build_id,
        "stage": info.get("stage_name") or step.STAGE_NAME,
        "url": info.get("url"),
        "match_basis": info.get("match_basis"),
    }
    if getattr(step, "REQUIRES_OWNER_APPROVAL", False):
        parameters.update(
            start_date=(policy_now or orch.now_local).date().isoformat(),
            manager_approved_by=manager_approved_by,
        )
    return W.WritePlan(
        step.WRITE_COMMAND,
        parameters,
        (W.WriteOperation(
            "run_build_stage",
            {
                "org": P.AUTH_ORG,
                "project": P.AUTH_PROJECT,
                "definition_id": P.AUTH_SIGNOFF_DEF,
                "build_id": build_id,
                "stage_ref": stage_ref,
            },
            {"state": "pending", "stage": info.get("stage_name") or step.STAGE_NAME},
            preconditions,
        ),),
    )


def plan_signoff_start(orch):
    return _plan_stage_start(orch, signoff_start)


def _apply(operation, step):
    target = thaw(operation.target)
    result = P.start_auth_signoff_stage(
        target["build_id"],
        target["stage_ref"],
    )
    ok, detail = result[:2]
    may_have_written = result[2] if len(result) > 2 else True
    if not ok:
        raise StageStartError(detail, may_have_written=may_have_written)
    ok, current, detail = P.read_auth_signoff_run(target["build_id"], stage_name=step.STAGE_NAME)
    if not ok or not current:
        raise ValueError(f"could not verify {step.STAGE_NAME} start after PATCH: {detail}")
    if current.get("stage_id") != operation.preconditions.get("stage_id"):
        raise ValueError(f"{step.STAGE_NAME} stage identity changed during start")
    if step.stage_failed(current):
        raise VerifiedStageFailure(
            f"{step.STAGE_NAME} started but has already failed "
            f"(result {current.get('stage_result')!r})")
    if not step.stage_started(current):
        raise ValueError(
            f"{step.STAGE_NAME} did not enter a started state after PATCH "
            f"(state {current.get('stage_state')!r}, result {current.get('stage_result')!r})")
    return current


def _validate_owner_execution_policy(state, step, plan):
    if not getattr(step, "REQUIRES_OWNER_APPROVAL", False):
        return
    owner = str(state.owner_email or "").strip().casefold()
    signed_in = str(checks.current_az_user() or "").strip().casefold()
    if not owner or signed_in != owner:
        raise ValueError(
            f"{step.STAGE_NAME} execution requires the release owner to be signed "
            f"in to Azure CLI ({state.owner_email})")
    current = schedule.now_local(schedule.get_tz(state.timezone))
    reviewed_date = str(plan.parameters.get("start_date") or "")
    if current.date().isoformat() != reviewed_date:
        raise ValueError(
            f"{step.STAGE_NAME} owner-local date changed after review; preview and approve "
            "a fresh request")
    if current.weekday() == 4 and not str(
            plan.parameters.get("manager_approved_by") or "").strip():
        raise ValueError(
            f"{step.STAGE_NAME} cannot start on Friday without manager approval")


def _cmd_start_stage(args, step):
    if (
        getattr(step, "REQUIRES_OWNER_APPROVAL", False)
        and (args.execute or args.reserve)
        and getattr(args, "as_of", None)
    ):
        print(json.dumps({
            "error": (
                f"{step.ID}: --as-of is preview-only; owner-approved execution uses "
                "the trusted current date in the release owner's timezone"),
            "permission_to_execute": False,
        }))
        return 1
    state, orch = C.load_orch(
        args.runs_root, args.release, args.config, C.parse_as_of(args))
    authorization = None
    provider_attempted = False

    def planner():
        policy_now = (
            schedule.now_local(schedule.get_tz(state.timezone))
            if (
                getattr(step, "REQUIRES_OWNER_APPROVAL", False)
                and (args.execute or args.reserve)
            )
            else orch.now_local
        )
        return _plan_stage_start(
            orch, step,
            manager_approved_by=getattr(args, "manager_approved_by", None),
            policy_now=policy_now,
        )
    try:
        if args.dry_run and (args.execute or args.reserve):
            raise ValueError("--dry-run cannot be combined with --execute/--reserve.")
        if not (args.execute or args.reserve):
            print(json.dumps(W.preview(orch, PHASE, step.ID, planner()), indent=2))
            return 0
        if getattr(step, "REQUIRES_OWNER_APPROVAL", False):
            if getattr(args, "auto_approve", False):
                raise ValueError(
                    f"{step.STAGE_NAME} requires release-owner approval; auto-approval is forbidden")
            owner = str(state.owner_email or "").strip().casefold()
            reviewer = str(getattr(args, "approved_by", None) or "").strip().casefold()
            if not owner or reviewer != owner:
                raise ValueError(
                    f"{step.STAGE_NAME} must be approved by the release owner ({state.owner_email})")
            signed_in = str(checks.current_az_user() or "").strip().casefold()
            if signed_in != owner:
                raise ValueError(
                    f"{step.STAGE_NAME} execution requires the release owner to be signed "
                    f"in to Azure CLI ({state.owner_email})")
        else:
            W.apply_auto_approval(
                args, orch, PHASE, step.ID, planner,
                approved_by=f"{step.AUTOMATION_LABEL}-automation")
        authorization = W.authorize(args, orch, PHASE, step.ID, planner)
        if authorization.reserved_only:
            W.print_reservation(authorization)
            return 0
        args.execution_id = authorization.execution_id
        authorization.validate()
        _validate_owner_execution_policy(state, step, authorization.plan)
        provider_attempted = True
        current = _apply(authorization.plan.operations[0], step)
        authorization.validate()
    except Exception as exc:
        if isinstance(exc, StageStartError):
            provider_attempted = exc.may_have_written
        message = f"{step.ID}: {exc}"
        print(json.dumps({"error": message, "permission_to_execute": False}))
        if authorization is None or authorization.reserved_only:
            return 1
        if isinstance(exc, VerifiedStageFailure):
            _record(
                orch, args, step, "attention", message,
                url=authorization.plan.parameters.get("url"))
            return 1
        if not provider_attempted:
            orch.settle_execution(
                PHASE,
                step.ID,
                authorization.execution_id,
                Blocked(message + " No provider write was attempted; reopen for a fresh review."),
            )
            C.save_state(orch.state, args.runs_root, args.release)
            return 1
        _record(orch, args, step, "attention",
                message + " Earlier operations may have succeeded; inspect the run before retrying.",
                url=authorization.plan.parameters.get("url"))
        return 2
    summary = (f"{step.ID}: started {step.STAGE_NAME} on build {current.get('build_id')} "
               f"({current.get('stage_state') or 'queued'}).")
    _record(orch, args, step, "pass", summary, url=current.get("url"))
    print(summary)
    return 0


def cmd_start_release_signoff(args):
    return _cmd_start_stage(args, signoff_start)


def register(sub):
    for command, spec in STAGE_SPECS.items():
        step = spec["step"]
        p = sub.add_parser(command, help=spec["description"])
        p.add_argument("--release", required=True)
        p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
        p.add_argument("--dry-run", action="store_true", help="Preview only (default behavior)")
        p.add_argument("--execute", action="store_true", help="Execute the approved, checked stage start")
        p.add_argument("--execution-id", help="Active reviewed reservation execution id")
        if getattr(step, "REQUIRES_OWNER_APPROVAL", False):
            p.add_argument(
                "--manager-approved-by",
                help="Manager identity authorizing a Friday release start override")
        W.add_auto_approve_argument(
            p,
            help_text=f"{step.STAGE_NAME} automation only: compute/checkpoint the current plan "
                      "without human approval, then execute through the normal fenced write path")
        W.add_arguments(p)
        p.set_defaults(func=lambda args, selected=step: _cmd_start_stage(args, selected))
