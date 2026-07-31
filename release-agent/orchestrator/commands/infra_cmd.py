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
    report = infra.run(C.REQUIREMENTS_CONFIG, register=not getattr(args, "no_register", False))
    if getattr(args, "json", False):
        print(_json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    print("Infrastructure preflight")
    if not report.get("scout_present", True):
        print("  ⛔ Microsoft Scout not detected (~/.scout missing).")
        scout_req = next((r for r in report["requirements"] if r["id"] == "scout"), None)
        if scout_req and scout_req.get("install"):
            print(f"      install: {scout_req['install']}")
        print("      Install Scout FIRST, then re-run — MCP servers can't be registered without it.")
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
    if report["restart_needed"]:
        print("\n  ⚠ RESTART Scout to load newly-registered MCP server(s).")
    if not report["ok"]:
        print("\n  Some infrastructure is missing — resolve the items above, then re-run.")
    else:
        print("\n  Infrastructure OK.")
    return 0 if report["ok"] else 1


def register(sub):
    inf = sub.add_parser("infra", help="Infrastructure preflight: check CLIs + register/verify MCP servers in Scout")
    inf.add_argument("--no-register", action="store_true", help="Only report; don't register missing MCP servers")
    inf.add_argument("--json", action="store_true")
    inf.set_defaults(func=cmd_infra)
