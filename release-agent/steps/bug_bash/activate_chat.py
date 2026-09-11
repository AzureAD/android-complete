"""Bind the Bug Bash chat to the exact acknowledged calendar invitation.

Resolve event ID -> join URL -> onlineMeeting.chatInfo.threadId. Topic searches and
bare pasted IDs are not evidence. Downstream senders only consume a current binding.
"""
from __future__ import annotations

from orchestrator import delivery
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.bug_bash.send_invite import delivered_invite
from tools.bugbash_meeting import meeting_chat_id

ID = "activate_chat"
KIND = "scout"


def stored_chat_id(state):
    """Return only a chat bound to the still-current sent invitation."""
    step = state.get_step("bug_bash", ID)
    data = step.data or {}
    try:
        invite = delivered_invite(state)
        verified = data.get("meeting") or {}
        if (step.status != "done" or data.get("invite") != invite
                or not meeting_chat_id(data.get("chat_id"))
                or verified.get("chat_id") != data["chat_id"]
                or verified.get("event_id") != invite["event_id"]
                or verified.get("subject") != invite["subject"]
                or not verified.get("join_url") or not verified.get("online_meeting_id")):
            return None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return data["chat_id"]


def chat_state_matches(state):
    """Freeze chat and invite sources in the existing notification scope."""
    if not stored_chat_id(state):
        raise ValueError("Missing/stale invitation-to-chat binding; run activate_chat")
    invite = delivered_invite(state)
    return [{"path": path, "hash": delivery.fingerprint(value)} for path, value in (
        (["steps", "bug_bash.activate_chat"], state.steps["bug_bash.activate_chat"]),
        (["steps", "bug_bash.send_invite"], state.steps["bug_bash.send_invite"]),
        (["notification_deliveries", invite["notification_id"]],
         state.notification_deliveries[invite["notification_id"]]),
        (["target_month"], state.target_month), (["ccd"], state.ccd), (["owner_email"], state.owner_email),
    )]


def build(state):
    if not state.ccd:
        return Blocked("activate_chat: no CCD set - cannot identify the Bug Bash meeting.")
    try:
        invite = delivered_invite(state)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Blocked(f"activate_chat: {exc}")
    instructions = (
        f"Run `record-bugbash-chat --release {state.release_id}` as the invite organizer. "
        f"It reads event {invite['event_id']} from the sent calendar receipt, resolves its "
        "join URL to onlineMeeting.chatInfo.threadId, verifies the chat and stores the binding. "
        "Never search by topic or accept a pasted ID as proof. If Teams has not exposed the "
        "thread, use Playwright to open THAT exact event's Chat pane, or ask the owner via "
        "m_ask_user to do so, then retry. Permission errors must remain blocked. "
        "A supplied --chat-id is only an assertion to compare against the resolved thread."
    )
    return NeedsSkill(
        tool="record-bugbash-chat",
        payload={"release": state.release_id,
                 "followup_command": f"record-bugbash-chat --release {state.release_id}",
                 "_gather": {"meeting_topic": invite["subject"], "event_id": invite["event_id"],
                             "instructions": instructions}},
        record_as=ID,
        summary=f"Verify the exact meeting chat for '{invite['subject']}'",
        note="awaiting event-bound chat verification",
    )
