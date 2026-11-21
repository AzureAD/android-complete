"""Bind the Bug Bash chat to the exact acknowledged calendar invitation.

Verify native connector observations, or record an explicit owner-confirmed Teams
link. Topic searches and bare pasted IDs are not evidence of either kind.
"""
from __future__ import annotations
from urllib.parse import urlparse

from orchestrator.step_context import StepContext

from orchestrator import delivery
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.bug_bash.send_invite import delivered_invite, gate_preconditions
from tools.bugbash_meeting import meeting_chat_id, manual_matches, native_matches

ID = "activate_chat"
KIND = "scout"
NAME = 'Activate the Bug Bash meeting chat + store its id'
REQUIRED_TOOLS = ('workiq', 'microsoft_teams')
REQUIRES_APPROVAL = False


def meeting_matches(invite, verified, chat_id):
    if verified.get("verification") == "owner_attested":
        return manual_matches(invite, verified, chat_id)
    if verified.get("verification") == "native_connectors":
        return native_matches(invite, verified, chat_id)
    join = urlparse(verified.get("join_url") or "")
    return (verified.get("verification") is None
            and meeting_chat_id(chat_id)
            and verified.get("chat_id") == chat_id
            and verified.get("event_id") == invite["event_id"]
            and verified.get("subject") == invite["subject"]
            and ("join_url" not in invite or invite["join_url"] == verified.get("join_url"))
            and join.scheme == "https" and join.hostname in ("teams.microsoft.com", "teams.live.com")
            and not join.username and not join.password
            and isinstance(verified.get("online_meeting_id"), str)
            and bool(verified["online_meeting_id"].strip()))


def stored_chat_id(context):
    """Return only a chat bound to the still-current sent invitation."""
    step = context.evidence.step("bug_bash", ID)
    data = step.data or {}
    try:
        invite = delivered_invite(context)
        verified = data.get("meeting") or {}
        if (step.status != "done" or data.get("invite") != invite
                or not meeting_matches(invite, verified, data.get("chat_id"))):
            return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return data["chat_id"]


def chat_state_matches(context):
    """Freeze chat and invite sources in the existing notification scope."""
    if not stored_chat_id(context):
        raise ValueError("Missing/stale invitation-to-chat binding; run activate_chat")
    invite = delivered_invite(context)
    data = context.evidence.step("bug_bash", ID).data
    sources = binding_preconditions(context, invite)
    sources += [{"path": ["steps", "bug_bash.activate_chat", "status"], "value": "done"}]
    paths = (("chat_id",), ("meeting",)) if data["meeting"].get(
        "verification") in ("owner_attested", "native_connectors") else (
        ("chat_id",), ("meeting", "event_id"), ("meeting", "chat_id"),
        ("meeting", "join_url"), ("meeting", "online_meeting_id"), ("meeting", "subject"))
    for path in paths:
        value = data
        for key in path:
            value = value[key]
        sources.append({"path": ["steps", "bug_bash.activate_chat", "data", *path],
                        "hash": delivery.fingerprint(value)})
    return sources


def binding_preconditions(context, invite):
    if invite.get("adoption_hash"):
        return gate_preconditions(context, [
            {"path": ["steps", "bug_bash.send_invite", "status"], "value": "done"},
            {"path": ["steps", "bug_bash.send_invite", "data", "external_adoption"],
             "hash": invite["adoption_hash"]},
        ])
    prefix = ["notification_deliveries", invite["notification_id"]]
    return gate_preconditions(context, [
        {"path": ["target_month"], "value": context.release.target_month},
        {"path": ["steps", "bug_bash.send_invite", "status"], "value": "done"},
        {"path": ["steps", "bug_bash.send_invite", "data", "notification_id"],
         "value": invite["notification_id"]},
        {"path": ["steps", "bug_bash.send_invite", "data", "notification_execution_id"],
         "value": invite["execution_id"]},
        {"path": prefix + ["descriptor", "hash"], "value": invite["delivery_hash"]},
        {"path": prefix + ["status"], "value": "sent"},
        {"path": prefix + ["completion", "status"], "value": "applied"},
        {"path": prefix + ["attempts", -1, "id"], "value": invite["execution_id"]},
        {"path": prefix + ["attempts", -1, "receipt", "id"], "value": invite["event_id"]},
        {"path": prefix + ["attempts", -1, "receipt"], "hash": invite["receipt_hash"]},
    ])


def build(context: StepContext):
    if not context.release.ccd:
        return Blocked("activate_chat: no CCD set - cannot identify the Bug Bash meeting.")
    try:
        invite = delivered_invite(context)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Blocked(f"activate_chat: {exc}")
    instructions = (
        f"Run `record-bugbash-chat --release {context.release.release_id}` as the invite organizer. "
        f"It requests read-only native evidence for event {invite['event_id']} from the sent "
        "recorded calendar invitation: workiq_get_my_profile, workiq_get_event with the exact eventId "
        "and complete typed attendees, then microsoft_teams-GetChat with the exact thread "
        "derived from that validated event's Teams meetup-join URL. Supply timestamped, "
        "owner-account-bound structured observation envelopes using --profile-observation, "
        "--event-observation and --chat-observation; the command prints exact requests and "
        "follow-up flags when evidence is absent and never calls Azure CLI or raw Graph. "
        "Observations are tool-host-supplied provenance, not cryptographic identity or send approval. "
        "For an opaque modern join URL without a thread, no native onlineMeeting resolver is "
        "assumed: stop with a missing-capability/owner-recovery request. "
        "Never search by topic or accept a bare pasted ID without a Teams URL. "
        "DO NOT spend multiple turns fighting Playwright on Teams Web if the thread is not "
        "exposed yet or SSO/UI automation fails: on the FIRST missing-thread or access failure, "
        "stop retrying and immediately ask the owner via m_ask_user to activate the chat manually and share the link:\n"
        "  1. Open the Bug Bash meeting invite in Outlook or Teams Calendar and click 'Chat with participants' "
        "(send a quick 'Bug Bash chat ready' message if needed so Teams materializes the thread).\n"
        "  2. Click '...' (More options) -> 'Copy link' on a message or the meeting chat header.\n"
        "  3. Paste that `https://teams.microsoft.com/...` link here.\n"
        "Then preview "
        f"`record-bugbash-chat --release {context.release.release_id} --chat-link <link>`, "
        "show the exact invitation/link and owner-attestation limitations, ask for explicit "
        "confirmation that they activated this invitation's chat, and record that approved "
        "review_hash with `--confirm-activated --review-hash <hash> --approved-by <owner_email> "
        "--reason \"<owner confirmation>\"`. This extracts and stores the exact `19:meeting_...@thread.v2` "
        "`chat_id` required by `bugbash_updates` without needing Graph or Playwright."
        "For an already-approved manual-posting path, use "
        f"`bugbash-progress --release {context.release.release_id}` without reopening chat "
        "or replaying notifications. The report never acknowledges delivery or human review. "
        "A supplied --chat-id is only an assertion to compare against the resolved thread."
    )
    return NeedsSkill(
        tool="record-bugbash-chat",
        payload={"release": context.release.release_id,
                 "followup_command": f"record-bugbash-chat --release {context.release.release_id}",
                 "_gather": {"meeting_topic": invite["subject"], "event_id": invite["event_id"],
                             "instructions": instructions}},
        record_as=ID,
        summary=f"Bind the exact meeting chat for '{invite['subject']}' (native evidence or owner-confirmed link)",
        note="awaiting event-bound chat verification",
    )

KNOWLEDGE = {'summary': "Bind the exact sent invitation's chat through native connectors or an "
            'owner-confirmed link.',
 'what': "Resolves the current send_invite calendar receipt's event id, reads that exact "
         "event's Teams join URL and derives its encoded meeting thread. Native WorkIQ reads "
         'the profile/event; Agency Teams GetChat verifies the exact thread. Opaque numeric '
         'links request owner recovery, not an assumed onlineMeeting API. Calendar subject, '
         'organizer, times, cancellation state and chat type/topic are verified. Stores '
         'chat_id plus meeting identity and the originating invitation binding. A previous '
         "month's chat, a topic-only match or a pasted thread id is not proof. An explicit "
         'owner-confirmed Teams link is a separate supported recovery mode: it records '
         'owner-attested identity, not independent provider verification.\n',
 'who': 'Scout gathers exact read-only native connector observations as the organizer. If '
        "Teams has not exposed the thread, open that exact calendar event's Chat pane with "
        'Playwright or ask the owner to do so, then retry. On the FIRST auth/access failure, '
        'stop unchanged retries and offer owner-confirmed chat-link recovery, manual posting, '
        'or keeping this held. Never choose another chat or manufacture a thread id.\n',
 'how': 'Run record-bugbash-chat --release <id>. The command enforces phase prerequisites, '
        'requires a completed invite with an applied sent receipt and resolves the thread '
        'itself. Optional --chat-id must equal the verified thread; it never authorizes an '
        'unverified chat. Use --profile-observation, --event-observation and '
        '--chat-observation with fresh owner/tool/argument-bound envelopes. Missing inputs '
        'print exact connector reads, not completion. The engine never obtains a Graph token '
        'through Azure CLI. No calendar or Teams message is sent. An identical completed '
        'binding is a no-op; changing a completed binding requires owner-reviewed reopen of '
        'activate_chat first. Missing historical event receipts require explicit owner '
        'recovery, not creating a replacement invitation or hand-editing state. Progress only '
        'consumes a current binding; prepared messages bind the invite receipt and chat so '
        'changed sources cannot authorize delivery or completion. Previously prepared legacy '
        'messages require owner review at deployment. HTTP 403 is not repaired by waiting for '
        'a thread. For manual link recovery, preview record-bugbash-chat --release <id> '
        '--chat-link <link> without Graph or state changes. Show the exact '
        'event/recipients/link and owner-attestation limitations; ask the owner to confirm '
        "they activated THIS invitation's chat and copied THIS link. Then record with "
        '--confirm-activated --review-hash <hash> --approved-by <owner_email> --reason "<owner '
        'confirmation>". Stale reviews or a contradictory receipted thread reject. Do not '
        'reject this path solely because Graph access is forbidden. It proves neither current '
        'event/attendee state nor posting access; delivery still needs normal receipts. If the '
        'owner explicitly chooses manual posting, preserve the reviewed skips and use '
        'bugbash-progress --release <id> for a private read-only report. Do not reopen chat or '
        'resend notifications just to read progress.\n',
 'faqs': [{'q': "Why can't it just post to the meeting chat directly?",
           'a': 'A topic is not unique and can identify an older release. Use native '
                'verification of the exact sent event/thread, or explicit owner confirmation '
                "of the link copied from that event's Chat pane. Link confirmation is "
                'owner-attested, not provider-verified.'},
          {'q': "What if Playwright can't open the chat?",
           'a': "Ask the owner to activate the exact invitation's Chat pane and copy its Teams "
                'link. Preview that link and obtain explicit owner confirmation before '
                'recording the reviewed hash. Graph permission failure does not prevent this '
                'recovery. Manual posting remains available if automated sending is '
                'unavailable.'}]}
