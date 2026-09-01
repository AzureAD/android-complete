"""`post-bugbash-update` — one tick of the bug-bash update poller (Phase-3
`bugbash_updates`), and `record-bugbash-updates-started` — the no-op followup that keeps
the trigger step from being force-recorded after the first post.

post-bugbash-update: gate on the working window (09:00–18:00 America/Los_Angeles, weekday,
not a US holiday), read live progress, and print a deterministic decision the poller acts
on:
  off_hours  — outside the working window; send nothing.
  no_chat    — the meeting chat isn't activated (run activate_chat).
  error      — couldn't read progress (detail included).
  complete   — every test is done: content is the completion summary; the poller posts it,
               deregisters itself, and the owner signs off (bugbash_complete).
  post       — content (HTML) + mentions to send to chatId.

`--now` overrides the clock for the window math (tests). `--force` skips the window gate
(the immediate first post is handled by the step itself, so this is mainly for testing).
"""
from __future__ import annotations
import json as _json
from datetime import datetime
from zoneinfo import ZoneInfo

from orchestrator import cli_common as C
from orchestrator import schedule
from tools import bugbash as BB
from steps.bug_bash.activate_chat import stored_chat_id
from steps.bug_bash import bugbash_updates as BU

_LA = ZoneInfo("America/Los_Angeles")


def cmd_post_bugbash_update(args):
    now = None
    if getattr(args, "now", None):
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(_json.dumps({"decision": "error", "detail": f"bad --now: {args.now!r}"}))
            return 1
    if now is None:
        now = datetime.now(_LA)
    now_naive = now.replace(tzinfo=None)

    if not getattr(args, "force", False) and not BB.is_working_time(now_naive):
        print(_json.dumps({"decision": "off_hours",
                           "note": "outside 09:00–18:00 LA on a working day"}))
        return 0

    st = C.load_state(args.runs_root, args.release)
    chat_id = stored_chat_id(st)
    if not chat_id:
        print(_json.dumps({"decision": "no_chat",
                           "note": "meeting chat not activated (run activate_chat)"}))
        return 0

    ok, progress, detail = BU.gather(st)
    if not ok:
        print(_json.dumps({"decision": "error", "detail": detail}))
        return 0

    month_year = schedule.target_month_label(st) or "Bug Bash"

    if BB.all_complete(progress):
        summary = (f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
                   f'<p><b>🎉 {month_year} Bug Bash — all {progress["total"]} tests complete!</b><br>'
                   f'Thanks everyone. Closing out the bash; no more automated updates.</p></div>')
        print(_json.dumps({"decision": "complete", "chatId": chat_id, "content": summary,
                           "total": progress["total"]}))
        return 0

    content, mentions = BB.render_update(progress, month_year, BU.plan_links(st))
    print(_json.dumps({"decision": "post", "chatId": chat_id,
                       "content": content, "mentions": mentions,
                       "done": progress["done"], "total": progress["total"],
                       "remaining": progress["remaining"]}))
    return 0


def register(sub):
    p = sub.add_parser("post-bugbash-update",
                       help="One tick of the bug-bash update poller: gate the window, read "
                            "progress, print a post/complete/off_hours decision")
    p.add_argument("--release", required=True)
    p.add_argument("--now", default=None, help="Override 'now' (ISO 8601) for the window math")
    p.add_argument("--force", action="store_true", help="Skip the working-window gate")
    p.set_defaults(func=cmd_post_bugbash_update)
