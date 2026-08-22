"""`record-bugbash-chat` — store the resolved Bug Bash meeting chat id (Phase-3
`activate_chat` recorder seam).

The scout step's skill resolves the meeting chat id (search → Playwright activate → human
fallback) and calls this to persist it:
  * --chat-id given  -> store it on the step (data.chat_id) + mark the step done. The
    periodic-update step reads it back via steps.bug_bash.activate_chat.stored_chat_id.
  * --chat-id omitted -> hold the step for the owner (attention): the chat couldn't be
    resolved; the owner must open the meeting + click Chat to activate the thread.
"""
from __future__ import annotations

from orchestrator import cli_common as C

_MEETING_PREFIX = "19:meeting_"


def cmd_record_bugbash_chat(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    chat_id = (args.chat_id or "").strip()

    if not chat_id:
        detail = ("Could not resolve the Bug Bash meeting chat. Open the meeting in Teams "
                  "and click Chat (or send one message) to activate the thread, then re-run "
                  "this step.")
        orch.record_scout_step("bug_bash", "activate_chat", "attention", detail)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] activate_chat: {detail}", kind="step")
        print(detail)
        return 2

    if not chat_id.startswith(_MEETING_PREFIX):
        # not fatal — warn but still store (allow non-meeting chat ids for flexibility/tests)
        print(f"warning: chat id '{chat_id}' doesn't look like a meeting chat "
              f"({_MEETING_PREFIX}…@thread.v2); storing anyway.")

    orch.record_scout_step("bug_bash", "activate_chat", "pass",
                           f"Bug Bash meeting chat ready: {chat_id}")
    step = orch.state.get_step("bug_bash", "activate_chat")
    step.data = dict(step.data or {})
    step.data["chat_id"] = chat_id
    step.by = "scout"
    orch.state.set_step("bug_bash", "activate_chat", step)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[ok] activate_chat: meeting chat stored ({chat_id})", kind="step")
    print(f"Stored Bug Bash meeting chat id: {chat_id}")
    return 0


def register(sub):
    p = sub.add_parser("record-bugbash-chat",
                       help="Store the resolved Bug Bash meeting chat id (Phase-3 activate_chat)")
    p.add_argument("--release", required=True)
    p.add_argument("--chat-id", default=None, dest="chat_id",
                   help="The meeting chat id (19:meeting_…@thread.v2). Omit to hold for the owner.")
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.set_defaults(func=cmd_record_bugbash_chat)
