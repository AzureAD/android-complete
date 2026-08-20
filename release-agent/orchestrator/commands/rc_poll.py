"""`poll-rc` — one poll of an in-flight Phase-2 RC verification (the 30-min RC poller).

After a re-triggered RC (see `rc-retriggered`), the Build & RC Verification phase holds
on an IN-FLIGHT MRWP run (status-aware verify — see steps/build_verify/_common). This
command is the poller seam the `build-verify-rc-poller` automation calls every 30 min:

  1. advance the drain (`run_until_gate`) so the in-flight verify step re-checks the run's
     live status — still running → stays in-flight; completed → the normal stage rule +
     UI gate apply and the phase moves on.
  2. emit a deterministic decision the skill acts on:
       waiting  — still running; nothing to send.
       nudge    — running past the 6h courtesy threshold; send the owner a heads-up (once).
       resolved — the new RC completed and PASSED the gate; Phase 2 advanced (deregister
                  the poller).
       blocked  — the new RC completed but re-blocked the gate (still failing).
       idle     — nothing in-flight (not in Phase 2, or nothing was re-triggered).

Decisions are pure functions of state; the 6h nudge stamps `nudged_at` on the step so it
is sent at most once. `--now` overrides the clock for the elapsed/nudge math (tests)."""
from __future__ import annotations
import json as _json
from datetime import datetime, timezone

from orchestrator import cli_common as C

# The poll cadence + courtesy-nudge threshold. A re-triggered RC that runs longer than
# NUDGE_AFTER_HOURS gets ONE heads-up to the owner (it is not a failure — Scout keeps
# polling), per the agreed Phase-2 blocked-state handling.
POLL_INTERVAL_MIN = 30
NUDGE_AFTER_HOURS = 6

# The verify steps whose run can be in-flight (checker/orchestrator resolve instantly).
_RC_VERIFY_STEPS = ("mrwp_ecs", "mrwp_local")


def _parse_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _elapsed_hours(since_iso, now):
    since = _parse_iso(since_iso)
    if not since:
        return 0.0
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return max(0.0, (now - since).total_seconds() / 3600.0)


def _nudge_payload(st, sid, hrs: int) -> dict:
    """A SHORT courtesy heads-up (not the full RC report) — the re-triggered RC is taking
    a while but Scout is still polling; no action needed yet."""
    label = {"mrwp_ecs": "MRWP (ECS)", "mrwp_local": "MRWP (Local)"}.get(sid, sid)
    subject = f"[Release {st.release_id}] Re-triggered RC still running after ~{hrs}h"
    body = (
        f"Heads-up: the re-triggered {label} run for release {st.release_id} has been "
        f"running for about {hrs} hours. This is NOT a failure — Scout is still polling "
        f"every {POLL_INTERVAL_MIN} minutes and will re-apply the RC gate the moment the "
        f"run completes, with no action needed from you. If a {hrs}h RC run is unexpected, "
        f"open the run in ADO to check for a stuck stage.")
    teams = (f"⏳ Release {st.release_id}: the re-triggered {label} RC has been running "
             f"~{hrs}h. Scout is still polling every {POLL_INTERVAL_MIN}m and re-applies "
             f"the gate on completion — no action needed yet.")
    return {
        "email": {"to": [st.owner_email] if st.owner_email else [],
                  "subject": subject, "body": body},
        "teams": {"text": teams},
    }


def cmd_poll_rc(args):
    now = _parse_iso(args.now) if getattr(args, "now", None) else datetime.now(timezone.utc)
    if now is None:
        print(_json.dumps({"error": f"bad --now: {args.now!r}"}))
        return 1

    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    # Advance: the in-flight verify step re-checks the run's LIVE status. Still running →
    # stays in-flight; completed → the stage rule + UI gate run and the phase moves on.
    orch.run_until_gate()
    st = orch.state
    C.save_state(st, args.runs_root, args.release)

    inflight = None
    for sid in _RC_VERIFY_STEPS:
        s = st.get_step("build_verify", sid)
        if s.status == "in_flight":
            inflight = (sid, s)
            break

    if inflight:
        sid, s = inflight
        elapsed = _elapsed_hours(s.data.get("in_flight_since"), now)
        decision = {"decision": "waiting", "step": sid,
                    "elapsed_hours": round(elapsed, 2), "poll_in_min": POLL_INTERVAL_MIN}
        if elapsed >= NUDGE_AFTER_HOURS and not s.data.get("nudged_at"):
            s.data["nudged_at"] = now.isoformat()
            st.set_step("build_verify", sid, s)
            C.save_state(st, args.runs_root, args.release)
            decision["decision"] = "nudge"
            decision["nudge"] = _nudge_payload(st, sid, int(elapsed))
            C.emit(args.runs_root, args.release,
                   f"[rc-poller] {sid} in-flight ~{int(elapsed)}h — 6h courtesy nudge sent "
                   f"to the owner.", kind="build_verify")
    else:
        rc = st.get_step("build_verify", "rc_report")
        if rc.status == "done":
            decision = {"decision": "resolved", "status": "passed", "note": rc.note}
        elif rc.status == "blocked":
            decision = {"decision": "blocked", "note": rc.note}
        else:
            decision = {"decision": "idle",
                        "note": "no in-flight RC in Build & RC Verification"}

    print(_json.dumps(decision))
    return 0


def register(sub):
    p = sub.add_parser("poll-rc",
                       help="One poll of an in-flight Phase-2 RC: advance + emit a "
                            "waiting/nudge/resolved/blocked/idle decision (30-min poller)")
    p.add_argument("--release", required=True)
    p.add_argument("--now", default=None,
                   help="Override 'now' (ISO-8601) for the elapsed / 6h-nudge math")
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.set_defaults(func=cmd_poll_rc)
