"""Verify the sent invitation's exact meeting thread, then record activate_chat.

The optional --chat-id is an assertion, never a way to bypass event resolution.
Already-completed bindings cannot be silently replaced; failures leave state unchanged.
"""
from __future__ import annotations

from orchestrator import cli_common as C, delivery
from orchestrator.outcomes import Done
from steps.bug_bash import activate_chat, send_invite
from tools import bugbash_meeting


def cmd_record_bugbash_chat(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config)
    chat_id = (args.chat_id or "").strip()
    guard = orch.step_action_guard("bug_bash", "activate_chat")
    if isinstance(guard, Done):
        stored = activate_chat.stored_chat_id(st)
        if stored and (not chat_id or stored == chat_id):
            print(f"Already bound to meeting chat {stored}; no changes.")
            return 0
        print("Chat step is terminal but this binding is stale/different. Owner-reviewed reopen "
              "of activate_chat is required; never silently replace a completed binding.")
        return 1
    if guard is not None:
        print(guard.reason)
        return 1
    try:
        if getattr(args, "as_of", None):
            raise ValueError("Chat recording requires the current clock, not --as-of")
        if chat_id and not bugbash_meeting.meeting_chat_id(chat_id):
            raise ValueError("Invalid meeting chat ID")
        invite = send_invite.delivered_invite(st)
        meeting = bugbash_meeting.resolve(invite)
        if chat_id and chat_id != meeting["chat_id"]:
            raise ValueError("Supplied chat does not belong to the acknowledged invitation")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Chat binding blocked: {exc}")
        return 1
    orch.record_scout_step("bug_bash", "activate_chat", "pass",
                           f"Bug Bash chat verified for event {invite['event_id']}: {meeting['chat_id']}")
    step = orch.state.get_step("bug_bash", "activate_chat")
    step.data = dict(step.data or {})
    step.data.update(chat_id=meeting["chat_id"], invite=invite, meeting=meeting,
                     verified_at=delivery.now_iso())
    step.by = "scout"
    orch.state.set_step("bug_bash", "activate_chat", step)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[ok] activate_chat: event-bound chat stored ({meeting['chat_id']})", kind="step")
    return 0


def cmd_record_nativeauth_notify(args):
    """Legacy terminal replay only; a bare engineer is not delivery evidence."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    completed = orch.completed_step_outcome("bug_bash", "notify_native_auth")
    if completed:
        print(completed.note)
        return 0
    print("Use notification prepare/claim/result with a verified engineer; no delivery may be inferred.")
    return 1


def register(sub):
    p = sub.add_parser("record-bugbash-chat",
                       help="Store the resolved Bug Bash meeting chat id (Phase-3 activate_chat)")
    p.add_argument("--release", required=True)
    p.add_argument("--chat-id", default=None, dest="chat_id",
                   help="Optional assertion; must equal the thread resolved from the sent invitation")
    p.set_defaults(func=cmd_record_bugbash_chat)

    n = sub.add_parser("record-nativeauth-notify",
                       help="Record that the Native Auth release engineer was notified the bug bash is ready")
    n.add_argument("--release", required=True)
    n.add_argument("--engineer", default=None,
                   help="Alias or UPN of the notified Native Auth RE. Omit to hold for the owner.")
    n.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    n.set_defaults(func=cmd_record_nativeauth_notify)
