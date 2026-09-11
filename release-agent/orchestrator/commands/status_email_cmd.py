"""Read-only partner digest preparation. Delivery uses notification claim/result."""
from __future__ import annotations
import json as _json

import yaml

from orchestrator import cli_common as C
from orchestrator import status_email as SE, delivery as D
from orchestrator import notifications as notif
from tools import bugbash as BB
from tools import prs


def _phase_order(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return [p["id"] for p in (doc.get("phases") or [])]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid phase configuration: {exc}") from exc


def _recipients(config_path):
    cfg = notif.load_config(config_path) or {}
    recipients = (cfg.get("status_email") or {}).get("recipients")
    if not isinstance(recipients, list) or not recipients:
        raise ValueError("Configure status_email.recipients as a non-empty list")
    return recipients


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


def prepare_status_email(args, st, _orch):
    today = _orch.now_local.date()
    force = bool(getattr(args, "force", False))
    cfg = notif.load_config(args.config)
    phases = (cfg.get("status_email") or {}).get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("Configure status_email.phases explicitly")
    scope = {"kind": "window", "phases": phases,
             "until_steps": (cfg.get("status_email") or {}).get("until_steps", []),
             "date": today.isoformat(), "release_matches": {"owner_email": st.owner_email}}
    reason = D.scope_reason(_orch, scope)
    if reason:
        return {"skip": True, "reason": reason, "release": args.release}

    recipients = _recipients(args.config)
    if getattr(args, "send_to", None):                     # test redirect
        recipients = [x.strip() for x in str(args.send_to).split(",") if x.strip()]

    res = SE.compose(st, _phase_order(args.config), recipients, changes=_broker_changes(st))

    # 1) window (Phase 2 <= current < Phase 5)
    if res["skip"]:
        return {"skip": True, "reason": res["reason"], "release": args.release}
    # 2) business day (weekday + not a US holiday)
    if not force and not BB.is_business_day(today):
        return {"skip": True, "reason": "weekend/holiday", "release": args.release}
    # 3) idempotency — already sent today
    if getattr(st, "last_status_email_date", None) == today.isoformat():
        return {"skip": True, "reason": "already recorded today", "release": args.release}

    item = D.descriptor(st, f"status-email:{today.isoformat()}", scope, "workiq_send_email",
                        {"to": res["to"], "subject": res["subject"],
                         "body": res["html"], "isHtml": True},
                        {"release_field": "last_status_email_date", "date": today.isoformat()})
    if not D.available(_orch, item):
        return {"skip": True, "reason": "already claimed or acknowledged", "release": args.release}
    return {
        "skip": False, "release": args.release, "to": res["to"],
        "subject": res["subject"], "html": res["html"],
        "redirected": bool(getattr(args, "send_to", None)),
        "notifications": [item], "permission_to_send": False,
    }


def cmd_status_email(args):
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
        print(_json.dumps(prepare_status_email(args, st, orch)))
    except ValueError as exc:
        print(_json.dumps({"error": str(exc)}))
        return 1
    return 0


def cmd_record_status_email(args):
    """Reject legacy date-only acknowledgements without channel delivery evidence."""
    print(_json.dumps({"error": "Use notification claim/result; date-only acknowledgement is unsafe"}))
    return 1


def register(sub):
    se = sub.add_parser("status-email",
                        help="Compose the partner-facing daily release status email (JSON payload; "
                             "skips outside Phase 2-4 / weekends / holidays / already-sent-today)")
    se.add_argument("--release", required=True)
    se.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    se.add_argument("--force", action="store_true",
                    help="Bypass business-day cadence only, never lifecycle or acknowledgement")
    se.add_argument("--send-to", default=None,
                    help="Redirect recipients to these address(es) (comma-separated) for a test run")
    se.set_defaults(func=cmd_status_email)

    rs = sub.add_parser("record-status-email",
                        help="Retired acknowledgement; use notification claim/result")
    rs.add_argument("--release", required=True)
    rs.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rs.add_argument("--final", action="store_true", help="This was the closing (end of Phase 4) email")
    rs.set_defaults(func=cmd_record_status_email)
