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


def cmd_record_nativeauth_notify(args):
    """Record that the Native Auth release engineer was notified the bug bash is ready.
      * --engineer given  -> store it (data.engineer) + mark notify_native_auth done.
      * --engineer omitted -> hold the step for the owner (attention): couldn't resolve/send.
    """
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    eng = (args.engineer or "").strip()

    if not eng:
        detail = ("Could not resolve/notify the Native Auth release engineer. Check the "
                  "release-engineer schedule and send them the bug-bash-ready message, then "
                  "re-run this step with --engineer.")
        orch.record_scout_step("bug_bash", "notify_native_auth", "attention", detail)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release,
               f"[attention] notify_native_auth: {detail}", kind="step")
        print(detail)
        return 2

    orch.record_scout_step("bug_bash", "notify_native_auth", "pass",
                           f"Native Auth RE notified: {eng}")
    step = orch.state.get_step("bug_bash", "notify_native_auth")
    step.data = dict(step.data or {})
    step.data["engineer"] = eng
    step.by = "scout"
    orch.state.set_step("bug_bash", "notify_native_auth", step)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[ok] notify_native_auth: Native Auth RE notified ({eng})", kind="step")
    print(f"Recorded Native Auth RE notification: {eng}")
    return 0


def cmd_record_native_auth_signoff(args):
    """Record the Native Auth sign-off (release engineer attests the Native Auth team signed
    off). --by given → store + mark done; omitted → hold the step for the owner."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    return _record_signoff(orch, args, "native_auth_signoff", "Native Auth")


def cmd_record_did_signoff(args):
    """Record the DID sign-off (from the DID contact, Sowmya Malayanur). --by given → store +
    mark done; omitted → hold the step for the owner."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    return _record_signoff(orch, args, "did_signoff", "DID")


def _record_signoff(orch, args, step_id, label):
    by = (args.by or "").strip()
    if not by:
        detail = (f"{label} sign-off still pending. Re-run with --by '<who>' once it's "
                  f"received.")
        orch.record_scout_step("bug_bash", step_id, "attention", detail)
        C.save_state(orch.state, args.runs_root, args.release)
        C.emit(args.runs_root, args.release, f"[attention] {step_id}: {detail}", kind="step")
        print(detail)
        return 2

    orch.record_scout_step("bug_bash", step_id, "pass", f"{label} sign-off: {by}")
    step = orch.state.get_step("bug_bash", step_id)
    step.data = dict(step.data or {})
    step.data["by"] = by
    step.by = "scout"
    orch.state.set_step("bug_bash", step_id, step)
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release, f"[ok] {step_id}: {label} sign-off ({by})", kind="step")
    print(f"Recorded {label} sign-off: {by}")
    return 0


def register(sub):
    p = sub.add_parser("record-bugbash-chat",
                       help="Store the resolved Bug Bash meeting chat id (Phase-3 activate_chat)")
    p.add_argument("--release", required=True)
    p.add_argument("--chat-id", default=None, dest="chat_id",
                   help="The meeting chat id (19:meeting_…@thread.v2). Omit to hold for the owner.")
    p.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    p.set_defaults(func=cmd_record_bugbash_chat)

    n = sub.add_parser("record-nativeauth-notify",
                       help="Record that the Native Auth release engineer was notified the bug bash is ready")
    n.add_argument("--release", required=True)
    n.add_argument("--engineer", default=None,
                   help="Alias or UPN of the notified Native Auth RE. Omit to hold for the owner.")
    n.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    n.set_defaults(func=cmd_record_nativeauth_notify)

    s = sub.add_parser("record-native-auth-signoff",
                       help="Record the Native Auth sign-off (release engineer attests)")
    s.add_argument("--release", required=True)
    s.add_argument("--by", default=None, help="Who signed off Native Auth. Omit to hold the step.")
    s.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    s.set_defaults(func=cmd_record_native_auth_signoff)

    dd = sub.add_parser("record-did-signoff",
                        help="Record the DID sign-off (from the DID contact, Sowmya Malayanur)")
    dd.add_argument("--release", required=True)
    dd.add_argument("--by", default=None, help="Who gave the DID sign-off. Omit to hold the step.")
    dd.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    dd.set_defaults(func=cmd_record_did_signoff)
