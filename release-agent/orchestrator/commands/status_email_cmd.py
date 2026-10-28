"""Read-only partner digest preparation. Delivery uses notification claim/result."""
from __future__ import annotations
import json as _json
import re

import yaml

from orchestrator import cli_common as C
from orchestrator import status_email as SE, delivery as D
from orchestrator import notifications as notif
from orchestrator import schedule
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
    zone = schedule.get_tz(st.timezone) if st.timezone else None
    if zone is None or _orch.now_local.tzinfo is None:
        raise ValueError("Daily status email requires a valid stored owner timezone and aware clock")
    now = _orch.now_local.astimezone(zone)
    today = now.date()
    force = bool(getattr(args, "force", False))
    cfg = notif.load_config(args.config)
    phases = (cfg.get("status_email") or {}).get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("Configure status_email.phases explicitly")
    scope = {"kind": "window", "phases": phases,
             "until_steps": (cfg.get("status_email") or {}).get("until_steps", []),
             "date": today.isoformat(), "release_matches": {"owner_email": st.owner_email}}
    scope["not_before"] = now.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    reason = D.scope_reason(_orch, scope)
    if reason:
        return {"skip": True, "reason": reason, "release": args.release}

    recipients = _recipients(args.config)
    if getattr(args, "send_to", None):                     # test redirect
        recipients = [x.strip() for x in str(args.send_to).split(",") if x.strip()]

    res = SE.compose(
        st,
        _phase_order(args.config),
        recipients,
        changes=_broker_changes(st),
        selection=_orch.scheduling(),
    )

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


def _review_scope(st, orch, *, phase=None):
    if not st.timezone or schedule.get_tz(st.timezone) is None or orch.now_local.tzinfo is None:
        raise ValueError("Status drafts require a valid stored owner timezone and aware clock")
    scope = {
        "kind": "phase_completion" if phase else "release",
        "date": orch.now_local.date().isoformat(),
        "requires_owner_approval": True,
        "release_matches": {"owner_email": st.owner_email},
        "state_matches": [
            {"path": [key], "hash": D.fingerprint(getattr(st, key))}
            for key in ("steps", "gate_decisions", "pipeline_runs", "versions",
                        "ccd", "target_month", "active_conditionals", "owner_name")
        ],
    }
    if phase:
        scope["phase"] = phase
    return scope


def prepare_reviewed_status_emails(args, st, orch):
    """Prepare independent, owner-reviewed drafts without advancing release work."""
    ad_hoc = args.source == "ad-hoc-status-email"
    request_id = getattr(args, "request_id", None)
    if ad_hoc:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", request_id):
            raise ValueError("Ad-hoc status requires a stable --request-id (1-80 letters/digits/._-)")
        for record in st.notification_deliveries.values():
            item = D.validate_record(orch, record)
            if (item["id"].startswith("ad-hoc-status-email:")
                    and record["status"] in ("claimed", "uncertain")):
                raise ValueError("Resolve the existing owned ad-hoc email before preparing another request")
        candidates = [(f"ad-hoc-status-email:{request_id}", None)]
    else:
        if request_id is not None:
            raise ValueError("--request-id is only valid for an ad-hoc status draft")
        candidates = [
            (f"phase-status-email:{phase.definition.id}", phase.definition.id)
            for phase in orch.scheduling().phases
            if phase.included and phase.complete and phase.due and phase.definition.steps
        ]
    items, reason, changes = [], "", None
    for logical_id, phase in candidates:
        scope = _review_scope(st, orch, phase=phase)
        reason = D.scope_reason(orch, scope)
        previous = st.notification_deliveries.get(logical_id + ":email")
        if previous:
            D.validate_record(orch, previous)
        if reason or previous and previous["status"] not in ("prepared", "not_sent"):
            continue
        if changes is None:
            changes = _broker_changes(st)
        recipients = _recipients(args.config)
        if getattr(args, "send_to", None):
            recipients = [value.strip() for value in args.send_to.split(",") if value.strip()]
        result = SE.compose(
            st, _phase_order(args.config), recipients, changes=changes,
            selection=orch.scheduling(), purpose="ad_hoc" if ad_hoc else "phase",
            completed_phase=phase)
        items.append(D.descriptor(
            st, logical_id, scope, "workiq_send_email",
            {"to": result["to"], "subject": result["subject"], "body": result["html"], "isHtml": True}))
    return {
        "release": args.release, "notifications": items, "permission_to_send": False,
        **({"reason": reason} if reason else {}),
    }


def cmd_status_email(args):
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
        if getattr(args, "ad_hoc", None) is not None:
            args.source, args.request_id = "ad-hoc-status-email", args.ad_hoc
            result = prepare_reviewed_status_emails(args, st, orch)
        else:
            result = prepare_status_email(args, st, orch)
        print(_json.dumps(result))
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
                    help="Bypass business-day cadence only, never 17:00/lifecycle/acknowledgement guards")
    se.add_argument("--send-to", default=None,
                    help="Redirect recipients to these address(es) (comma-separated) for a test run")
    se.add_argument("--ad-hoc", metavar="REQUEST_ID",
                    help="Preview an engineer-requested draft outside daily cadence; reuse its identity for recovery")
    se.set_defaults(func=cmd_status_email)

    rs = sub.add_parser("record-status-email",
                        help="Retired acknowledgement; use notification claim/result")
    rs.add_argument("--release", required=True)
    rs.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rs.add_argument("--final", action="store_true", help="This was the closing (end of Phase 4) email")
    rs.set_defaults(func=cmd_record_status_email)
