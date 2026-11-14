"""`preview-release` command — pre-init release date & label candidates.

Defaults to offering the 2nd Wednesday of the current month; if that date has
already passed in the current month, marks the 2nd Wednesday of the next month
as `recommended`. Release labels directly match the calendar month of the
elected release date (e.g. a date in November 2026 is labeled November 2026).

Read-only, release-independent, no state written — pure schedule math.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from orchestrator import schedule


def cmd_preview_release(args):
    """Preview release candidates (calendar-month label + default 2nd-Wed CCD) for the start prompt.
    --month <YYYY-MM> sets the first candidate (default: current month); --count N returns
    that many consecutive months (default 4). --json for machine output."""
    start = getattr(args, "month", None)
    count = getattr(args, "count", None) or 4
    as_of = C.parse_as_of(args)
    cands = schedule.preview_releases(start, count, as_of=as_of)
    if not cands:
        print("could not compute release candidates (bad --month?)")
        return 1
    if getattr(args, "json", False):
        print(_json.dumps(cands, indent=2))
        return 0
    print("Release candidates (label matches the elected release date's calendar month):")
    for c in cands:
        notes = []
        if c.get("ccd_past"):
            notes.append("2nd-Wed has passed — pick a custom day in this month or use next month")
        if c.get("recommended"):
            notes.append("default offer")
        elif c.get("is_default"):
            notes.append("current month")
        tag = f"   ← {'; '.join(notes)}" if notes else ""
        print(f"  {c['ship_label']} ({c['label']})  —  {c['ccd_pretty']} ({c['ccd']})  —  id {c['release_id']}{tag}")
    return 0


def register(sub):
    pr = sub.add_parser("preview-release",
                        help="Preview release candidates (calendar-month label + default 2nd-Wed CCD) for the start prompt")
    pr.add_argument("--month", default=None, help="First candidate as YYYY-MM (default: current month)")
    pr.add_argument("--count", type=int, default=4, help="Number of consecutive months to preview (default 4)")
    pr.add_argument("--as-of", dest="as_of", default="", help="Simulated clock YYYY-MM-DD (default: today)")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_preview_release)
