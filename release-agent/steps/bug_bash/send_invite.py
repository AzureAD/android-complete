"""Step: `send_invite` — schedule the combined Bug Bash and send the Teams meeting invite
(Phase 3, bug_bash).

Composes the Bug Bash meeting from the release's real artifacts and returns a
NeedsSkill(workiq_create_event) the skill executes (the engine can't create calendar
events). The invite body is rendered from templates/bug-bash-invite.html (edit the file to
restyle — the step always renders from it).

When (agreed rule, see tools.invite.schedule_bugbash):
  * all scheduling uses America/Los_Angeles, not the owner or runner timezone
  * reached after 3pm or on a weekend -> next BUSINESS morning at 09:00
  * reached before 3pm on a weekday    -> later the SAME day, no earlier than 09:00
  Weekends roll forward to Monday. ~2h duration (extend if failures need investigation).

Recipients (real): the config `recipients` — the Azure Identity Android SDK / Android
Identity DL + the Dublin CIAM alias. Redirect for testing with the `send_to` mock knob
(keeps the send real, points it at you, tags the subject).

Links come from prior steps + Phase 2 (no re-discovery):
  * Broker test plan  <- clone_plans_broker.data.plan_id   (blocks if not cloned)
  * Auth test suite   <- clone_plans_auth.data.suite_id     (blocks if not created)
  * ECS / Local MRWP  <- state.pipeline_runs latest rc      (TBD if unresolved)
  * Auth ECS build    <- latest rc.auth.build.run_id         (blocks if missing/stale)
  * Local flags       <- ADO variable group 40 'local-flights' (live; TBD on failure)

Mock knobs (mocks.local.yaml / tests):
  now      : override the clock (ISO) so the schedule rule is deterministic in tests.
  flags    : inject the local-flights string (skip the live var-group fetch).
  send_to  : redirect the invite to these attendee(s) for testing (payload override).
"""
from __future__ import annotations

from datetime import datetime
from html import escape

from orchestrator import schedule
from orchestrator.delivery import fingerprint
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.context import resolve_recipients
from steps.lib.mockctx import mock_input, MISSING
from steps.build_verify._common import valid_id
from tools import invite as I
from tools import testplans as T
from tools.pipelines.auth_app import auth_build_url

ID = "send_invite"
KIND = "scout"
NOTIFICATION = True

# The real invite recipients (the Azure Identity Android SDK / Android Identity team DL +
# the Dublin CIAM alias). Redirect for a test with the `send_to` knob.
RECIPIENTS = ["androididentity@microsoft.com", "idnadevexciamdublin@microsoft.com"]

MOCKABLE = {
    "now": {"kind": "input", "desc": "Override the clock (ISO 8601) for the schedule rule."},
    "flags": {"kind": "input", "desc": "Inject the local-flights string (skip the var-group fetch)."},
    "send_to": {"kind": "payload", "sets": "attendees", "as": "list", "tag_subject": True,
                "desc": "Send the invite for real, but only to these attendee(s) (DL -> you)."},
}


def _latest_rc(state):
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    return rcs[-1] if rcs else {}


def delivered_invite(state):
    """Identity from this step's acknowledged calendar receipt, never a topic search."""
    step = state.get_step("bug_bash", ID)
    execution = (step.data or {}).get("_execution") or {}
    notification_id = execution.get("notification_id")
    record = state.notification_deliveries.get(notification_id) or {}
    item = record.get("descriptor") or {}
    attempts = record.get("attempts") or []
    if (step.status != "done" or record.get("status") != "sent"
            or (record.get("completion") or {}).get("status") != "applied"
            or not attempts):
        raise ValueError("A completed send_invite with its sent calendar receipt is required; "
                         "recover the original invitation evidence, do not create another meeting.")
    attempt = attempts[-1]
    receipt = attempt.get("receipt") or {}
    event_id = receipt.get("id")
    payload = item.get("payload") or {}
    scope = item.get("scope") or {}
    expected_subject = f"{schedule.target_month_label(state)} Release Bug Bash"
    if (not isinstance(event_id, str) or not event_id.strip()
            or item.get("id") != notification_id or item.get("release") != state.release_id
            or item.get("tool") != "workiq_create_event"
            or scope.get("phase") != "bug_bash" or scope.get("step") != ID
            or attempt.get("id") != execution.get("id") or attempt.get("status") != "sent"
            or item.get("hash") != fingerprint({k: v for k, v in item.items() if k != "hash"})
            or attempt.get("hash") != item.get("hash")
            or (item.get("completion") or {}).get("record_as") != ID
            or scope.get("release_matches", {}).get("owner_email") != state.owner_email
            or scope.get("release_matches", {}).get("ccd") != state.ccd
            or payload.get("subject") != expected_subject
            or not all(isinstance(payload.get(k), str) and payload[k] for k in ("start", "end", "timeZone"))):
        raise ValueError("Missing or mismatched invitation identity/receipt; owner recovery required.")
    return {"release": state.release_id, "notification_id": notification_id,
            "delivery_hash": item["hash"], "execution_id": execution["id"],
            "receipt_hash": fingerprint(receipt), "event_id": event_id,
            "owner": state.owner_email, "subject": payload["subject"],
            "start": payload["start"], "end": payload["end"], "timeZone": payload["timeZone"]}


def build(state):
    if not state.ccd:
        return Blocked("send_invite: no CCD set — can't title/schedule the Bug Bash.")

    # hard deps: both plans must exist (from the two clone steps)
    broker_plan = (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")
    if not broker_plan:
        return Blocked("send_invite: the Broker test plan hasn't been cloned yet "
                       "(clone_plans_broker) — run that first.")
    auth_suite = (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")
    if not auth_suite:
        return Blocked("send_invite: the Authenticator bug-bash suite hasn't been created yet "
                       "(clone_plans_auth) — run that first.")

    month_year = schedule.target_month_label(state)

    # when
    zone_name = I.SCHEDULING_TIMEZONE
    zone = schedule.get_tz(zone_name)
    if zone is None:
        return Blocked(f"send_invite: timezone data unavailable for {zone_name}")
    now_raw = mock_input("now", MISSING)
    if now_raw is not MISSING:
        now = datetime.fromisoformat(str(now_raw).replace("Z", "+00:00"))
        now = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    else:
        now = schedule.now_local(zone)
    start, end, when_note = I.schedule_bugbash(now.replace(tzinfo=None))
    start_zoned = start.replace(tzinfo=zone)
    offset = start_zoned.strftime("%z")
    when_note += f" ({zone_name}, UTC{offset[:3]}:{offset[3:]})"

    # links (Phase 2 pipeline runs — TBD if not resolved)
    rc = _latest_rc(state)
    ecs = (rc.get("ecs") or {}).get("run_id")
    local = (rc.get("local") or {}).get("run_id")
    auth_build = ((rc.get("auth") or {}).get("build") or {})
    if (not valid_id(auth_build.get("run_id")) or not valid_id(auth_build.get("rc"))
            or not valid_id(rc.get("rc")) or int(auth_build["rc"]) != int(rc["rc"])):
        return Blocked("send_invite: current RC Authenticator ECS build link is missing/invalid "
                       "or belongs to another RC; refresh auth_ecs before preparing the invitation.")

    # local flags (live var group 40, mockable)
    flags = mock_input("flags", MISSING)
    if flags is MISSING:
        ok, flags, _d = I.local_flights()
        if not ok:
            flags = None
    flags_html = I.format_flags_html(flags) if flags else "&lt;TBD — see variable group 40&gt;"

    tokens = {
        "MONTH_YEAR": month_year,
        "WHEN": when_note,
        "BROKER_PLAN_URL": I.testplan_url(broker_plan),
        "ECS_URL": I.build_url(ecs) or "#",
        "LOCAL_URL": I.build_url(local) or "#",
        "LOCAL_FLAGS_HTML": flags_html,
        "FLAGS_GROUP_URL": I.FLAGS_GROUP_URL,
        "AUTH_PLAN_URL": I.testplan_url(T.AUTH_PLAN, auth_suite),
        "AUTH_PIPELINE_URL": escape(auth_build_url(auth_build["run_id"]), quote=True),
    }
    body = I.render_invite(tokens)

    recipients, rnote, prefix = resolve_recipients(state, RECIPIENTS)
    subject = f"{prefix}{month_year} Release Bug Bash"

    return NeedsSkill(
        tool="workiq_create_event",
        payload={
            "subject": subject,
            "attendees": recipients,
            "body": body,
            "bodyContentType": "html",
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": zone_name,
            "isOnlineMeeting": True,
        },
        record_as=ID,
        summary=f"Schedule the {month_year} Bug Bash ({when_note}) + invite {len(recipients)} "
                f"recipient(s) ({rnote}). Save the returned event (including id) with "
                "notification result --receipt-file so chat activation can bind to this meeting.",
        note=f"invited {', '.join(recipients) if recipients else '(no recipients)'}",
        outbound=True,
        notification={"expires_at": start_zoned.isoformat()},
    )
