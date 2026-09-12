"""Event-log commands: log (show/analyze) and journal (record interaction)."""
from __future__ import annotations
import json as _json

from orchestrator.eventlog import EventLog, summarize
from orchestrator import cli_common as C


def cmd_log(args):
    """Show or analyze this release's event log (per-release only)."""
    el = EventLog(args.runs_root, args.release)
    events = el.read(args.limit)
    if args.analyze:
        print(_json.dumps(summarize(events), indent=2))
        return 0
    if args.json:
        print(_json.dumps(events, indent=2))
        return 0
    if not events:
        print("No events logged yet.")
        return 0
    for e in events:
        src = e.get("source", "engine")
        loc = f" {e['phase']}/{e.get('step','')}" if e.get("phase") else ""
        extra = ""
        if e.get("event") == "step_qa":
            q = (e.get("question") or "").replace("\n", " ")
            a = (e.get("answer") or "").replace("\n", " ")
            extra += f"  Q=\"{q[:60]}{'…' if len(q) > 60 else ''}\"  A=\"{a[:60]}{'…' if len(a) > 60 else ''}\""
        if e.get("driver"):
            extra += f"  driver=\"{e['driver']}\""
        if e.get("text"):
            t = e["text"].replace("\n", " ")
            extra += f"  \"{t[:80]}{'…' if len(t) > 80 else ''}\""
        if e.get("choice"):
            extra += f"  choice={e['choice']}"
        print(f"  {e['ts']}  {src:<6} {e.get('actor','?'):<10} {e['event']}{loc}{extra}")
    return 0


def cmd_journal(args):
    """Record an INTERACTION event (what Scout showed / what the user chose).
    The skill calls this so the per-release log captures the real conversation
    for debugging. Best-effort; never affects the flow."""
    el = C.elog(args.runs_root, args.release)
    if args.kind == "qa":
        el.qa(args.question or args.text or "", args.answer or "",
              phase=args.phase or None, step=args.step or None)
        return 0
    if args.source == "scout":
        el.scout_said(args.text or "", kind=args.kind or "message", options=args.option or None)
    else:
        el.user_said(args.text or "", kind=args.kind or "input", choice=args.choice or None)
    return 0


def register(sub):
    lg = sub.add_parser("log", help="Show or analyze this release's event log")
    lg.add_argument("--release", required=True)
    lg.add_argument("--analyze", action="store_true", help="Print a rolled-up summary")
    lg.add_argument("--limit", type=int, default=None)
    lg.add_argument("--json", action="store_true")
    lg.set_defaults(func=cmd_log)

    jn = sub.add_parser("journal", help="Record an interaction event (scout output / user input / step Q&A)")
    jn.add_argument("--release", required=True)
    jn.add_argument("--source", choices=["scout", "user"], default="user",
                    help="Who produced it (ignored for --kind qa, which is two-sided)")
    jn.add_argument("--text", default="", help="What was shown / said")
    jn.add_argument("--kind", default="", help="e.g. prompt, checklist, message, choice, input, qa")
    jn.add_argument("--choice", default="", help="For user: the option id/label chosen")
    jn.add_argument("--option", action="append", help="For scout: an option presented (repeatable)")
    jn.add_argument("--question", default="", help="For --kind qa: the question the user asked")
    jn.add_argument("--answer", default="", help="For --kind qa: the answer Scout gave")
    jn.add_argument("--phase", default="", help="For --kind qa: the phase the question was about")
    jn.add_argument("--step", default="", help="For --kind qa: the step the question was about")
    jn.set_defaults(func=cmd_journal)
