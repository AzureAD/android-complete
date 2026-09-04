"""Step: `send_invite` — schedule the combined Bug Bash and send the Teams meeting invite
(Phase 3, bug_bash).

Composes the Bug Bash meeting from the release's real artifacts and returns a
NeedsSkill(workiq_create_event) the skill executes (the engine can't create calendar
events). The invite body is rendered from templates/bug-bash-invite.html (edit the file to
restyle — the step always renders from it).

When (agreed rule, see tools.invite.schedule_bugbash):
  * reached after 3pm or on a weekend -> next BUSINESS morning at 09:00
  * reached before 3pm on a weekday    -> later the SAME day
  Weekends roll forward to Monday. ~2h duration (extend if failures need investigation).

Recipients (real): the config `recipients` — the Azure Identity Android SDK / Android
Identity DL + the Dublin CIAM alias. Redirect for testing with the `send_to` mock knob
(keeps the send real, points it at you, tags the subject).

Links come from prior steps + Phase 2 (no re-discovery):
  * Broker test plan  <- clone_plans_broker.data.plan_id   (blocks if not cloned)
  * Auth test suite   <- clone_plans_auth.data.suite_id     (blocks if not created)
  * ECS / Local MRWP  <- state.pipeline_runs latest rc      (TBD if unresolved)
  * Local flags       <- ADO variable group 40 'local-flights' (live; TBD on failure)

Mock knobs (mocks.local.yaml / tests):
  now      : override the clock (ISO) so the schedule rule is deterministic in tests.
  flags    : inject the local-flights string (skip the live var-group fetch).
  send_to  : redirect the invite to these attendee(s) for testing (payload override).
"""
from __future__ import annotations

from datetime import datetime

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.context import resolve_recipients
from steps.lib.mockctx import mock_input, MISSING
from tools import invite as I
from tools import testplans as T

ID = "send_invite"
KIND = "scout"

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
    now_raw = mock_input("now", MISSING)
    if now_raw is not MISSING:
        now = datetime.fromisoformat(str(now_raw).replace("Z", "+00:00")).replace(tzinfo=None)
    else:
        now = datetime.now()
    start, end, when_note = I.schedule_bugbash(now)

    # links (Phase 2 pipeline runs — TBD if not resolved)
    rc = _latest_rc(state)
    ecs = (rc.get("ecs") or {}).get("run_id")
    local = (rc.get("local") or {}).get("run_id")

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
        "AUTH_PIPELINE": "&lt;TBD&gt;",
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
            "timeZone": getattr(state, "timezone", None) or "America/Los_Angeles",
            "isOnlineMeeting": True,
        },
        record_as=ID,
        summary=f"Schedule the {month_year} Bug Bash ({when_note}) + invite {len(recipients)} "
                f"recipient(s) ({rnote})",
        note=f"invited {', '.join(recipients) if recipients else '(no recipients)'}",
        outbound=True,
    )
