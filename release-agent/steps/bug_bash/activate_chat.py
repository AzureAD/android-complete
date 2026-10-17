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
        "calendar receipt: workiq_get_my_profile, workiq_get_event with the exact eventId "
        "and complete typed attendees, then microsoft_teams-GetChat with the exact thread "
        "derived from that validated event's Teams meetup-join URL. Supply timestamped, "
        "owner-account-bound structured observation envelopes using --profile-observation, "
        "--event-observation and --chat-observation; the command prints exact requests and "
        "follow-up flags when evidence is absent and never calls Azure CLI or raw Graph. "
        "Observations are tool-host-supplied provenance, not cryptographic identity or send approval. "
        "For an opaque modern join URL without a thread, no native onlineMeeting resolver is "
        "assumed: stop with a missing-capability/owner-recovery request. "
        "Never search by topic or accept a pasted ID as proof. If Teams has not exposed the "
        "thread, use Playwright to open THAT exact event's Chat pane, or ask the owner via "
        "m_ask_user to do so, then use the owner-confirmed link recovery. HTTP 403 is a permission problem, not an "
        "uninitialized thread; waiting or opening Chat is not a permission fix. "
        "On the first access failure, stop retrying and immediately offer owner-confirmed "
        "chat-link recovery, manual posting, or keeping this held. The owner may open the "
        "exact invitation's Chat pane and provide its Teams chat/message link. Preview "
        f"`record-bugbash-chat --release {context.release.release_id} --chat-link <link>`, "
        "show the exact invitation/link and owner-attestation limitations, then ask for "
        "explicit confirmation that they activated this invitation's chat. Record only "
        "that approved review_hash with --confirm-activated --review-hash <hash> "
        "--approved-by <owner_email> --reason <owner confirmation>. This path does not "
        "require Graph or invent provider verification; notification sends still require "
        "their normal claims/receipts. Never reject it solely because Graph is forbidden. "
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
