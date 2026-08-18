"""Notification + owner commands: notify (daily phase digest) and set-owner."""
from __future__ import annotations
import json as _json
import os

from orchestrator.state import ReleaseState
from orchestrator.engine import Orchestrator
from orchestrator import render, schedule
from orchestrator import notifications as notif
from orchestrator import cli_common as C
from tools import checks


def _empty_payload(rid, config_path=None):
    """The 'nothing to send' payload — still reports which channels are configured
    so callers see a stable shape."""
    ch = notif.channels(notif.load_config(config_path)) if config_path else {"email": True, "teams": False}
    return {"message": "", "html": "", "subject": "", "owner_email": None,
            "owner_name": None, "release": rid, "channels": ch, "teams": None}


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
    De-duped to one per calendar day; --force bypasses; --json prints the mailer
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
    `message` is "" unless a digest is due AND not already sent today (or --force)."""
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
        C.log_actions(C.elog(args.runs_root, rid), actions)
    else:
        st = ReleaseState.load(sp)
        orch = Orchestrator(args.config, st, as_of=as_of)
    report = orch.status_report()
    msg = render.notification(report)
    html = render.notification_html(report)
    subject = render.notification_subject(report)
    today = (as_of or schedule.today()).isoformat()
    fresh = bool(msg) and (getattr(args, "force", False) or st.last_notified_date != today)
    if fresh:
        st.last_notified_date = today
        st.save(sp)
        try:
            C.elog(args.runs_root, rid).log("notified", text=msg, owner=st.owner_email)
        except Exception:
            pass
    # Fan-out channels (config/notifications.yaml). Email is the existing path; when
    # Teams is on and a digest is actually due, attach a delivery descriptor (Scout
    # bot by default, or an explicit chat).
    ncfg = notif.load_config(getattr(args, "config", None))
    ch = notif.channels(ncfg)
    teams = notif.teams_delivery(ncfg, html, msg) if (fresh and msg and ch.get("teams")) else None
    return {"message": msg if fresh else "", "html": html if fresh else "",
            "subject": subject, "owner_email": st.owner_email,
            "owner_name": st.owner_name, "release": rid,
            "channels": ch, "teams": teams}


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
    elif payload["message"]:
        print(payload["message"])
    return 0


def register(sub):
    so = sub.add_parser("set-owner", help="Set/change the release owner (who reminders are emailed to)")
    so.add_argument("--release", required=True)
    so.add_argument("--owner-email", default=None, help="Owner email (default: signed-in az user)")
    so.add_argument("--owner-name", default=None, help="Owner display name (optional)")
    so.set_defaults(func=cmd_set_owner)

    nt = sub.add_parser("notify", help="Emit a push line if something needs the user now (else nothing)")
    nt.add_argument("--release", default=None, help="Target release; if omitted, discover the active one")
    nt.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD) — debug override; default today")
    nt.add_argument("--force", action="store_true", help="Bypass de-dup (always emit if actionable)")
    nt.add_argument("--json", action="store_true", help="Emit {message,subject,owner_email,owner_name,release} for the mailer")
    nt.set_defaults(func=cmd_notify)

    tk = sub.add_parser("tick", help="Automation heartbeat: ADVANCE the active release, then emit the digest payload")
    tk.add_argument("--release", default=None, help="Target release; if omitted, discover the active one")
    tk.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD) — debug override; default today")
    tk.add_argument("--force", action="store_true", help="Bypass the once-per-day digest de-dup")
    tk.add_argument("--json", action="store_true", help="Emit {message,subject,owner_email,owner_name,release} for the mailer")
    tk.set_defaults(func=cmd_tick)
