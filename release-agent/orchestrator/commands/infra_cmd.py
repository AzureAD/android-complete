"""Infrastructure preflight command: check CLIs + register/verify MCP servers in
Scout. Named infra_cmd to avoid clashing with the orchestrator.infra module."""
from __future__ import annotations
import json as _json

from orchestrator import infra
from orchestrator import cli_common as C


def cmd_infra(args):
    """Infrastructure preflight: check CLI/host deps and register + verify the
    MCP servers the skill needs in Scout. Run before the tool-level requirements.
    Registers missing MCP servers into Scout's config (backup first) unless
    --no-register; --json for machine output."""
    try:
        inventory = None
        if getattr(args, "tools_file", None):
            with open(args.tools_file, encoding="utf-8-sig") as stream:
                inventory = _json.load(stream)
            if not isinstance(inventory, dict):
                raise ValueError("--tools-file must contain a version-1 loaded-tool inventory object")
        report = infra.run(
            C.REQUIREMENTS_CONFIG, register=not getattr(args, "no_register", False),
            tool_inventory=inventory, scout_root=getattr(args, "scout_root", None),
            agency_path=getattr(args, "agency_path", None), workiq_path=getattr(args, "workiq_path", None))
    except (OSError, ValueError) as exc:
        if getattr(args, "json", False):
            print(_json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Infrastructure preflight failed: {exc}")
        return 1
    if getattr(args, "json", False):
        print(_json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    print("Infrastructure preflight")
    if not report.get("scout_present", True):
        print(f"  Microsoft Scout profile not found: {report['scout_root']}")
        scout_req = next((r for r in report["requirements"] if r["id"] == "scout"), None)
        if scout_req and scout_req.get("install"):
            print(f"      install: {scout_req['install']}")
        print("      Install Scout FIRST, then re-run — MCP servers can't be registered without it.")
    for request in report["path_requests"]:
        print(f"  [PATH REQUIRED] {request['question']} Supply {request['flag']}.")
    print("  CLIs / host:")
    for r in report["requirements"]:
        mark = "OK" if r["ok"] else "MISSING"
        print(f"    [{mark}] {r['name']}")
        if not r["ok"] and r["install"]:
            print(f"        install: {r['install']}")
    print("  MCP servers (Scout config):")
    if not report["mcp_servers"]:
        print("    (none required)")
    for m in report["mcp_servers"]:
        label = {"present": "OK", "registered": "REGISTERED", "would_register": "MISSING",
                 "provider_missing": "PROVIDER MISSING", "launcher_missing": "LAUNCHER MISSING",
                 "scout_missing": "SCOUT NOT INSTALLED"}.get(m["status"], m["status"].upper())
        print(f"    [{label}] {m['name']} — {m['detail']}")
    connectors = report["m365_connectors"]
    if connectors is not None:
        label = {"inventory_required": "CHECK IN SCOUT", "missing_tools": "ACTION NEEDED",
                 "tools_available": "TOOLS AVAILABLE"}[connectors["status"]]
        print(f"  Microsoft 365: [{label}] {connectors['detail']}")
    if report["restart_needed"]:
        print("\n  ⚠ RESTART Scout to load newly-registered MCP server(s).")
    if report["setup_status"] == "needs_scout_check":
        print("\n  Installation prerequisites checked. One check remains in Scout.")
    elif not report["ok"]:
        print("\n  Setup needs attention. No release has been started by this check.")
    else:
        print("\n  Setup prerequisites OK. Release-specific readiness checks are still required.")
    print(f"  {report['next_action']}")
    return 0 if report["ok"] else 1


def register(sub):
    inf = sub.add_parser("infra", help="Infrastructure preflight: check CLIs + register/verify MCP servers in Scout")
    inf.add_argument("--no-register", action="store_true", help="Only report; don't register missing MCP servers")
    inf.add_argument("--tools-file", help="Fresh version-1 observed_at/available_tools inventory from inside Scout")
    inf.add_argument("--scout-root", help="Existing Scout profile directory (default: per-user .scout)")
    inf.add_argument("--agency-path", help="Agency executable; otherwise discover PATH, configured launchers and defaults")
    inf.add_argument("--workiq-path", help="Existing native WorkIQ launcher if profile/PATH discovery fails")
    inf.add_argument("--json", action="store_true")
    inf.set_defaults(func=cmd_infra)
