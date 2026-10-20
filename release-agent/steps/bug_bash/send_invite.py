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
  * ECS / Local MRWP  <- state.pipeline_runs latest rc      (blocks if unresolved)
  * Auth ECS build    <- latest rc.auth.build.run_id         (blocks if missing/stale)
  * Local flags       <- ADO variable group 40 'local-flights' (live; TBD on failure)

Mock knobs (mocks.local.yaml / tests):
  now      : override the clock (ISO) so the schedule rule is deterministic in tests.
  flags    : inject the local-flights string (skip the live var-group fetch).
  send_to  : redirect the invite to these attendee(s) for testing (payload override).
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from datetime import datetime
from dataclasses import asdict
from html import escape
from urllib.parse import urlparse

from orchestrator import schedule
from orchestrator.delivery import fingerprint
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.context import resolve_recipients
from steps.lib.mockctx import MISSING
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


def _latest_rc(context):
    rcs = context.evidence.pipeline_runs.get("rcs")
    return rcs[-1] if isinstance(rcs, list) and rcs and isinstance(rcs[-1], dict) else {}


def source_matches(context, *steps, pipelines=False):
    fields = {"clone_plans_broker": ("plan_id",), "clone_plans_auth": (
        "plan_id", "suite_id", "layout", "manual_suite_id", "automated_suite_id", "classification", "queries",
        "monthly", "monthly_source", "monthly_amendment"), "ui_test_status": ("result",)}
    sources = []
    for step in steps:
        record = context.evidence.step("bug_bash", step)
        prefix = ["steps", f"bug_bash.{step}"]
        sources.append((prefix + ["status"], record.status))
        sources.extend((prefix + ["data", key], record.data[key])
                       for key in fields[step] if key in record.data)
    sources += [(["target_month"], context.release.target_month),
                (["owner_name"], context.release.owner_name)]
    if pipelines:
        rc = _latest_rc(context)
        for path in (("rc",), ("ecs", "run_id"), ("local", "run_id"),
                     ("auth", "build", "run_id"), ("auth", "build", "rc")):
            value = rc
            for key in path:
                value = value[key]
            sources.append((["pipeline_runs", "rcs", -1, *path], value))
    return [{"path": path, "hash": fingerprint(value)} for path, value in sources]


def gate_preconditions(context, sources):
    return thaw(sources) + [
        {"path": ["release_id"], "value": context.release.release_id},
        {"path": ["ccd"], "value": context.release.ccd},
        {"path": ["owner_email"], "value": context.release.owner_email},
    ]


def _check_source_matches(context, scope):
    values = {"steps": {k: thaw(asdict(v)) for k, v in context.evidence.steps.items()},
              "pipeline_runs": thaw(context.evidence.pipeline_runs),
              "resources": thaw(context.evidence.resources),
              "target_month": context.release.target_month, "owner_name": context.release.owner_name}
    for match in scope.get("state_matches", []):
        path = match["path"]
        # The engine validates its workflow revision; this view only exposes step sources.
        if path == ["workflow_revision"]:
            continue
        try:
            value = values
            for key in path:
                value = value[key]
        except (KeyError, TypeError, IndexError):
            raise ValueError("Invitation source checkpoint missing; owner recovery required.") from None
        if fingerprint(value) != match.get("hash"):
            raise ValueError("Invitation source checkpoint changed; owner recovery required.")


def sent_notification(context, step_id, tool):
    """Read a step's exact terminal delivery, without granting new send authority."""
    try:
        step = context.evidence.step("bug_bash", step_id)
        notification_id = step.data.get("notification_id")
        if not isinstance(notification_id, str) or not notification_id.strip():
            raise ValueError("Missing delivery identity; recover the original notification evidence.")
        record = context.evidence.notification_deliveries.get(notification_id) or {}
        item = record.get("descriptor") or {}
        attempts = record.get("attempts") or []
        attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
        scope = item.get("scope") or {}
        if (step.status != "done" or record.get("status") != "sent"
                or (record.get("completion") or {}).get("status") != "applied"
                or item.get("id") != notification_id or item.get("release") != context.release.release_id
                or item.get("tool") != tool or scope.get("kind") != "step"
                or scope.get("phase") != "bug_bash" or scope.get("step") != step_id
                or not attempt.get("id")
                or attempt.get("id") != step.data.get("notification_execution_id")
                or attempt.get("status") != "sent"
                or item.get("hash") != fingerprint({k: v for k, v in item.items() if k != "hash"})
                or attempt.get("hash") != item.get("hash")
                or (item.get("completion") or {}).get("record_as") != step_id
                or (item.get("completion") or {}).get("kind") != "step"
                or scope.get("release_matches", {}).get("owner_email") != context.release.owner_email
                or scope.get("release_matches", {}).get("ccd") != context.release.ccd
                or ("generation" in scope
                    and scope["generation"] != (step.invalidated_at or "initial"))):
            raise ValueError("Missing or mismatched terminal notification evidence; owner recovery required.")
        _check_source_matches(context, scope)
        return step, item, attempt
    except (TypeError, KeyError, AttributeError, IndexError):
        raise ValueError("Malformed notification evidence; owner recovery required.") from None


def delivered_invite(context):
    """Identity from this step's acknowledged calendar receipt, never a topic search."""
    try:
        step, item, attempt = sent_notification(context, ID, "workiq_create_event")
        receipt = attempt.get("receipt") or {}
        event_id = receipt.get("id")
        payload = item.get("payload") or {}
        recipients = payload.get("attendees")
        expected_subject = f"{schedule.target_month_label(context.release)} Release Bug Bash"
        if (not isinstance(event_id, str) or not event_id.strip()
                or item.get("channel") != "calendar"
                or payload.get("subject") not in (expected_subject, "[TEST \u2192 me] " + expected_subject)
                or not isinstance(recipients, list) or not recipients
                or any(not isinstance(v, str) or v != v.strip() or "@" not in v for v in recipients)
                or item.get("target") != {"attendees": recipients}
                or not all(isinstance(payload.get(k), str) and payload[k]
                           for k in ("start", "end", "timeZone"))):
            raise ValueError("Missing or mismatched invitation identity/receipt; owner recovery required.")
        zone = schedule.get_tz(payload["timeZone"])
        start, end = [datetime.fromisoformat(payload[k]).replace(tzinfo=zone) for k in ("start", "end")]
        if zone is None or start >= end:
            raise ValueError("Invalid invitation time range; owner recovery required.")
        for key in ("optionalAttendees", "resourceAttendees"):
            if key in payload and (not isinstance(payload[key], list)
                    or any(not isinstance(v, str) or v != v.strip() or "@" not in v
                           for v in payload[key])):
                raise ValueError("Invalid invitation recipient list; owner recovery required.")
        join = (receipt.get("onlineMeeting") or {}).get("joinUrl")
        if join is not None:
            parsed = urlparse(join)
            if (parsed.scheme != "https" or parsed.hostname not in ("teams.microsoft.com", "teams.live.com")
                    or parsed.username or parsed.password):
                raise ValueError("Malformed invitation join URL; owner recovery required.")
    except (TypeError, KeyError, AttributeError, IndexError):
        raise ValueError("Malformed invitation evidence; owner recovery required.") from None
    return {"release": context.release.release_id, "notification_id": item["id"],
            "delivery_hash": item["hash"],
            "execution_id": step.data["notification_execution_id"],
            "receipt_hash": fingerprint(receipt), "event_id": event_id,
            "owner": context.release.owner_email, "subject": payload["subject"],
            **{k: thaw(payload[k]) for k in ("attendees", "optionalAttendees", "resourceAttendees")
               if k in payload},
            **({"join_url": join} if join is not None else {}),
            "start": payload["start"], "end": payload["end"], "timeZone": payload["timeZone"]}


def build(context: StepContext):
    if not context.release.ccd:
        return Blocked("send_invite: no CCD set — can't title/schedule the Bug Bash.")

    # hard deps: both plans must exist (from the two clone steps)
    broker_step = context.evidence.step("bug_bash", "clone_plans_broker")
    broker_plan = broker_step.data.get("plan_id")
    if broker_step.status != "done" or not valid_id(broker_plan):
        return Blocked("send_invite: the Broker test plan hasn't been cloned yet "
                       "(clone_plans_broker) — run that first.")
    auth_step = context.evidence.step("bug_bash", "clone_plans_auth")
    auth_suite = auth_step.data.get("suite_id")
    if auth_step.status != "done" or not valid_id(auth_suite):
        return Blocked("send_invite: the Authenticator bug-bash suite hasn't been created yet "
                       "(clone_plans_auth) — run that first.")
    from tools import auth_partition as A
    monthly_links = []
    try:
        A.current(context, thaw(auth_step.data))
        if A.has_monthly(thaw(auth_step.data)):
            from .ui_results import completed_result
            from tools import auth_monthly as M, distribution as D
            result = completed_result(context)
            context.services.testplans.validate_release_targets(
                context.release.release_id, context.release.ccd, result["binding"],
                expected_names=D.release_target_names(broker_step.data, auth_step.data))
            monthly_links = M.links(result["monthly"]["target"])
    except (ValueError, KeyError, TypeError) as exc:
        return Blocked(f"send_invite: {exc}")

    month_year = schedule.target_month_label(context.release)

    # when
    zone_name = I.SCHEDULING_TIMEZONE
    zone = schedule.get_tz(zone_name)
    if zone is None:
        return Blocked(f"send_invite: timezone data unavailable for {zone_name}")
    now_raw = context.input("now", MISSING)
    if now_raw is not MISSING:
        try:
            now = datetime.fromisoformat(str(now_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return Blocked("send_invite: invalid scheduling clock; supply an ISO 8601 timestamp.")
        now = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)
    else:
        now = context.clock.now().astimezone(zone)
    start, end, when_note = I.schedule_bugbash(now.replace(tzinfo=None))
    start_zoned = start.replace(tzinfo=zone)
    offset = start_zoned.strftime("%z")
    when_note += f" ({zone_name}, UTC{offset[:3]}:{offset[3:]})"

    # Links must be resolved from the current Phase-2 RC, never guessed.
    rc = _latest_rc(context)
    ecs_node, local_node, auth_node = (rc.get(k) for k in ("ecs", "local", "auth"))
    ecs = ecs_node.get("run_id") if isinstance(ecs_node, dict) else None
    local = local_node.get("run_id") if isinstance(local_node, dict) else None
    auth_build = auth_node.get("build") if isinstance(auth_node, dict) else None
    auth_build = auth_build if isinstance(auth_build, dict) else {}
    if (not valid_id(auth_build.get("run_id")) or not valid_id(auth_build.get("rc"))
            or not valid_id(rc.get("rc")) or int(auth_build["rc"]) != int(rc["rc"])):
        return Blocked("send_invite: current RC Authenticator ECS build link is missing/invalid "
                       "or belongs to another RC; refresh auth_ecs before preparing the invitation.")
    if not valid_id(ecs) or not valid_id(local):
        return Blocked("send_invite: current RC ECS/Local run IDs are missing/invalid; "
                       "refresh the pipeline evidence before preparing the invitation.")

    # local flags (live var group 40, mockable)
    flags = context.input("flags", MISSING)
    if flags is MISSING:
        ok, flags, _d = context.services.pipelines.local_flights()
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
    body = I.render_invite(tokens, context.services.assets.invite_template())
    for link in monthly_links:
        body += f'<p><a href="{escape(link["url"], quote=True)}">{escape(link["name"])}</a> — release-owned failure investigations; canonical E2E cases unchanged.</p>'

    recipients, rnote, prefix = resolve_recipients(context, RECIPIENTS)
    subject = f"{prefix}{month_year} Release Bug Bash"

    sources = source_matches(context, "clone_plans_broker", "clone_plans_auth", pipelines=True)
    if A.has_monthly(thaw(auth_step.data)):
        sources += source_matches(context, "ui_test_status")
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
                f"recipient(s) ({rnote}). This is a proposed slot, not availability or OOF evidence. "
                "Scout must verify a future booking and recipient consent before claiming this exact "
                "payload; unknown availability stays unresolved, never assume anyone is free or away. "
                "Save the returned event (including id) with "
                "notification result --receipt-file so chat activation can bind to this meeting.",
        note=f"invited {', '.join(recipients) if recipients else '(no recipients)'}",
        outbound=True,
        notification={"expires_at": start_zoned.isoformat(),
                      "state_matches": sources,
                      "completion": {"data": {
                          "gate_preconditions": gate_preconditions(context, sources)}}},
    )
