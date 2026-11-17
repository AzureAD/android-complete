"""`commands` CLI view — deterministic live-filled command guide & `/release-agent` launcher payload."""
from __future__ import annotations

import json as _json

from orchestrator import cli_common as C
from orchestrator import discovery, render


def _launcher_payload(report: dict | None) -> dict:
    if not report:
        return {
            "kind": "launcher_no_release",
            "question": "How would you like to use Release Agent?",
            "recommendedIndex": 0,
            "answers": [
                {
                    "title": "Start a new release",
                    "description": "Confirm clone folder + release date, then run the Readiness Entry Gate.",
                    "action": "start_release",
                },
                {
                    "title": "Check setup & permissions",
                    "description": "Verify CLI tools, MCP connectors, and M365 access without starting a release.",
                    "action": "check_setup",
                },
                {
                    "title": "List releases",
                    "description": "Inspect existing or past release runs on this machine.",
                    "action": "list_releases",
                },
                {
                    "title": "Show command guide",
                    "description": "Display all natural-language commands supported by /release-agent.",
                    "action": "show_commands",
                },
            ],
        }

    rid = report.get("release_id") or ""
    scheduled = report.get("scheduled")
    continue_desc = (
        f"Phase opens {scheduled.get('opens')} — recheck or adjust CCD."
        if scheduled and scheduled.get("opens")
        else "Run all eligible steps automatically until the next human checkpoint."
    )
    return {
        "kind": "launcher_active_release",
        "release": rid,
        "question": f"Release {rid} — what would you like to do?",
        "recommendedIndex": 0,
        "answers": [
            {
                "title": "Continue release",
                "description": continue_desc,
                "action": "continue",
            },
            {
                "title": "Update / override a step",
                "description": "Mark a step completed, completed manually, or skip (waive) it.",
                "action": "resolve_step",
            },
            {
                "title": "Re-run a step (with new inputs)",
                "description": "Pick any step to re-execute or pass updated parameters.",
                "action": "rerun_step",
            },
            {
                "title": "Command guide & step help",
                "description": "Show the live command cheat-sheet or explain a specific step.",
                "action": "show_commands",
            },
        ],
    }


def cmd_commands(args):
    """Render the live-filled command guide (and optional JSON launcher metadata)."""
    explicit_release = getattr(args, "release", None)
    as_of = C.parse_as_of(args)
    report = None
    resolution = "none"
    resolved_id = explicit_release

    if explicit_release:
        _st, orch = C.load_orch(args.runs_root, explicit_release, args.config, as_of)
        report = orch.status_report()
        resolution = "one"
    else:
        disc = discovery.resolve(args.runs_root, None, args.config)
        resolution = disc["resolution"]
        if disc.get("release"):
            resolved_id = disc["release"]["release_id"]
            _st, orch = C.load_orch(args.runs_root, resolved_id, args.config, as_of)
            report = orch.status_report()

    guide_md = render.commands_card(report)
    if getattr(args, "json", False):
        payload = {
            "has_active_release": report is not None,
            "resolution": resolution,
            "release_id": report.get("release_id") if report else None,
            "status": report.get("status") if report else "none",
            "current_phase": report.get("current_phase") if report else None,
            "current_phase_name": report.get("current_phase_name") if report else None,
            "guide_markdown": guide_md,
            "status_markdown": render.status_view(report) if report else None,
            "launcher_prompt": _launcher_payload(report),
        }
        print(_json.dumps(payload))
        return 0

    print(guide_md)
    return 0


def register(sub):
    p = sub.add_parser(
        "commands",
        help="Show the contextual Release Agent command guide (and launcher metadata with --json)",
    )
    p.add_argument("--release", default=None, help="Release id YYYY-MM (auto-detects active release if omitted)")
    p.add_argument("--as-of", dest="as_of", default="", help="Simulated clock YYYY-MM-DD")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_commands)
