"""Two-hour Phase-4 Release Orchestrator poll and one-time 8-hour escalation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from orchestrator import cli_common as C, delivery as D

PHASE = "finalize"
STEP = "orchestrator_finalization"


def _parse_iso(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _elapsed_hours(started_at, now):
    started = _parse_iso(started_at)
    return max(0.0, (now - started).total_seconds() / 3600) if started else 0.0


def _escalation(st, hours):
    run_id = ((st.pipeline_runs.get("orchestrator") or {}).get("run_id")
              or "the release orchestrator")
    text = (
        f"Release {st.release_id}: Release Orchestrator run {run_id} has not reached the "
        f"'Publish GitHub Release Notes' gate after about {hours} hours. Scout will keep "
        "polling every 2 hours; inspect the run for a stuck or failed stage.")
    return {
        "email": {
            "to": [st.owner_email] if st.owner_email else [],
            "subject": f"[Release {st.release_id}] Release Orchestrator delayed over 8 hours",
            "body": text,
            "isHtml": False,
        },
        "teams": {"message": text},
    }


def cmd_poll_orchestrator_finalization(args):
    now = _parse_iso(args.now) if getattr(args, "now", None) else datetime.now(timezone.utc)
    if now is None:
        print(json.dumps({"error": f"bad --now: {args.now!r}"}))
        return 1
    st, orch = C.load_orch(
        args.runs_root, args.release, args.config, C.parse_as_of(args) or now)

    selection = orch.scheduling()
    target = next((
        item for item in selection.runnable
        if item.step.phase_id == PHASE and item.step.id == STEP
    ), None)
    if target:
        orch.step_once()
        st = orch.state
        C.save_state(st, args.runs_root, args.release)

    step = st.get_step(PHASE, STEP)
    if step.status == "done":
        final = st.pipeline_runs.get("final") or {}
        decision = {
            "decision": "resolved",
            "mrwp_run_id": final.get("mrwp_run_id"),
            "authenticator_build_id": final.get("authenticator_build_id"),
            "authenticator_version": final.get("authenticator_version"),
            "notifications": [],
        }
    elif step.status == "blocked":
        decision = {"decision": "blocked", "note": step.note, "notifications": []}
    elif step.status != "in_flight":
        decision = {"decision": "idle", "note": "finalization monitor is not active",
                    "notifications": []}
    else:
        elapsed = _elapsed_hours(step.data.get("in_flight_since"), now)
        decision = {
            "decision": "waiting",
            "elapsed_hours": round(elapsed, 2),
            "poll_in_min": step.data.get("poll_in_min", 120),
            "notifications": [],
        }
        if elapsed > 8 and not step.data.get("escalated_at"):
            payload = _escalation(st, int(elapsed))
            started_at = step.data.get("in_flight_since")
            scope = {
                "kind": "step",
                "phase": PHASE,
                "step": STEP,
                "statuses": ["in_flight"],
                "step_matches": {"in_flight_since": started_at},
                "release_matches": {"owner_email": st.owner_email},
                "not_before": (
                    _parse_iso(started_at) + timedelta(hours=8)
                ).isoformat(),
            }
            completion = {"kind": "step_data", "stamp": ["escalated_at"]}
            logical = f"orchestrator-finalization:{started_at}:8h"
            descriptors = []
            if st.owner_email:
                descriptors = [
                    D.descriptor(
                        st, logical, scope, "workiq_send_email", payload["email"], completion
                    ),
                    D.descriptor(
                        st, logical, scope, "m_send_teams_message", payload["teams"], completion
                    ),
                ]
            else:
                decision["escalation_blocked"] = (
                    "Release owner email is unresolved; set the owner before escalation."
                )
            decision["notifications"] = [
                D.offer(orch, item) for item in descriptors if D.available(orch, item)
            ]
            if decision["notifications"]:
                decision["decision"] = "escalate"
                decision["escalation"] = payload
                decision["permission_to_send"] = False
                C.save_state(st, args.runs_root, args.release)

    print(json.dumps(decision))
    return 0


def register(sub):
    parser = sub.add_parser(
        "poll-orchestrator-finalization",
        help="Poll Phase-4 final orchestration and offer the one-time 8-hour escalation")
    parser.add_argument("--release", required=True)
    parser.add_argument("--now", default=None, help="Override now for elapsed-time tests")
    parser.add_argument("--as-of", default=None, help="Simulated date; default today")
    parser.set_defaults(func=cmd_poll_orchestrator_finalization)
