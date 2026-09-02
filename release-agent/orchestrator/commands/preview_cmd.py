"""`preview-release` command — the full identity of a release BEFORE init.

The start-a-release prompt used to be two confusing steps: (1) "which month?"
— ambiguous (the release id is the CCD/work month) — then (2) "…so it's the
<next month> release, right?" — which felt like a contradiction, because a
release is NAMED for its ship month (CCD month + 1). This command lets the
skill collapse that into ONE coherent confirmation: it derives, for the
default (current) month plus the next few, the ship-month display name AND the
default CCD date, so the skill can lead with "Start the October 2026 release
(code-complete Wed Sep 9, 2026)?" and offer the alternatives by name + date.

Read-only, release-independent, no state written — pure schedule math.
"""
from __future__ import annotations
import json as _json

from orchestrator import schedule


def cmd_preview_release(args):
    """Preview release candidates (ship-month name + default CCD) for the start prompt.
    --month <YYYY-MM> sets the first candidate (default: current month); --count N returns
    that many consecutive months (default 4). --json for machine output."""
    start = getattr(args, "month", None)
    count = getattr(args, "count", None) or 4
    cands = schedule.preview_releases(start, count)
    if not cands:
        print("could not compute release candidates (bad --month?)")
        return 1
    if getattr(args, "json", False):
        print(_json.dumps(cands, indent=2))
        return 0
    print("Release candidates (named for the SHIP month = code-complete month + 1):")
    for c in cands:
        tag = "   ← default (current month)" if c.get("is_default") else ""
        print(f"  {c['ship_label']} release  —  code-complete {c['ccd_pretty']}  —  id {c['release_id']}{tag}")
    return 0


def register(sub):
    pr = sub.add_parser("preview-release",
                        help="Preview release candidates (ship-month name + default CCD) for the start prompt")
    pr.add_argument("--month", default=None, help="First candidate as YYYY-MM (default: current month)")
    pr.add_argument("--count", type=int, default=4, help="Number of consecutive months to preview (default 4)")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=cmd_preview_release)
