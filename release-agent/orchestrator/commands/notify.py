"""Notification + owner commands: notify (daily phase digest) and set-owner."""
from __future__ import annotations
import json as _json
import os

from orchestrator.state import ReleaseState
from orchestrator.engine import Orchestrator
from orchestrator import render, schedule, delivery as D
from orchestrator import notifications as notif
from orchestrator import cli_common as C
from tools import checks


def _empty_payload(rid, config_path=None):
    """The 'nothing to send' payload — still reports which channels are configured
    so callers see a stable shape."""
    ch = notif.channels(notif.load_config(config_path)) if config_path else {"email": True, "teams": False}
    return {"message": "", "html": "", "subject": "", "owner_email": None,
            "owner_name": None, "release": rid, "channels": ch, "teams": None,
            "core_alert": None, "notifications": [], "permission_to_send": False}


def cmd_set_owner(args):
    """Set/change the release owner (who reminders are emailed to)."""
    st = C.load_state(args.runs_root, args.release)
    st.owner_email = (getattr(args, "owner_email", None) or checks.current_az_user())
    if getattr(args, "owner_name", None):
        st.owner_name = args.owner_name
    C.save_state(st, args.runs_root, args.release)
    C.elog(args.runs_root, args.release).log("owner_set", owner=st.owner_email)
    if not st.owner_email:
        print("Couldn't resolve an owner (no --owner-email and az user unavailable).")
        return 1
    who = f"{st.owner_name + ' ' if st.owner_name else ''}{st.owner_email}"
    print(f"Release {args.release} owner set to {who}.")
    return 0


def cmd_notify(args):
    """Emit the daily phase digest IF the active phase is open with outstanding
    work, else nothing. Read-only (does NOT advance the flow — use `tick` for that).
    De-duped per channel and owner-local day; --force cannot resend; --json prints the
    payload {message,subject,owner_email,owner_name,release}."""
    rid = C.resolve_release_id(args.runs_root, args.release)
    want_json = getattr(args, "json", False)
    if not rid:
        if want_json:
            print(_json.dumps(_empty_payload(None, getattr(args, "config", None))))
        return 0
    payload = _notify_payload(args, rid, advance=False)
    if want_json:
        print(_json.dumps(payload))
    elif payload["message"]:
        print(payload["message"])
    return 0


def _notify_payload(args, rid, advance):
    """Shared by `notify` and `tick`. Optionally ADVANCE the flow first
    (run_until_gate), then read the state machine and build the once-per-day
    digest payload. Returns {message, subject, owner_email, owner_name, release}.
    `message` is "" unless an eligible digest channel is not yet acknowledged today."""
    sp = C.state_path(args.runs_root, rid)
    if not os.path.exists(sp):
        return _empty_payload(rid, getattr(args, "config", None))
    as_of = C.parse_as_of(args)
    if advance:
        # Auto-advance: run every agent step that can run, holding at the first
        # gate / action-needed. Idempotent — a no-op once holding or not due.
        st, orch = C.load_orch(args.runs_root, rid, args.config, as_of)
        actions = orch.run_until_gate()
        C.save_state(st, args.runs_root, rid)
        C.log_actions(C.elog(args.runs_root, rid), actions, state=st)
    else:
        st = ReleaseState.load(sp)
        orch = Orchestrator(args.config, st, as_of=as_of)
    report = orch.status_report()
    if D.scope_reason(orch, {"kind": "release"}):
        return _empty_payload(rid, args.config)
    # Both preview paths are read-only with respect to delivery checkpoints.
    alert_model = notif.preflight_escalation(
        report, orch.now_local, getattr(st, "escalation_checkpoints", {}))
    core_alert = None
    if alert_model:
        core_alert = notif.core_alert_delivery(
            report, alert_model, render.preflight_core_alert(report, alert_model))
    msg = render.notification(report)
    html = render.notification_html(report)
    md = render.notification_markdown(report)
    subject = render.notification_subject(report)
    today = orch.now_local.date().isoformat()
    fresh = bool(msg) and st.last_notified_date != today
    # Fan-out channels (config/notifications.yaml). Email is the existing path; when
    # Teams is on and a digest is actually due, attach a delivery descriptor (Scout
    # bot by default, or an explicit chat).
    ncfg = notif.load_config(getattr(args, "config", None))
    ch = notif.channels(ncfg)
    teams = notif.teams_delivery(ncfg, html, msg, md) if (fresh and msg and ch.get("teams")) else None
    items = []
    scope = {"kind": "phase", "phase": orch.current_phase_id(), "date": today,
             "release_matches": {"owner_email": st.owner_email}}
    if fresh and ch.get("email"):
        items.append(D.descriptor(st, f"digest:{today}", scope, "workiq_send_email",
                                  {"to": [st.owner_email] if st.owner_email else [],
                                   "subject": subject, "body": html or msg, "isHtml": bool(html)}))
    if teams:
        tool = "m_send_teams_message" if teams["via"] == "scout_bot" else "workiq_send_chat_message"
        payload = ({"message": teams["text"]} if teams["via"] == "scout_bot" else
                   {k: v for k, v in teams.items() if k != "via"})
        items.append(D.descriptor(st, f"digest:{today}", scope, tool, payload))
    if core_alert:
        items.append(D.descriptor(
            st, f"core-alert:{alert_model['key']}",
            {"kind": "phase", "phase": "preflight", "date": today,
             "release_matches": {"ccd": st.ccd, "owner_email": st.owner_email}},
            "workiq_send_chat_message",
            {k: core_alert[k] for k in ("chatId", "content", "contentType", "mentions")},
            {"checkpoint": alert_model["key"]}))
    items = [i for i in items if D.available(orch, i)]
    digest_channels = {i["channel"] for i in items if i["id"].startswith("digest:")}
    fresh = bool(digest_channels)
    if "teams" not in digest_channels:
        teams = None
    if not any(i["id"].startswith("core-alert:") for i in items):
        core_alert = None
    return {"message": msg if fresh else "", "html": html if fresh else "",
            "subject": subject, "owner_email": st.owner_email,
            "owner_name": st.owner_name, "release": rid,
            "channels": {k: v and k in digest_channels for k, v in ch.items()},
            "teams": teams, "core_alert": core_alert, "notifications": items,
            "permission_to_send": False}


def cmd_tick(args):
    """One automation heartbeat: discover the active release, ADVANCE it (run the
    agent steps that can run, holding at gates/actions), then emit the daily digest
    payload for the mailer. Safe to run often — advancing is idempotent and the
    digest is de-duped to once per calendar day. This is what the hourly Scout
    automation runs so an open phase makes progress even if the 9am tick was missed
    (machine off) — the next tick after the machine is on picks it up."""
    rid = C.resolve_release_id(args.runs_root, args.release)
    if not rid:
        print(_json.dumps(_empty_payload(None, getattr(args, "config", None))))
        return 0
    payload = _notify_payload(args, rid, advance=True)
    if getattr(args, "json", False):
        print(_json.dumps(payload))
    else:
        if payload["message"]:
            print(payload["message"])
        if payload["core_alert"]:
            print(f"Core Team deadline alert due: {payload['core_alert']['checkpoint']}")
    return 0


def cmd_record_core_alert(args):
    """Acknowledge a Core Team alert only after WorkIQ confirms the send."""
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    valid = {f"preflight:{st.ccd}:pre_ccd", f"preflight:{st.ccd}:ccd"} if st.ccd else set()
    if args.checkpoint not in valid:
        print("Checkpoint does not match this release's CCD.")
        return 1
    if args.checkpoint in st.escalation_checkpoints:
        print("Core Team alert already recorded; no changes.")
        return 0
    print("Use notification claim/result; a checkpoint alone cannot prove delivery.")
    return 1


def register(sub):
    so = sub.add_parser("set-owner", help="Set/change the release owner (who reminders are emailed to)")
    so.add_argument("--release", required=True)
    so.add_argument("--owner-email", default=None, help="Owner email (default: signed-in az user)")
    so.add_argument("--owner-name", default=None, help="Owner display name (optional)")
    so.set_defaults(func=cmd_set_owner)

    nt = sub.add_parser("notify", help="Emit a push line if something needs the user now (else nothing)")
    nt.add_argument("--release", default=None, help="Target release; if omitted, discover the active one")
    nt.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD) — debug override; default today")
    nt.add_argument("--force", action="store_true", help="Never bypasses acknowledgement or lifecycle guards")
    nt.add_argument("--json", action="store_true", help="Emit {message,subject,owner_email,owner_name,release} for the mailer")
    nt.set_defaults(func=cmd_notify)

    tk = sub.add_parser("tick", help="Automation heartbeat: ADVANCE the active release, then emit the digest payload")
    tk.add_argument("--release", default=None, help="Target release; if omitted, discover the active one")
    tk.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD) — debug override; default today")
    tk.add_argument("--force", action="store_true", help="Never bypasses acknowledgement or lifecycle guards")
    tk.add_argument("--json", action="store_true", help="Emit {message,subject,owner_email,owner_name,release} for the mailer")
    tk.set_defaults(func=cmd_tick)

    ra = sub.add_parser("record-core-alert",
                        help="Record a Core Team deadline alert after successful delivery")
    ra.add_argument("--release", required=True)
    ra.add_argument("--checkpoint", required=True)
    ra.set_defaults(func=cmd_record_core_alert)
