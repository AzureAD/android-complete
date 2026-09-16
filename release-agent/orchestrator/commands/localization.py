"""Localization poll commands — the recorder/decider seam for the Phase-1
`localization` step (P1-2).

The step's logic lives in `steps/ccd/localization.py` (trigger + pure `decide`).
These commands are the thin CLI seam the skill/poller calls:

  * launch-localization — review/reserve a concrete source/target/parameter plan;
    fence a single trigger and verify its actual provider receipt before attachment.
  * record-localization-run — recover only a receipt matching an already-started
    launch review (leaves it IN-FLIGHT, not done).
  * check-localization — one poll: given the run or PR state, apply `decide()` and
    either wait, request a notification, or finish. Prints the decision JSON so the
    poller can claim and acknowledge each notification through the shared delivery protocol.
  * record-localization-post — legacy readback of an already acknowledged Code reviews
    post; a PR identifier alone never proves delivery.
"""
from __future__ import annotations
import json as _json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from orchestrator import cli_common as C, delivery as D, revision, write_review as W
from orchestrator import mocks as mocks_mod
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.transitions import TransitionIntent
from steps.lib.context import SELF_CHAT_ID
from steps.ccd import localization as L
from tools import localization as provider


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _launch_config():
    cfg = deepcopy(L.CONFIG)
    inputs = mocks_mod.load_mocks().get("ccd.localization", {}) or {}
    if "create_pr" in inputs:
        value = str(inputs["create_pr"]).strip().lower()
        if value not in ("true", "false", "1", "0", "yes", "no"):
            raise ValueError("Localization create_pr override must explicitly be true or false")
        cfg["variables"]["isCreatePrSelected"] = "true" if value in ("true", "1", "yes") else "false"
    return cfg


def plan_localization(args, orch):
    step = orch.state.get_step("ccd", L.ID)
    if step.data.get("build_id") and not (
            step.invalidated_at and
            (not step.execution or (step.status == "running" and step.execution.get("refresh")))):
        raise ValueError("Localization already has a run; an explicit owner-reviewed reopen is required")
    return provider.plan_launch(
        orch, _launch_config(), source_branch=args.branch, source_version=args.source_version,
        overrides=args.variable)


def _receipt_owner(orch, execution_id):
    revision.assert_current(orch)
    step = orch.state.get_step("ccd", L.ID)
    execution = step.execution or {}
    if (not execution_id or execution.get("id") != execution_id
            or step.status not in ("in_flight", "blocked")
            or not revision.is_hash((execution.get("write_review") or {}).get("hash"))):
        raise ValueError("Localization receipt requires the exact already-started reviewed execution")
    return step


def _attach_run(args, orch, build, *, authorization=None):
    step = _receipt_owner(orch, args.execution_id)
    plan = provider.receipt_plan(orch, _launch_config(), build)
    if W.review_hash(orch, "ccd", L.ID, plan) != step.execution["write_review"]["hash"]:
        raise ValueError("Localization build receipt does not match the stored launch review")
    if authorization is not None and plan.as_dict() != authorization.plan.as_dict():
        raise ValueError("Localization build receipt differs from the authorized launch")
    started_at = provider.receipt_time(build, step)
    target = plan.operations[0].target
    build_id = str(build["id"])
    run_url = f"{target['org']}/{target['project']}/_build?buildId={build_id}"
    supplied_start = getattr(args, "started_at", None)
    if supplied_start and datetime.fromisoformat(supplied_start.replace("Z", "+00:00")) != datetime.fromisoformat(started_at):
        raise ValueError("--started-at does not match the provider queue time")
    if getattr(args, "run_url", None) not in (None, run_url):
        raise ValueError("--run-url does not match the verified provider build URL; omit it to use readback")
    data = deepcopy(step.data)
    if data.get("build_id") and str(data["build_id"]) == build_id:
        print(_json.dumps({"recorded": False, "reason": "build already recorded"}))
        return 0
    if data.get("build_id") and step.execution.get("refresh"):
        try:
            previous_start = datetime.fromisoformat(str(data.get("started_at", "")).replace("Z", "+00:00"))
            execution_start = datetime.fromisoformat(step.execution["started_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Previous localization run lacks valid ownership timing; owner review required") from exc
        if previous_start.tzinfo is None or previous_start >= execution_start:
            raise ValueError("A different localization build already belongs to this execution")
        prior = {
            key: data.get(key)
            for key in (
                "build_id", "started_at", "run_url", "pr_id", "pr_url",
                "pr_status", "pipeline_complete",
                "last_checked", "pr_discovered_at", "pr_announced_at",
                "merge_deadline_alert_at", "timeout_notified_at",
                "in_flight_since", "poll_in_min",
            )
            if data.get(key) is not None
        }
        if prior:
            data.setdefault("previous_runs", []).append(prior)
        for key in (
            "build_id", "started_at", "run_url", "pr_id", "pr_url", "pr_status",
            "pipeline_complete", "last_checked", "pr_discovered_at",
            "pr_announced_at", "merge_deadline_alert_at", "timeout_notified_at",
            "in_flight_since", "poll_in_min",
        ):
            data.pop(key, None)
    if data.get("build_id"):
        raise ValueError("A different localization build is already recorded; owner review required")
    data.update(build_id=build_id, started_at=started_at, run_url=run_url)
    orch.settle_execution(
        "ccd", L.ID, args.execution_id,
        InProgress("Verified localization launch; Scout is polling its outcome.",
                   links=[{"name": "Localization run", "url": run_url}],
                   poll_in_min=L.CONFIG["poll_interval_min"]), data=data)
    C.save_state(orch.state, args.runs_root, args.release)
    print(_json.dumps({"recorded": True, "build_id": build_id, "run_url": run_url}))
    return 0


def cmd_launch_localization(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        if args.dry_run and (args.execute or args.reserve):
            raise ValueError("--dry-run cannot be combined with --execute/--reserve")
        if not (args.execute or args.reserve):
            print(_json.dumps(W.preview(orch, "ccd", L.ID, plan_localization(args, orch)), indent=2))
            return 0
        authorization = W.authorize(args, orch, "ccd", L.ID, lambda: plan_localization(args, orch))
    except ValueError as exc:
        print(_json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1
    if authorization.reserved_only:
        W.print_reservation(authorization)
        return 0
    try:
        authorization.validate()
        build = provider.trigger(authorization.plan.operations[0])
        authorization.validate()
        return _attach_run(args, orch, build, authorization=authorization)
    except Exception as exc:
        note = (f"Localization launch uncertain: {exc}. Do not trigger again. Inspect ADO and use "
                "record-localization-run for a matching receipt, or explicit owner resolution.")
        orch.settle_execution("ccd", L.ID, authorization.execution_id, Blocked(note))
        C.save_state(orch.state, args.runs_root, args.release)
        print(_json.dumps({"error": note, "execution_id": authorization.execution_id}))
        return 2


def cmd_record_localization_run(args):
    """Recover only a provider-verified receipt belonging to an already-reviewed launch."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        _receipt_owner(orch, args.execution_id)
        cfg = _launch_config()
        build = provider.read_build(cfg["org"], cfg["project"], args.build_id)
        return _attach_run(args, orch, build)
    except (ValueError, TypeError) as exc:
        print(_json.dumps({"error": str(exc), "recorded": False}))
        return 1


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "complete", "completed", "succeeded")


def cmd_check_localization(args):
    """One poll of the localization run. Reads the stored start time, applies the
    deterministic decision, records terminal state, and prints the decision JSON."""
    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(_json.dumps({"error": f"bad --now: {args.now!r}"}))
            return 1

    st, orch = C.load_orch(args.runs_root, args.release, args.config, now or C.parse_as_of(args))
    now = now or orch.now_local
    step = st.get_step("ccd", "localization")
    scope = {"kind": "step", "phase": "ccd", "step": "localization"}
    reason = D.scope_reason(orch, scope)
    if reason:
        print(_json.dumps({"decision": "stopped", "note": reason, "notifications": []}))
        return 0
    execution = step.execution or {}
    if (
        not execution
        or not getattr(args, "execution_id", None)
        or execution.get("id") != args.execution_id
    ):
        print(_json.dumps({
            "error": "Localization poll does not own the active execution."
        }))
        return 1

    # Guard: nothing to poll if it wasn't triggered, or it's already terminal.
    if not step.data.get("started_at"):
        print(_json.dumps({"decision": "not_started",
                           "note": "localization has not been triggered yet"}))
        return 0
    if step.status in ("done", "skipped", "blocked"):
        print(_json.dumps({"decision": "already_final", "status": step.status}))
        return 0
    try:
        permit = orch.authorize_outcome(
            TransitionIntent.POLL, "ccd", "localization",
            execution_id=args.execution_id,
        )
    except ValueError as exc:
        print(_json.dumps({"error": str(exc)}))
        return 1

    if getattr(args, "complete", None) is None and getattr(args, "pr_status", None) is None:
        print(_json.dumps(L.poll_target(orch.context("ccd", "localization"))))
        return 0

    logs = args.logs
    if logs is None and args.logs_file:
        try:
            with open(args.logs_file, "r", encoding="utf-8") as fh:
                logs = fh.read()
        except (OSError, UnicodeError):
            logs = None  # Missing evidence follows the same bounded timeout/owner review.

    try:
        decision = L.decide(
            orch.context("ccd", "localization"), is_complete=_truthy(args.complete), logs=logs, now=now,
            pr_status=getattr(args, "pr_status", None),
            evidence=L.RunEvidence(
                result=getattr(args, "run_result", None),
                logs_complete=getattr(args, "logs_complete", False),
                no_change_confirmation=getattr(args, "no_change_confirmation", None)))
    except ValueError as e:
        print(_json.dumps({"error": str(e)}))
        return 1
    d = decision["decision"]
    # Completed status is monotonic, but does not prove a successful result.
    step.data["pipeline_complete"] = bool(
        step.data.get("pipeline_complete") or _truthy(args.complete) or decision.get("pr_id"))
    if decision.get("pr_id"):
        step.data.update(pr_id=decision["pr_id"], pr_url=decision["pr_url"])
    if getattr(args, "pr_status", None) is not None:
        step.data["pr_status"] = str(args.pr_status).strip().lower()

    # mocks.local.yaml send_to → redirect localization PR posts to your own chat.
    if d in ("announce_pr", "warn_unmerged") and decision.get("chat"):
        spec = mocks_mod.load_mocks().get("ccd.localization") or {}
        if "send_to" in spec:
            val = spec["send_to"]
            val = {"me": SELF_CHAT_ID, "self": SELF_CHAT_ID}.get(val, val)
            decision["chat"]["chatId"] = val
            decision["test_redirect"] = {"send_to": val}

    if d == "failed":
        step.data["last_checked"] = now.isoformat()
        orch.apply_outcome(
            permit, Blocked(decision["note"], links=decision["links"], by="scout"),
            data=step.data)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] localization: {decision['note']}",
               kind="localization")
    elif d in ("wait", "wait_for_merge"):
        # Not terminal — keep in-flight, just record progress on the step.
        step.data["last_checked"] = now.isoformat() if now else _now_iso()
        orch.apply_outcome(
            permit, InProgress(decision["note"], links=decision.get("links", step.links),
                               poll_in_min=L.CONFIG["poll_interval_min"]), data=step.data)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[localization] {decision['note']}", kind="localization")
    elif d == "timeout":
        # Keep the worker alive until its required escalation is acknowledged.
        orch.apply_outcome(
            permit, InProgress(
                "localization timeout; required owner notification awaiting delivery",
                links=decision.get("links", step.links),
                poll_in_min=L.CONFIG["poll_interval_min"]), data=step.data)
    elif d == "announce_pr":
        step.data["pr_id"] = decision["pr_id"]
        step.data["pr_url"] = decision["pr_url"]
        step.data["last_checked"] = now.isoformat()
        step.data.setdefault("pr_discovered_at", now.isoformat() if now else _now_iso())
        orch.apply_outcome(
            permit, InProgress(decision["note"], links=decision.get("links", []),
                               poll_in_min=L.CONFIG["poll_interval_min"]), data=step.data)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[localization] {decision['note']}",
               kind="localization")
    elif d == "warn_unmerged":
        step.data["last_checked"] = now.isoformat() if now else _now_iso()
        orch.apply_outcome(
            permit, InProgress(decision["note"], links=step.links,
                               poll_in_min=L.CONFIG["poll_interval_min"]), data=step.data)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] localization: {decision['note']}",
               kind="localization")
    elif d == "omit_unmerged":
        orch.omit_execution(permit, decision["note"], links=decision.get("links", step.links))
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[omitted] localization: {decision['note']}",
               kind="localization")
    elif d in ("merged", "complete_none"):
        orch.apply_outcome(
            permit, Done(decision["note"], by="scout", links=decision.get("links", step.links)),
            data=step.data)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[ok] localization: {decision['note']}",
               kind="localization")
    else:
        print(_json.dumps({"error": f"unsupported localization decision: {d}"}))
        return 1

    if d in ("timeout", "announce_pr", "warn_unmerged"):
        step = st.get_step("ccd", "localization")
        scope["generation"] = step.invalidated_at or "initial"
        scope["statuses"] = ["in_flight"]
        scope["step_matches"] = {"build_id": step.data.get("build_id")}
        if d == "timeout":
            scope["step_matches"].update(
                started_at=step.data["started_at"], pr_id=step.data.get("pr_id"),
                pipeline_complete=step.data["pipeline_complete"],
                last_checked=step.data.get("last_checked"))
            started = datetime.fromisoformat(step.data["started_at"].replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            scope["not_before"] = (started + timedelta(hours=L.CONFIG["timeout_hours"])).isoformat()
        else:
            scope["step_matches"]["pr_id"] = step.data.get("pr_id")
            deadline_cfg = {**L.CONFIG, "merge_deadline_local": L.CONFIG["omission_deadline_local"]}
            scope["expires_at"] = L.merge_deadline(
                orch.context("ccd", "localization"), deadline_cfg).isoformat()
        scope["release_matches"] = {"owner_email": st.owner_email, "ccd": st.ccd}
        completion = ({"kind": "step_result", "status": "attention", "note": decision["note"],
                       "links": decision.get("links", step.links),
                       "stamp": ["timeout_notified_at"],
                       "execution_id": (step.execution or {}).get("id")} if d == "timeout" else
                      {"kind": "step_data", "stamp": [
                          "pr_announced_at" if d == "announce_pr" else "merge_deadline_alert_at"]})
        checkpoint = f"localization:{step.data.get('build_id')}:{decision.get('pr_id', '')}:{d}"
        item = D.descriptor(st, checkpoint, scope,
                            "workiq_send_email" if d == "timeout" else "workiq_send_chat_message",
                            decision.get("email") if d == "timeout" else decision["chat"],
                            completion)
        decision["notifications"] = [D.offer(orch, item)]
        decision["permission_to_send"] = False
        C.save_state(st, args.runs_root, args.release)
    print(_json.dumps(decision))
    return 0


def cmd_record_localization_post(args):
    """Record a localization Code reviews post only after delivery succeeds."""
    st = C.load_state(args.runs_root, args.release)
    step = st.get_step("ccd", "localization")
    stored_pr_id = str(step.data.get("pr_id") or "")
    if not stored_pr_id:
        print(_json.dumps({"error": "localization PR has not been discovered"}))
        return 1
    if stored_pr_id != str(args.pr_id):
        print(_json.dumps({
            "error": f"PR mismatch: step has {stored_pr_id}, acknowledgement has {args.pr_id}"
        }))
        return 1

    key = "pr_announced_at" if args.kind == "initial" else "merge_deadline_alert_at"
    already = step.data.get(key)
    if not already:
        print(_json.dumps({"error": "Use notification claim/result; a PR ID alone does not prove delivery"}))
        return 1
    print(_json.dumps({
        "recorded": not bool(already), "kind": args.kind, "pr_id": stored_pr_id,
        "at": already or step.data[key],
    }))
    return 0


def register(sub):
    launch = sub.add_parser("launch-localization", help="Preview/reserve/execute a checked localization launch")
    launch.add_argument("--release", required=True)
    launch.add_argument("--as-of", default=None)
    launch.add_argument("--branch", default=None, help="Source branch; defaults to the pipeline's branch")
    launch.add_argument("--source-version", default=None, help="Full commit; must match the reviewed branch head")
    launch.add_argument("--variable", action="append", default=[], help="Queue variable NAME=VALUE")
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument("--execute", action="store_true")
    launch.add_argument("--execution-id", default=None)
    W.add_arguments(launch)
    launch.set_defaults(func=cmd_launch_localization)
    rr = sub.add_parser("record-localization-run",
                        help="Recover a provider-verified receipt for an already reviewed localization launch")
    rr.add_argument("--release", required=True)
    rr.add_argument("--as-of", default=None)
    rr.add_argument("--build-id", required=True, dest="build_id")
    rr.add_argument("--run-url", default=None, dest="run_url")
    rr.add_argument("--execution-id", required=True, dest="execution_id",
                    help="Active reserve-step execution id")
    rr.add_argument("--started-at", default=None, dest="started_at",
                    help="Optional assertion of the provider's exact queue timestamp")
    rr.set_defaults(func=cmd_record_localization_run)

    cl = sub.add_parser("check-localization",
                        help="One localization poll: run status/result plus PR status after discovery")
    cl.add_argument("--release", required=True)
    cl.add_argument("--execution-id", required=True, dest="execution_id",
                    help="Active localization execution id")
    cl.add_argument("--complete", default=None,
                    choices=("true", "false"),
                    help="Whether the pipeline run status is completed; not its result")
    cl.add_argument("--run-result", default=None, dest="run_result",
                    help="Exact ADO run result (succeeded/failed/canceled/etc.); missing is unknown")
    cl.add_argument("--logs-complete", action="store_true", dest="logs_complete",
                    help="The full OneLocBuild@3 task log was fetched, without paging/truncation")
    cl.add_argument("--no-change-confirmation", default=None, dest="no_change_confirmation",
                    help="Owner-reviewed explanation proving no changes from the full successful "
                        "task output; never infer this from an absent PR line")
    cl.add_argument("--logs", default=None,
                    help="OneLocBuild@3 task log text (when complete) to scan for the PR id")
    cl.add_argument("--logs-file", default=None, dest="logs_file",
                    help="Path to the OneLocBuild@3 log instead of --logs")
    cl.add_argument("--pr-status", default=None, dest="pr_status",
                    help="ADO PR status after discovery (active/completed/abandoned)")
    cl.add_argument("--now", default=None, help="Override 'now' (ISO-8601) for elapsed/timeout math")
    cl.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    cl.set_defaults(func=cmd_check_localization)

    rp = sub.add_parser(
        "record-localization-post",
        help="Record a successful localization Code reviews post")
    rp.add_argument("--release", required=True)
    rp.add_argument("--kind", required=True, choices=("initial", "deadline"))
    rp.add_argument("--pr-id", required=True, dest="pr_id")
    rp.set_defaults(func=cmd_record_localization_post)
