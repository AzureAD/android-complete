"""Localization poll commands — the recorder/decider seam for the Phase-1
`localization` step (P1-2).

The step's logic lives in `steps/ccd/localization.py` (trigger + pure `decide`).
These commands are the thin CLI seam the skill/poller calls:

  * record-localization-run — after the pipeline is triggered, store the queued
    build id + start time on the step (leaves it IN-FLIGHT, not done).
  * check-localization — one poll: given the run or PR state, apply `decide()` and
    either wait, request a notification, or finish. Prints the decision JSON so the
    poller can claim and acknowledge each notification through the shared delivery protocol.
  * record-localization-post — legacy readback of an already acknowledged Code reviews
    post; a PR identifier alone never proves delivery.
"""
from __future__ import annotations
import json as _json
from datetime import datetime, timedelta, timezone

from orchestrator import cli_common as C, delivery as D
from orchestrator import mocks as mocks_mod
from steps.lib.context import SELF_CHAT_ID
from steps.ccd import localization as L


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def cmd_record_localization_run(args):
    """Store the triggered build id + start time on the localization step. Leaves the
    step in-flight so the poller can drive it to completion."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    step = st.get_step("ccd", "localization")
    if step.status in ("done", "skipped", "blocked"):
        print(_json.dumps({"recorded": False, "reason": "localization is terminal"}))
        return 0
    reason = D.scope_reason(orch, {"kind": "step", "phase": "ccd", "step": "localization"})
    if reason:
        print(_json.dumps({"error": reason}))
        return 1
    if step.data.get("build_id"):
        if str(step.data["build_id"]) != str(args.build_id):
            print(_json.dumps({"error": "A different localization build is already recorded; owner review required"}))
            return 1
        print(_json.dumps({"recorded": False, "reason": "build already recorded"}))
        return 0
    step.data["build_id"] = args.build_id
    step.data["started_at"] = args.started_at or _now_iso()
    step.status = "in_flight"
    step.note = "localization pipeline running — Scout is polling hourly"
    if args.run_url:
        step.data["run_url"] = args.run_url
    st.set_step("ccd", "localization", step)
    C.save_state(st, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[localization] pipeline triggered — build {args.build_id}; polling every "
           f"{L.CONFIG['poll_interval_min']}m (timeout {L.CONFIG['timeout_hours']}h).",
           kind="localization")
    return 0


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

    # Guard: nothing to poll if it wasn't triggered, or it's already terminal.
    if not step.data.get("started_at"):
        print(_json.dumps({"decision": "not_started",
                           "note": "localization has not been triggered yet"}))
        return 0
    if step.status in ("done", "skipped", "blocked"):
        print(_json.dumps({"decision": "already_final", "status": step.status}))
        return 0

    if getattr(args, "complete", None) is None and getattr(args, "pr_status", None) is None:
        print(_json.dumps(L.poll_target(st)))
        return 0

    logs = args.logs
    if logs is None and args.logs_file:
        try:
            with open(args.logs_file, "r", encoding="utf-8") as fh:
                logs = fh.read()
        except OSError as e:
            print(_json.dumps({"error": f"could not read --logs-file: {e}"}))
            return 1

    try:
        decision = L.decide(
            st, is_complete=_truthy(args.complete), logs=logs, now=now,
            pr_status=getattr(args, "pr_status", None))
    except ValueError as e:
        print(_json.dumps({"error": str(e)}))
        return 1
    d = decision["decision"]
    # Completion evidence is monotonic: a delayed timeout receipt cannot undo recovery.
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

    if d in ("wait", "wait_for_merge"):
        # Not terminal — keep in-flight, just record progress on the step.
        step.data["last_checked"] = now.isoformat() if now else _now_iso()
        step.note = decision["note"]
        st.set_step("ccd", "localization", step)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[localization] {decision['note']}", kind="localization")
    elif d == "timeout":
        # Keep the worker alive until its required escalation is acknowledged.
        step.note = "localization timeout; required owner notification awaiting delivery"
        st.set_step("ccd", "localization", step)
    elif d == "announce_pr":
        step.data["pr_id"] = decision["pr_id"]
        step.data["pr_url"] = decision["pr_url"]
        step.data.setdefault("pr_discovered_at", now.isoformat() if now else _now_iso())
        step.links = decision.get("links", [])
        step.note = decision["note"]
        st.set_step("ccd", "localization", step)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[localization] {decision['note']}",
               kind="localization")
    elif d == "warn_unmerged":
        step.data["last_checked"] = now.isoformat() if now else _now_iso()
        step.note = decision["note"]
        st.set_step("ccd", "localization", step)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] localization: {decision['note']}",
               kind="localization")
    elif d == "omit_unmerged":
        step.status = "skipped"
        step.completed_at = now.isoformat() if now else _now_iso()
        step.by = "scout"
        step.note = decision["note"]
        step.links = decision.get("links", step.links)
        st.set_step("ccd", "localization", step)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[omitted] localization: {decision['note']}",
               kind="localization")
    elif d in ("merged", "complete_none"):
        orch.record_scout_step("ccd", "localization", "pass", decision["note"])
        done = orch.state.get_step("ccd", "localization")
        done.by = "scout"
        done.data = step.data                      # preserve build id / start time
        if decision.get("links"):
            done.links = decision["links"]         # the PR link
        orch.state.set_step("ccd", "localization", done)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[ok] localization: {decision['note']}",
               kind="localization")
    else:
        print(_json.dumps({"error": f"unsupported localization decision: {d}"}))
        return 1

    if d in ("timeout", "announce_pr", "warn_unmerged"):
        scope["step_matches"] = {"build_id": step.data.get("build_id")}
        if d == "timeout":
            scope["step_matches"].update(
                started_at=step.data["started_at"], pr_id=None, pipeline_complete=False)
            started = datetime.fromisoformat(step.data["started_at"].replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            scope["not_before"] = (started + timedelta(hours=L.CONFIG["timeout_hours"])).isoformat()
        else:
            scope["step_matches"]["pr_id"] = step.data.get("pr_id")
            deadline_cfg = {**L.CONFIG, "merge_deadline_local": L.CONFIG["omission_deadline_local"]}
            scope["expires_at"] = L.merge_deadline(st, deadline_cfg).isoformat()
        scope["release_matches"] = {"owner_email": st.owner_email, "ccd": st.ccd}
        completion = ({"kind": "step_result", "status": "attention", "note": decision["note"],
                       "stamp": ["timeout_notified_at"]} if d == "timeout" else
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
    rr = sub.add_parser("record-localization-run",
                        help="Record the triggered localization build id + start time (leaves it in-flight)")
    rr.add_argument("--release", required=True)
    rr.add_argument("--as-of", default=None)
    rr.add_argument("--build-id", required=True, dest="build_id")
    rr.add_argument("--run-url", default=None, dest="run_url")
    rr.add_argument("--started-at", default=None, dest="started_at",
                    help="ISO-8601 start time; defaults to now")
    rr.set_defaults(func=cmd_record_localization_run)

    cl = sub.add_parser("check-localization",
                        help="One localization poll: pipeline status before PR discovery, PR status afterward")
    cl.add_argument("--release", required=True)
    cl.add_argument("--complete", default=None,
                    help="Whether the pipeline run has finished (true/false/succeeded)")
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
