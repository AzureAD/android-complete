"""`status-email` — compose the partner-facing DAILY release status email; `record-status-email`
— stamp it after the skill sends (idempotency + last-sent tracking).

`status-email --json` returns either {skip:true, reason} (out of the Phase-2..Phase-4 window, a
weekend/US holiday, or already sent today) or {skip:false, to, subject, html, followup_command}.
The skill sends the payload via workiq_send_email (redirecting to the owner via --send-to for a
test run), then runs `record-status-email` to stamp the day. The composer + template live in
orchestrator/status_email.py (pure); this command adds the live broker change-list + the
business-day/idempotency gates.
"""
from __future__ import annotations
import json as _json
from datetime import date

import yaml

from orchestrator import cli_common as C
from orchestrator import status_email as SE
from orchestrator import notifications as notif
from tools import bugbash as BB
from tools import prs


def _phase_order(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return [p["id"] for p in (doc.get("phases") or [])]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return []


def _recipients(config_path):
    cfg = notif.load_config(config_path) or {}
    return list((cfg.get("status_email") or {}).get("recipients") or [])


def _broker_changes(state):
    """Best-effort broker change list (PRs merged into the broker release branch). Never raises."""
    try:
        from steps.finalize import integ_prs as IP
        gh = (IP.CONFIG.get("broker") or {}).get("gh_repo")
        bver = (getattr(state, "versions", None) or {}).get("broker")
        if not (gh and bver):
            return []
        ok, ch, _d = prs.broker_change_list(gh, bver)
        return ch if ok else []
    except Exception:  # noqa: BLE001
        return []


def cmd_status_email(args):
    st, _orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    today = C.parse_as_of(args) or date.today()
    force = bool(getattr(args, "force", False))

    recipients = _recipients(args.config)
    if getattr(args, "send_to", None):                     # test redirect
        recipients = [x.strip() for x in str(args.send_to).split(",") if x.strip()]

    res = SE.compose(st, _phase_order(args.config), recipients, changes=_broker_changes(st))

    # 1) window (Phase 2 <= current < Phase 5)
    if res["skip"] and not force:
        print(_json.dumps({"skip": True, "reason": res["reason"], "release": args.release}))
        return 0
    # 2) business day (weekday + not a US holiday)
    if not force and not BB.is_business_day(today):
        print(_json.dumps({"skip": True, "reason": "weekend/holiday", "release": args.release}))
        return 0
    # 3) idempotency — already sent today
    if not force and getattr(st, "last_status_email_date", None) == today.isoformat():
        print(_json.dumps({"skip": True, "reason": "already sent today", "release": args.release}))
        return 0

    print(_json.dumps({
        "skip": False, "release": args.release, "to": res["to"],
        "subject": res["subject"], "html": res["html"],
        "redirected": bool(getattr(args, "send_to", None)),
        "followup_command": "record-status-email",
    }))
    return 0


def cmd_record_status_email(args):
    """Stamp the day a status email was sent (idempotency) and, with --final, close the channel
    (nothing more to send). The skill runs this AFTER a successful send."""
    st = C.load_state(args.runs_root, args.release)
    today = C.parse_as_of(args) or date.today()
    st.last_status_email_date = today.isoformat()
    C.save_state(st, args.runs_root, args.release)
    print(_json.dumps({"recorded": today.isoformat(), "final": bool(getattr(args, "final", False)),
                       "release": args.release}))
    return 0


def register(sub):
    se = sub.add_parser("status-email",
                        help="Compose the partner-facing daily release status email (JSON payload; "
                             "skips outside Phase 2-4 / weekends / holidays / already-sent-today)")
    se.add_argument("--release", required=True)
    se.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    se.add_argument("--force", action="store_true",
                    help="Compose regardless of window/business-day/idempotency (testing)")
    se.add_argument("--send-to", default=None,
                    help="Redirect recipients to these address(es) (comma-separated) for a test run")
    se.set_defaults(func=cmd_status_email)

    rs = sub.add_parser("record-status-email",
                        help="Stamp that the daily status email was sent (idempotency); --final closes it")
    rs.add_argument("--release", required=True)
    rs.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rs.add_argument("--final", action="store_true", help="This was the closing (end of Phase 4) email")
    rs.set_defaults(func=cmd_record_status_email)
