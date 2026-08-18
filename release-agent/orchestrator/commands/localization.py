"""Localization poll commands — the recorder/decider seam for the Phase-1
`localization` step (P1-2).

The step's logic lives in `steps/ccd/localization.py` (trigger + pure `decide`).
These commands are the thin CLI seam the skill/poller calls:

  * record-localization-run — after the pipeline is triggered, store the queued
    build id + start time on the step (leaves it IN-FLIGHT, not done).
  * check-localization — one poll: given the run's completion state (and the
    OneLocBuild@3 log when complete), apply `decide()` and either wait, escalate
    (email the engineer) + hold, or finish (post the PR to Code reviews + mark done,
    or mark done with no strings). Prints the decision JSON so the poller can perform
    the email/chat side-effect described in it.
"""
from __future__ import annotations
import json as _json
from datetime import datetime, timezone

from orchestrator import cli_common as C
from steps.ccd import localization as L


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def cmd_record_localization_run(args):
    """Store the triggered build id + start time on the localization step. Leaves the
    step in-flight (pending) so the poller can drive it to completion."""
    st = C.load_state(args.runs_root, args.release)
    step = st.get_step("ccd", "localization")
    step.data["build_id"] = args.build_id
    step.data["started_at"] = args.started_at or _now_iso()
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

    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    step = st.get_step("ccd", "localization")

    # Guard: nothing to poll if it wasn't triggered, or it's already terminal.
    if not step.data.get("started_at"):
        print(_json.dumps({"decision": "not_started",
                           "note": "localization has not been triggered yet"}))
        return 0
    if step.status in ("done", "skipped", "blocked"):
        print(_json.dumps({"decision": "already_final", "status": step.status}))
        return 0

    logs = args.logs
    if logs is None and args.logs_file:
        try:
            with open(args.logs_file, "r", encoding="utf-8") as fh:
                logs = fh.read()
        except OSError as e:
            print(_json.dumps({"error": f"could not read --logs-file: {e}"}))
            return 1

    decision = L.decide(st, is_complete=_truthy(args.complete), logs=logs, now=now)
    d = decision["decision"]

    if d == "wait":
        # Not terminal — keep in-flight, just record progress on the step.
        step.data["last_checked"] = now.isoformat() if now else _now_iso()
        step.note = decision["note"]
        st.set_step("ccd", "localization", step)
        C.save_state(st, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[localization] {decision['note']}", kind="localization")
    elif d == "timeout":
        # Hold the step for the engineer; the poller sends decision['email'].
        orch.record_scout_step("ccd", "localization", "attention", decision["note"])
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] localization: {decision['note']}",
               kind="localization")
    else:  # complete_pr | complete_none → done
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

    print(_json.dumps(decision))
    return 0


def register(sub):
    rr = sub.add_parser("record-localization-run",
                        help="Record the triggered localization build id + start time (leaves it in-flight)")
    rr.add_argument("--release", required=True)
    rr.add_argument("--build-id", required=True, dest="build_id")
    rr.add_argument("--run-url", default=None, dest="run_url")
    rr.add_argument("--started-at", default=None, dest="started_at",
                    help="ISO-8601 start time; defaults to now")
    rr.set_defaults(func=cmd_record_localization_run)

    cl = sub.add_parser("check-localization",
                        help="One poll of the localization run: wait / escalate (email) / finish (post PR)")
    cl.add_argument("--release", required=True)
    cl.add_argument("--complete", default="false",
                    help="Whether the pipeline run has finished (true/false/succeeded)")
    cl.add_argument("--logs", default=None,
                    help="OneLocBuild@3 task log text (when complete) to scan for the PR id")
    cl.add_argument("--logs-file", default=None, dest="logs_file",
                    help="Path to the OneLocBuild@3 log instead of --logs")
    cl.add_argument("--now", default=None, help="Override 'now' (ISO-8601) for elapsed/timeout math")
    cl.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    cl.set_defaults(func=cmd_check_localization)
