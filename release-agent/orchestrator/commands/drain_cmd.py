# Copyright (c) Microsoft Corporation. All rights reserved.
"""CLI commands for the unified Release Drainer (`drain`, `drain-log`, `ack-hold`)."""
from __future__ import annotations

from datetime import datetime
import json as _json

from orchestrator import cli_common as C
from orchestrator import drain as D


def _parse_now(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now requires an ISO-8601 timestamp with timezone offset")
    return parsed


def cmd_drain(args) -> int:
    rid = C.resolve_release_id(args.runs_root, args.release)
    if not rid:
        print(_json.dumps({"status": "needs_input", "error": "No active release found."}))
        return 1
    now = _parse_now(getattr(args, "now", None))
    as_of = C.parse_as_of(args)
    if getattr(args, "deliver", False) and (now is not None or as_of is not None):
        print(_json.dumps({
            "status": "failed",
            "error": "drain --deliver requires the trusted current clock; --now/--as-of are preview-only",
            "permission_to_send": False,
        }))
        return 1
    out = D.run_drain(
        args.runs_root,
        rid,
        args.config,
        trigger=getattr(args, "trigger", "scheduled") or "scheduled",
        finalize_run=getattr(args, "finalize_run", None),
        deliver=bool(getattr(args, "deliver", False)),
        now=now,
        as_of=as_of,
    )
    if getattr(args, "json", False):
        print(_json.dumps(out))
        return 0
    snap = out.get("exited") or out.get("current") or {}
    waiting = out.get("waiting") or {}
    p_done = snap.get("phase_done", snap.get("done", 0))
    p_total = snap.get("phase_total", snap.get("total", 0))
    lines = [
        f"Drain [{out['status']}] — release {rid} ({snap.get('phase_name')}: "
        f"{p_done}/{p_total} phase steps)",
        f"  Run ID: {out['run_id']}  |  Missed slots: {out.get('missed_slot_count', 0)}",
    ]
    if out.get("executed_steps"):
        lines.append(
            "  Executed: "
            + ", ".join(f"{s.get('name') or s['step']} -> {s['to_status']}" for s in out["executed_steps"])
        )
    if waiting:
        lines.append(
            f"  Waiting: blocked={waiting.get('blocked_count', 0)}, "
            f"human_input={waiting.get('human_input_count', 0)}, "
            f"in_flight={waiting.get('in_flight_count', 0)}, "
            f"unavailable={waiting.get('unavailable_count', 0)}"
        )
    if out.get("risk"):
        lines.append(
            f"  Risk: {out['risk']['level']} "
            f"(unattended={out['risk']['unattended_business_hours']}h)"
        )
    print("\n".join(lines))
    return 0


def cmd_drain_log(args) -> int:
    rid = C.resolve_release_id(args.runs_root, args.release)
    if not rid:
        print(_json.dumps({"error": "No release found."}) if args.json else "No release found.")
        return 1
    now = _parse_now(getattr(args, "now", None))
    summary = D.drain_log_summary(
        args.runs_root,
        rid,
        args.config,
        limit=args.limit,
        now=now,
    )
    if getattr(args, "json", False):
        print(_json.dumps(summary, indent=2))
        return 0
    print(
        f"Drain journal for {rid}: {summary['total_runs']} recorded run(s), "
        f"{summary['live_missed_slot_count']} live missed slot(s)"
    )
    for r in summary["runs"]:
        ex = r.get("exited") or {}
        wt = r.get("waiting") or {}
        rk = r.get("risk") or {}
        print(
            f"  • {r['started_at']}  [{r['status']}]  phase={ex.get('phase')} "
            f"({ex.get('done')}/{ex.get('total')})  "
            f"blocked={wt.get('blocked_count', 0)} human={wt.get('human_input_count', 0)}  "
            f"missed={r.get('missed_slot_count', 0)}  risk={rk.get('level', 'normal')}"
        )
    return 0


def cmd_ack_hold(args) -> int:
    """Record an owner check-in on the current hold to reset idle-stall escalation."""
    st = C.load_state(args.runs_root, args.release)
    st.touch_owner_interaction()
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log(
        "owner_hold_acknowledged", note=getattr(args, "note", None) or None
    )
    payload = {
        "status": "done",
        "release_id": st.release_id,
        "last_owner_interaction_at": st.last_owner_interaction_at,
    }
    if getattr(args, "json", False):
        print(_json.dumps(payload))
    else:
        print(f"Acknowledged hold for {st.release_id} at {st.last_owner_interaction_at}.")
    return 0


def register(sub) -> None:
    dr = sub.add_parser(
        "drain",
        help="Run the unified Release Drainer, record drain-runs.jsonl, and stage/send due CTAs",
    )
    dr.add_argument("--release", default=None, help="Release ID (default: active release)")
    dr.add_argument(
        "--trigger",
        choices=("scheduled", "interactive"),
        default="scheduled",
        help="How this drain run was initiated",
    )
    dr.add_argument(
        "--finalize-run",
        default=None,
        dest="finalize_run",
        help="Finalize an open drain run after host-assisted non-approval steps finish",
    )
    dr.add_argument(
        "--deliver",
        action="store_true",
        help="Deliver auto-notifications and due CTA emails via Agency MCP and stage Scout bot CTA",
    )
    dr.add_argument("--now", default=None, help="Simulated ISO-8601 timestamp with TZ (preview/test only)")
    dr.add_argument("--as-of", default=None, help="Simulated YYYY-MM-DD date (preview/test only)")
    dr.add_argument("--json", action="store_true")
    dr.set_defaults(func=cmd_drain)

    dl = sub.add_parser(
        "drain-log",
        help="Read structured drain-runs.jsonl history and missed drain slots",
    )
    dl.add_argument("--release", default=None, help="Release ID (default: active release)")
    dl.add_argument("--limit", type=int, default=25, help="Max recent runs to return (0 = all)")
    dl.add_argument("--now", default=None, help="Optional ISO-8601 timestamp with TZ for missed-slot check")
    dl.add_argument("--json", action="store_true")
    dl.set_defaults(func=cmd_drain_log)

    ak = sub.add_parser(
        "ack-hold",
        help="Record an interactive owner check-in on an active hold (resets idle-stall escalation)",
    )
    ak.add_argument("--release", required=True)
    ak.add_argument("--note", default="", help="Optional owner note")
    ak.add_argument("--json", action="store_true")
    ak.set_defaults(func=cmd_ack_hold)
