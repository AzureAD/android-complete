"""`bugbash-progress` — read-only private/manual Bug Bash progress report."""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from orchestrator import schedule
from tools import bugbash as BB
from steps.bug_bash import bugbash_updates as BU
from steps.bug_bash.ui_results import completed_result


def cmd_bugbash_progress(args):
    """Private/manual report only: no chat lookup, offers, completion or pruning."""
    try:
        st, orch = C.load_orch(args.runs_root, args.release, args.config)
        context = orch.context("bug_bash", "bugbash_updates")
        result = completed_result(context)
        ok, progress, detail = BU.gather(context)
        if not ok:
            raise ValueError(f"Could not read Bug Bash progress: {detail}")
        content, _ = BB.render_update(
            progress, schedule.target_month_label(st) or "Bug Bash",
            BU.plan_links(context), mention_pending=False)
    except (ValueError, OSError) as exc:
        print(_json.dumps({"decision": "error", "detail": str(exc),
                           "notifications": [], "permission_to_send": False}))
        return 1
    print(_json.dumps({
        "decision": "preview_only",
        "note": "Private/manual preview; nothing sent or completed. Mentions are not included. "
                "UI-failure review, Native Auth sign-off and Bug Bash completion still require the owner.",
        "content": content, "contentType": "html",
        "done": progress["done"], "total": progress["total"], "remaining": progress["remaining"],
        "investigations": result["investigations"],
        "notifications": [], "permission_to_send": False,
    }))
    return 0


def register(sub):
    preview = sub.add_parser(
        "bugbash-progress", help="Read-only private/manual Bug Bash progress and UI investigation evidence; no chat required")
    preview.add_argument("--release", required=True)
    preview.set_defaults(func=cmd_bugbash_progress)
