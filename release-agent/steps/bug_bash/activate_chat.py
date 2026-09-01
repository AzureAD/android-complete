"""Step: `activate_chat` — resolve & activate the Bug Bash meeting chat, store its id
(Phase 3, bug_bash).

The periodic bug-bash updates (next step) post to the meeting's Teams chat. But a Teams
meeting chat is DORMANT until it's activated once (first join, first message, or opening
its Chat pane) — only then does it get a stable `19:meeting_…@thread.v2` id that the
automated poster can reach via workiq_send_chat_message.

This scout step resolves + activates that chat and stores the id on the step
(`data.chat_id`) so the poller step can read it. Three-tier resolution (skill-driven):

  1. SEARCH — find the meeting chat by topic (workiq_search_chats). Already active → done.
  2. ACTIVATE (Playwright) — if dormant, open Teams web (signed in) → Calendar → the
     meeting → click Chat (materializes the thread), then re-search for the id.
  3. HUMAN FALLBACK — if activation fails, ask the owner to open the meeting and click
     Chat / send one message, then re-search (or accept a pasted chat id).

The skill runs `record-bugbash-chat --release <id> --chat-id <resolved>` to store it (or
without --chat-id to hold the step for the owner when it truly can't be resolved).
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked

ID = "activate_chat"
KIND = "scout"


def stored_chat_id(state):
    """The resolved Bug Bash meeting chat id, or None — read by the periodic-update step."""
    return (state.get_step("bug_bash", ID).data or {}).get("chat_id")


def build(state):
    if not state.ccd:
        return Blocked("activate_chat: no CCD set — can't identify the Bug Bash meeting.")
    month_year = schedule.target_month_label(state)
    topic = f"{month_year} Release Bug Bash"

    instructions = (
        f"Resolve and activate the Bug Bash meeting chat, then store its id.\n"
        f"1. SEARCH: `workiq_search_chats` for a meeting chat whose topic is "
        f"'{topic}' (chatType 'meeting', id like 19:meeting_…@thread.v2). If found, that's "
        f"the id — go to step 4.\n"
        f"2. ACTIVATE (Playwright) if not found — the chat is dormant: open Teams web "
        f"(https://teams.microsoft.com/v2/, already signed in) → Calendar → next week if "
        f"needed → click the '{topic}' meeting → click the **Chat** button in the peek "
        f"(this 'turns on' the meeting chat). Then re-run `workiq_search_chats` for "
        f"'{topic}' to get the now-active id.\n"
        f"3. HUMAN FALLBACK if Playwright can't activate it: use `m_ask_user` to ask the "
        f"owner to open the '{topic}' meeting in Teams and click **Chat** (or send one "
        f"message) to activate the thread — then re-search, or accept a chat id they paste.\n"
        f"4. Run `record-bugbash-chat --release {state.release_id} --chat-id '<id>'`. If it "
        f"truly can't be resolved, run it WITHOUT --chat-id to hold the step for the owner."
    )

    return NeedsSkill(
        tool="record-bugbash-chat",              # follow-up recorder command
        payload={
            "release": state.release_id,
            "chat_id": None,
            "followup_command": (f"record-bugbash-chat --release {state.release_id} "
                                 f"--chat-id '<resolved-chat-id>'"),
            "_gather": {"meeting_topic": topic, "instructions": instructions},
        },
        record_as=ID,
        summary=f"Resolve + activate the '{topic}' meeting chat, then store its id",
        note="awaiting meeting-chat resolution (search → Playwright activate → human fallback)",
    )
