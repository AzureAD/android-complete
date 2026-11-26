"""Step: `bugbash_updates` — post periodic bug-bash progress updates until tests complete
(Phase 3, bug_bash).

While manual/triage tests remain incomplete, `bugbash_updates` posts a progress update to
the Bug Bash meeting chat and remains `in_flight` on a 3h working-hours cooldown
(09:00–18:00 America/Los_Angeles, weekdays excluding US holidays). Once all tests finish,
it posts the closing summary and transitions to `done`.

Each update is grouped by the release test-point Tester (set by distribute_tests):
owners with remaining tests (not-run, failed, or blocked — failed/blocked surfaced, not
hidden) are @mentioned. Always list every workload case, including completed cases, with
its source-plan product ([Broker] / [Authenticator]), link and live state. Unresolved
work comes first; Passed/N/A rows follow. Each row's leading icon alone shows the outcome:
checkmark for Passed, dash for N/A, cross for Failed, stop sign for Blocked, square for
Not run. A single report legend explains the icons; no repeated trailing status text.
Automation triage keeps a short context note, not a second status or product label.
Owners who finished retain their full list and an "all completed" line (no mention).
A test counts as done only when it passed
(or is N/A). Broker and Authenticator cases actually written Failed by `ui_test_status` are shown
distinctly as 'triage', not a manual test to run. Live assignments identify the current
owner; a failed reassignment must not invent an owner change.

The header includes the Broker plan, Authenticator suite, MRWP ECS/Local runs, the
Authenticator UI-test pipeline and the test-account portal. Run links use the latest recorded RC's run_id fields
(auth.test, not the upstream APK build), with the existing organization-specific URL
helpers. Missing run IDs block preparation rather than produce guessed/stale links.

The denominator covers manual/triage work, not every case in the Authenticator suite.
Use the completed fill's automated-case classification (the same source as distribution)
to exclude automation-only Auth cases, regardless of their current ADO owner/outcome.
Keep applied automation failures as explicit triage; keep genuine manual Passed/N/A
results in the done count. No internal assignment list or cached owner totals are used.
Broker UI triage reads the completed fill's originally failed points in its exact UI
suite, matching point/case/configuration IDs. Multiple configurations count as one case;
all tracked failing configurations must resolve. Successful automation and unexecuted
configurations outside this triage set do not affect totals or resolution.

Depends on: clone_plans_broker (Broker plan id), clone_plans_auth (Auth suite id),
ui_test_status (completed automation classification), activate_chat (meeting chat id).
Blocks if the chat hasn't been activated or the completed fill is missing/stale.
Missing member evidence requests a read-only Teams ListChatMembers call before preparing
any notification. members_file must contain its fresh owner-bound M365 observation envelope
(or the native workiq_get_chat envelope for this exact meeting), never bare JSON.
If assigned UPNs differ from member emails, fetch the exact directory users through
the discovered MCP or verified native WorkIQ CLI; bind GUIDs to actual members.

Step defaults (`step_defaults` / offline scenarios):
  progress : inject the gathered progress dict (skip the live ADO reads); include every
             owner's tests with state and products (["Broker"], ["Authenticator"], or
             both for a shared case), including all completed rows.
  people   : verified Teams people {upn: {id: Entra GUID, name}} for offline rendering.
  send_to  : redirect the post to this chat id for testing.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext, thaw

import re

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done, InProgress
from orchestrator.step_context import MISSING
from tools import bugbash as BB
from tools.distribution import ConnectorObservationRequired
from tools import testplans as T
from tools.coordinates import coords
from tools.pipelines.auth_app import auth_build_url
from steps.build_verify._common import build_url, latest_rc, valid_id
from steps.bug_bash.activate_chat import stored_chat_id, chat_state_matches
from steps.bug_bash.ui_results import completed_result

ID = "bugbash_updates"
KIND = "scout"
NAME = 'Post bug-bash progress updates (active until marked complete)'
REQUIRED_TOOLS = ('az', 'workiq')
REQUIRES_APPROVAL = False
REPEATABLE = True
POLLABLE = True
LONG_RUNNING_IN_FLIGHT = True
NOTIFICATION = True

BROKER_SUITE_NAME = "Manual Tests (Android Broker)"

def _broker_plan(context):
    return (context.evidence.step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")

def _auth_suite(context):
    return (context.evidence.step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")

def gather(context):
    """(ok, progress, detail) — live progress, or the configured `progress` input."""
    inj = context.input("progress", MISSING)
    if inj is not MISSING:
        return (True, inj, "")
    bp, asuite = _broker_plan(context), _auth_suite(context)
    if not bp or not asuite:
        return (False, None, "the Broker plan / Auth suite aren't ready (run the clone steps).")
    try:
        result = completed_result(context)
        if result["auth"]["target"].get("layout"):
            from tools import distribution as D
            context.services.testplans.validate_release_targets(
                context.release.release_id, context.release.ccd, result["binding"],
                expected_names=D.release_target_names(
                    context.evidence.step("bug_bash", "clone_plans_broker").data,
                    context.evidence.step("bug_bash", "clone_plans_auth").data))
    except ValueError as exc:
        return False, None, str(exc)
    return context.services.testplans.gather_progress(bp, BROKER_SUITE_NAME, T.AUTH_PLAN, asuite,
                              auto_failed_ids=result["auth"]["failed_case_ids"],
                              auth_automated_ids=result["auth"]["automated_case_ids"],
                              broker_ui_result=result["broker"], auth_ui_result=result["auth"],
                              **({"monthly_result": result["monthly"]} if "monthly" in result else {}))

def plan_links(context):
    """Header destinations from the current release targets and recorded RC run IDs."""
    rc = latest_rc(context)
    runs = [
        ("MRWP · ECS run", (rc.get("ecs") or {}).get("run_id"), build_url),
        ("MRWP · Local run", (rc.get("local") or {}).get("run_id"), build_url),
        ("Auth pipeline", ((rc.get("auth") or {}).get("test") or {}).get("run_id"), auth_build_url),
    ]
    for name, run_id, _ in runs:
        if not valid_id(run_id):
            raise ValueError(f"Missing/invalid current RC run ID for {name}; refresh the owning verification step")
    from tools import auth_partition as A
    auth_data = thaw(context.evidence.step("bug_bash", "clone_plans_auth").data)
    if A.is_partition(auth_data):
        A.target(auth_data)
    monthly_links = []
    if "monthly" in auth_data:
        from tools import auth_monthly as M
        monthly_links = M.links(completed_result(context)["monthly"]["target"])
    return [
        {"name": "Broker test plan", "url": T.plan_web_url(_broker_plan(context))},
        {"name": "Authenticator suite", "url": T.plan_web_url(T.AUTH_PLAN, _auth_suite(context))},
    ] + [{"name": name, "url": url_for(run_id)} for name, run_id, url_for in runs] + [
        {"name": "Get test accounts", "url": coords.link("test_accounts")},
    ] + monthly_links

DEFAULT_POLL_HOURS = 3


def _cached_people_if_complete(context, chat_id: str, progress: dict) -> dict | None:
    """Reuse cached Entra mention identities when bound to `chat_id` and covering all pending owners."""
    cached = (context.evidence.step("bug_bash", ID).data or {}).get("cached_people")
    if not isinstance(cached, dict) or cached.get("chat_id") != chat_id:
        return None
    people = thaw(cached.get("people"))
    if not isinstance(people, dict):
        return None
    by_lower = {str(k).casefold(): v for k, v in people.items()}
    for upn, info in (progress.get("owners") or {}).items():
        if (info or {}).get("remaining", 0) > 0 and str(upn).casefold() not in by_lower:
            return None
    return people

def prepare_update(context, progress, members_file=None):
    """One resolved transport payload shared by initial and periodic progress posts."""
    from orchestrator import environment as env

    chat_id = stored_chat_id(context)
    if not chat_id:
        return False, None, "Missing/stale meeting binding"
    send_to_raw = context.input("send_to", MISSING)
    target_chat, redirected_self = env.resolve_bugbash_chat(
        chat_id,
        override=(None if send_to_raw is MISSING else send_to_raw),
    )
    people = context.input("people", MISSING) if members_file is None else MISSING
    if people is MISSING and redirected_self and members_file is None:
        # Active profile ('test' -> 'self') routes updates to personal self-chat ('48:notes'):
        # render full owner names without @-mentions so self-chat delivery works without pinging testers.
        try:
            content, _ = BB.render_update(
                progress,
                schedule.target_month_label(context.release) or "Bug Bash",
                plan_links(context),
                {},
                mention_pending=False,
            )
        except ValueError as exc:
            return False, None, str(exc)
        return True, {
            "chatId": target_chat,
            "content": "<p><b>[TEST · intended: Bug Bash meeting chat]</b></p>" + content,
            "contentType": "html",
            "mentions": [],
            "_resolved_people": {},
        }, ""
    if people is MISSING and members_file is None:
        reused = _cached_people_if_complete(context, chat_id, progress)
        if reused is not None:
            people = reused
    if people is MISSING:
        observation = None
        if members_file is not None:
            try:
                observation = context.services.assets.json_file(members_file)
            except (OSError, ValueError) as exc:
                return False, None, f"Cannot read meeting-member observation: {exc}"
        ok, people, detail = context.services.identities.resolve_mention_people(
            chat_id, progress.get("owners") or {}, member_observation=observation,
            owner=context.release.owner_email)
        if not ok:
            return False, None, detail
    try:
        content, mentions = BB.render_update(progress, schedule.target_month_label(context.release) or "Bug Bash",
                                             plan_links(context), people)
    except ValueError as exc:
        return False, None, str(exc)
    return True, {
        "chatId": target_chat,
        "content": content,
        "contentType": "html",
        "mentions": [] if redirected_self else mentions,
        "_resolved_people": thaw(people) if isinstance(people, dict) else {},
    }, ""

@dataclass(frozen=True)
class BuildParameters:
    members_file: str | None = None
    force: bool | str = False

    def __post_init__(self):
        if isinstance(self.force, str):
            norm = self.force.strip().lower()
            if norm in ("1", "true", "yes"):
                object.__setattr__(self, "force", True)
            elif norm in ("0", "false", "no", ""):
                object.__setattr__(self, "force", False)
            else:
                raise ValueError("force must be true or false")

PARAMETERS = {"build": BuildParameters}

def _cooldown_remaining_minutes(last_posted_iso: str | None, now_utc: datetime, interval_hours: int = DEFAULT_POLL_HOURS) -> int:
    last_dt = schedule.parse_iso_utc(last_posted_iso)
    if last_dt is None:
        return 0
    # Allow 5-minute grace so 2h/3h cron jitter never skips an aligned slot.
    target_min = max(30, interval_hours * 60 - 5)
    elapsed_min = int((now_utc - last_dt.astimezone(timezone.utc)).total_seconds() // 60)
    return max(0, target_min - elapsed_min)

def build(context: StepContext[BuildParameters]):
    members_file = context.parameters.members_file
    force = bool(context.parameters.force)
    if not context.release.ccd:
        return Blocked("bugbash_updates: no CCD set — can't title the Bug Bash.")
    chat_id = stored_chat_id(context)
    if not chat_id:
        return Blocked("bugbash_updates: meeting chat binding is missing/stale "
                       "(run activate_chat against the current invitation).")

    step_rec = context.evidence.step("bug_bash", ID)
    step_data = thaw(step_rec.data or {})
    posts_sent = int(step_data.get("posts_sent") or 0)
    last_posted_at = step_data.get("last_posted_at")

    ok, progress, detail = gather(context)
    if not ok:
        return Blocked(f"bugbash_updates: couldn't read test progress ({detail}).")

    month_year = schedule.target_month_label(context.release)
    if BB.all_complete(progress):
        if posts_sent == 0:
            if not progress["total"]:
                return Done(f"No manual or triage bug-bash work remains for {month_year}; nothing to post.")
            return Done(f"All {progress['total']} bug-bash tests are already complete — "
                        f"ready for {month_year} bug bash sign-off.")
        ok_fin, payload_fin, detail_fin = prepare_update(context, progress, members_file)
        if not ok_fin:
            return Done(f"All {progress['total']} bug-bash tests are complete — closing updates ({detail_fin}).")
        resolved_people = payload_fin.pop("_resolved_people", {})
        completion_text = (
            f"all {progress['total']} tests complete!"
            if progress["total"] else "no manual or triage work remaining!"
        )
        wrapup_html = (
            f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
            f'<p><b>🎉 {month_year} Bug Bash — {completion_text}</b><br>'
            f'Thanks everyone. Closing out automated updates.</p></div>'
            + payload_fin["content"]
        )
        final_note = f"Final wrap-up posted (#{posts_sent + 1}) — all {progress['total']} bug-bash tests complete"
        return NeedsSkill(
            tool="workiq_send_chat_message",
            payload={**payload_fin, "content": wrapup_html},
            record_as=ID,
            summary=f"Post final {month_year} bug-bash wrap-up (all {progress['total']} tests complete)",
            note=final_note,
            outbound=True,
            notification={
                "checkpoint": f"complete-{posts_sent + 1}",
                "state_matches": chat_state_matches(context),
                "completion": {
                    "status": "pass",
                    "note": final_note,
                    "data": {
                        **({"cached_people": {"chat_id": chat_id, "people": resolved_people}} if resolved_people else {}),
                        "posts_sent": posts_sent + 1,
                        "done": progress["done"],
                        "total": progress["total"],
                        "remaining": 0,
                        "poll_complete": True,
                    },
                    "stamp": ["last_posted_at"],
                },
            },
        )

    # For periodic updates after Update #1 (`posts_sent >= 1`), honor working hours + 3h cooldown unless `force=True`.
    if posts_sent >= 1 and not force:
        la_tz = ZoneInfo("America/Los_Angeles")
        now_raw = context.input("now", MISSING)
        now_dt = (
            datetime.fromisoformat(str(now_raw).replace("Z", "+00:00")).astimezone(la_tz)
            if now_raw is not MISSING and now_raw
            else datetime.now(la_tz)
        )
        if not BB.is_working_time(now_dt.replace(tzinfo=None)):
            return InProgress(
                step_rec.note
                or f"Update #{posts_sent} active ({progress['done']}/{progress['total']} done, {progress['remaining']} remaining) — paused outside 09:00–18:00 PT working hours."
            )
        rem_min = _cooldown_remaining_minutes(last_posted_at, now_dt.astimezone(timezone.utc))
        if rem_min > 0:
            return InProgress(
                step_rec.note
                or f"Update #{posts_sent} active ({progress['done']}/{progress['total']} done, {progress['remaining']} remaining) — next update in ~{rem_min}m; mark complete anytime."
            )

    try:
        ok, payload, detail = prepare_update(context, progress, members_file)
    except ConnectorObservationRequired as exc:
        return NeedsSkill(
            tool=exc.tool,
            payload={**exc.arguments, "_connector_reads": exc.requests,
                     "_followup": "Append the requested fresh owner-bound directory envelopes "
                                  "as directory_observations on the members-file envelope; rerun "
                                  "step-action with --param members_file=<path>. Do not record completion."},
            summary="Resolve exact pending tester UPNs to verified meeting-member user IDs",
            note=str(exc))
    if not ok:
        if members_file is None and context.input("people", MISSING) is MISSING:
            request = BB.mention_read_request(chat_id, context.release.owner_email)
            return NeedsSkill(
                tool="microsoft_teams-ListChatMembers",
                payload={"chatId": chat_id, "_connector_reads": [request],
                         "_followup": "Save the fresh owner-bound observation envelope; rerun "
                                      "step-action with --param members_file=<path>. Do not record completion."},
                summary="Gather complete meeting-member identities before preparing the update",
                note=detail)
        return Blocked(f"bugbash_updates: {detail}")

    resolved_people = payload.pop("_resolved_people", {})
    next_num = posts_sent + 1
    active_note = (
        f"Update #{next_num} posted ({progress['done']}/{progress['total']} done, "
        f"{progress['remaining']} remaining across {len(progress['owners'])} owner(s)) — active until marked complete"
    )
    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload=payload,
        record_as=ID,
        summary=(
            f"Post {month_year} bug-bash update #{next_num} "
            f"({progress['done']}/{progress['total']} done) to the meeting chat"
        ),
        note=active_note,
        outbound=True,
        notification={
            "checkpoint": f"post-{next_num}",
            "state_matches": chat_state_matches(context),
            "completion": {
                "status": "in_flight",
                "note": active_note,
                "data": {
                    **({"cached_people": {"chat_id": chat_id, "people": resolved_people}} if resolved_people else {}),
                    "posts_sent": next_num,
                    "done": progress["done"],
                    "total": progress["total"],
                    "remaining": progress["remaining"],
                },
                "stamp": ["last_posted_at"],
            },
        },
    )

KNOWLEDGE = {'summary': 'Post periodic bug-bash progress updates to the meeting chat until all tests finish.',
 'what': 'Posts progress updates to the Bug Bash meeting chat while tests remain active '
         '(staying `in_flight` on a 3h working-hours cadence and completing automatically '
         'when all tests finish). Each update is grouped '
         "by each test's release-point Tester (read live from BOTH the Broker 'Manual Tests "
         "(Android Broker)' subtree, Broker UI failure-triage points, and the Authenticator "
         'bug-bash suite). The code-generated header includes Broker test plan, Authenticator '
         'suite, MRWP · ECS run, MRWP · Local run, Auth pipeline and Get test accounts (portal '
         'URL from config/coordinates.yaml links.test_accounts). Source IDs come from the '
         'latest recorded RC: ecs.run_id, local.run_id and auth.test.run_id. Auth pipeline '
         'opens the UI-test run in msazure/One, not the upstream APK build; MRWP links use '
         'identitydivision/Engineering. No pipeline discovery/refetch is needed; '
         'missing/invalid run IDs require refreshing the owning verification step. Initial, '
         'periodic and final reports share these links. Counts cover manual/triage work, not '
         'every Authenticator suite case. The completed ui_test_status result supplies the '
         'same automated-case classification used by distribution. Automation-only Auth cases '
         'are excluded from both done and total, even if their shared ADO assignee is a manual '
         'tester. Applied automated failures remain visible as triage; genuine manual '
         'Passed/N/A cases still count as done. The filter is classification-based, never '
         '"remove all passes" or a shared Automated tag. A test counts as done only when it '
         'PASSED (or is N/A); not-run, failed, and blocked all count as remaining. Always list '
         'ALL workload cases under each person, not just those remaining: unresolved work '
         'first, then Passed/N/A rows. The leading icon is the only row outcome indicator '
         '(checkmark Passed, dash N/A, cross Failed, stop sign Blocked, square Not run), with '
         'a single report legend and no redundant trailing status text. Every row has a '
         'Broker/Authenticator label from its source plan (both labels if the same case is '
         'present in both); never infer product from titles. Broker and Authenticator cases '
         "actually written Failed in ui_test_status's completed result retain a short "
         "'Automation triage' context note. Resolved triage rows remain visible with their "
         'current outcome icon, not an open-failure label. Broker triage reads the completed '
         "fill's originally failed points from its exact UI suite, checking "
         'point/case/configuration identity. Count distinct cases, not configuration points; '
         'all originally failing configurations must resolve to Passed/N/A. Successful '
         'automation and untouched configurations outside that triage set are not included. '
         'Missing or changed tracked points block rather than disappear from totals. No raw '
         'Phase-2 capture schema, new projection or source-only/unapplied failure enters this '
         "set. Missing/partial/stale results surface an error requiring the owning fill's "
         'refresh. Owners with any remaining test are @mentioned; owners who finished appear '
         "by name with an 'all completed' line AND their complete case list (no mention). The "
         'final poller wrap-up includes the same full completed-case report instead of just a '
         "summary. The progress step resolves owners against the verified meeting's members, "
         'using directory lookup for UPN/SMTP differences. Pending owners require a real Entra '
         'user GUID and display name; membership-record IDs and UPN strings are not mention '
         'user IDs. Unknown/nonmember owners or lookup failures block preparation instead of '
         'silently sending plain email addresses. Both initial and periodic paths send '
         'matching HTML at-tags and canonical mentions metadata unchanged through notification '
         'claim. Scout supplies a fresh account/argument-bound Teams ListChatMembers or native '
         'workiq_get_chat observation envelope for the exact chat; raw legacy member JSON is '
         'not proof. Optional directory_observations link actual assigned UPNs to member GUIDs '
         'when SMTP differs: use the discovered structured fetch route for the exact '
         '/users/<verified-UPN> lookup requested by the command, not people search/Ask. Bind '
         'the returned immutable GUID to the exact meeting member GUID; never infer membership '
         'from an address or use a membership-record ID. Missing input prints exact read '
         'requests and never authorizes delivery. First-post notification prepare uses --param '
         'members_file=<path>, and the periodic command uses --members-file <path>. The chat '
         'identity is validated; never synthesize user IDs or use an unrelated chat.\n',
 'who': 'Driven automatically by `next --deliver` and `release-drainer` (`drain --deliver`). '
        'Caches resolved Entra mention identities across subsequent updates.\n',
 'how': 'Reads live test-point progress from Broker and Authenticator suites, checks the '
        '3h working-hours cooldown (`09:00–18:00 America/Los_Angeles` on business days), '
        'and delivers the update via `deliver-step` / `notification` claim/result. '
        'Stays `in_flight` while tests remain and completes automatically (`done`) once all '
        "tests finish. Blocks if the meeting chat isn't activated (`activate_chat`).\n",
 'faqs': [{'q': 'When do the updates run?',
           'a': 'On `release-drainer` passes (with a 3-hour cooldown) during 09:00–18:00 '
                'America/Los_Angeles on weekdays excluding US federal holidays.'},
          {'q': 'Why does a periodic update still retain a small delivery receipt?',
           'a': 'It prevents a retry or restarted worker from repeating a confirmed send '
                'without retaining the full report. Acknowledged message IDs (or provider '
                'evidence), destination, execution identity and the approved hash remain '
                'temporarily. Teams contains the delivered report; ADO remains authoritative '
                'for test progress. Uncertain sends retain full evidence and still require '
                'recovery.'},
          {'q': 'When do the updates stop?',
           'a': 'After every test completes, the final wrap-up must be claimed and '
                'successfully acknowledged before poll_complete is recorded. Known failed '
                'sends retry while the phase remains open. Owner sign-off/phase completion '
                'stops stale sends. Cleanup deletes first, then deregisters.'},
          {'q': 'Why are resolved tests still listed?',
           'a': 'The list should show the full denominator: 1/7 done means seven visible '
                'cases, one checked as passed. Passed and N/A have distinct icons explained '
                'once in the legend; rows do not repeat status text. Finished owners retain '
                'all their rows without being mentioned. Every case is labeled Broker or '
                'Authenticator from the source plan.'},
          {'q': "Why group by the release point's Tester instead of AssignedTo?",
           'a': "The release plan/suite point owns this release's manual/triage work. Shared "
                'test-case System.AssignedTo can belong to another release or template and is '
                'never the ownership fallback.'},
          {'q': "Why doesn't progress count every test assigned to someone in the full ADO "
                'suite?',
           'a': 'The suite also contains automated results whose shared case owner may be a '
                "manual tester. Those automation-only cases are not that person's Bug Bash "
                'workload: exclude them from both completed and total counts. Real manual '
                'completions still count; applied automation failures remain explicit '
                'owner-triage work.'},
          {'q': "Why are some of the owner's tests marked 'Automation triage'?",
           'a': 'Those are Broker or Authenticator points recorded as actually written Failed '
                'by ui_test_status. Current ownership comes from the live release-point '
                'Tester, not an assumed successful reassignment. They remain outstanding until '
                'resolved. Broker cases with multiple originally failing configurations count '
                'once and require every tracked failure to resolve.'}]}
