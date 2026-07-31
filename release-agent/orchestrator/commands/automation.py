"""Automation registry command: register / list / deregister provisioned Scout
automations so they can be torn down cleanly at release close."""
from __future__ import annotations
import json as _json

from orchestrator.registry import AutomationRegistry


def cmd_automation(args):
    """Track Scout automations the orchestrator provisions, so they can be torn
    down at release close. This only records ids — the skill does the actual
    Scout create/delete via m_create_automation / m_delete_automation."""
    reg = AutomationRegistry(args.runs_root)
    if args.action == "register":
        if not (args.id and args.name):
            print("register needs --id and --name.")
            return 1
        e = reg.register(args.id, args.name, release=args.release,
                         shared=args.shared, purpose=args.purpose or "")
        where = "shared" if e["scope"] == "shared" else f"release {e['release']}"
        print(f"Registered automation {e['id']} ({where}): {e['name']}")
        return 0
    if args.action == "deregister":
        if not args.id:
            print("deregister needs --id.")
            return 1
        print("Deregistered." if reg.deregister(args.id) else "No such automation id in registry.")
        return 0
    # list
    items = reg.list(release=args.release, scope=(args.scope or None))
    if args.json:
        print(_json.dumps(items, indent=2))
        return 0
    if not items:
        print("No automations registered." if args.release is None
              else f"No automations registered for release {args.release}.")
        return 0
    for e in items:
        where = "shared" if e.get("scope") == "shared" else (e.get("release") or "?")
        print(f"  {e['id']}  [{where}]  {e['name']}  — {e.get('purpose','')}")
    return 0


def register(sub):
    au = sub.add_parser("automation", help="Track provisioned automations (register/list/deregister) for teardown")
    au.add_argument("action", choices=["register", "list", "deregister"])
    au.add_argument("--id", default=None, help="Scout automation id")
    au.add_argument("--name", default="", help="Automation name (for register)")
    au.add_argument("--release", default=None, help="Release scope (omit + --shared for machine-wide)")
    au.add_argument("--shared", action="store_true", help="Mark as shared/persistent (not torn down per release)")
    au.add_argument("--scope", default=None, choices=["shared", "release"], help="Filter list by scope")
    au.add_argument("--purpose", default="", help="Short description")
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_automation)
