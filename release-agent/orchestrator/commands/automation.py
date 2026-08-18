"""Automation registry command: register / list / deregister / plan provisioned
Scout automations so they can be created with step linkage and torn down cleanly at
release close."""
from __future__ import annotations
import json as _json

from orchestrator.registry import AutomationRegistry
from orchestrator import automations as auto_plan
from orchestrator import cli_common as C


def cmd_automation(args):
    """Track Scout automations the orchestrator provisions, so they can be torn
    down at release close. This only records ids + step linkage — the skill does the
    actual Scout create/delete via m_create_automation / m_delete_automation."""
    reg = AutomationRegistry(args.runs_root)
    if args.action == "plan":
        return _cmd_plan(args)
    if args.action == "register":
        if not (args.id and args.name):
            print("register needs --id and --name.")
            return 1
        e = reg.register(args.id, args.name, release=args.release,
                         shared=args.shared, purpose=args.purpose or "",
                         steps=getattr(args, "step", None) or [])
        where = "shared" if e["scope"] == "shared" else f"release {e['release']}"
        drives = f" — drives {', '.join(e['steps'])}" if e.get("steps") else ""
        print(f"Registered automation {e['id']} ({where}): {e['name']}{drives}")
        return 0
    if args.action == "deregister":
        if not args.id:
            print("deregister needs --id.")
            return 1
        print("Deregistered." if reg.deregister(args.id) else "No such automation id in registry.")
        return 0
    # list
    items = reg.list(release=args.release, scope=(args.scope or None),
                     step=(getattr(args, "step_filter", None) or None))
    if args.json:
        print(_json.dumps(items, indent=2))
        return 0
    if not items:
        print("No automations registered." if args.release is None
              else f"No automations registered for release {args.release}.")
        return 0
    for e in items:
        where = "shared" if e.get("scope") == "shared" else (e.get("release") or "?")
        drives = f"  drives: {', '.join(e.get('steps') or [])}" if e.get("steps") else ""
        print(f"  {e['id']}  [{where}]  {e['name']}  — {e.get('purpose','')}{drives}")
    return 0


def _cmd_plan(args):
    """Emit the concrete per-release automations to provision (from
    config/automations.yaml + the release CCD), each with the exact steps it drives.
    The skill creates each via m_create_automation, then `automation register`s it
    with the same --step ids. Fails loudly if the config/step mapping has drifted."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    result = auto_plan.plan(config_path, args.release, getattr(st, "ccd", None))
    if args.json:
        print(_json.dumps(result, indent=2))
        return 0
    if result["problems"]:
        print("⚠ automation mapping problems (fix config/automations.yaml or step fire_at_local):")
        for p in result["problems"]:
            print(f"  - {p}")
        print()
    if not result["ccd"]:
        print(f"Release {args.release} has no CCD yet — set it before provisioning CCD automations.")
    for a in result["automations"]:
        sched = a.get("schedule") or "(no CCD → schedule unknown)"
        print(f"• {a['name']}")
        print(f"    slug:     {a['slug']}")
        print(f"    schedule: {sched}   (one-shot; fires {a.get('fire_at')} on CCD {a.get('ccd_date')})")
        print(f"    drives:   {', '.join(a['steps'])}")
        print(f"    purpose:  {a['purpose']}")
        print(f"    register: automation register --id <scout-id> --name \"{a['name']}\" "
              f"--release {args.release} --purpose \"{a['purpose']}\" "
              + " ".join(f"--step {s}" for s in a["steps"]))
    return 0 if not result["problems"] else 1


def register(sub):
    au = sub.add_parser("automation", help="Track provisioned automations (plan/register/list/deregister) for teardown")
    au.add_argument("action", choices=["plan", "register", "list", "deregister"])
    au.add_argument("--id", default=None, help="Scout automation id")
    au.add_argument("--name", default="", help="Automation name (for register)")
    au.add_argument("--release", default=None, help="Release scope (omit + --shared for machine-wide)")
    au.add_argument("--shared", action="store_true", help="Mark as shared/persistent (not torn down per release)")
    au.add_argument("--scope", default=None, choices=["shared", "release"], help="Filter list by scope")
    au.add_argument("--step", action="append", default=[],
                    help="For register: a '<phase>.<step>' id this automation drives (repeatable)")
    au.add_argument("--step-filter", default=None, dest="step_filter",
                    help="For list: show only automations that drive this '<phase>.<step>' id")
    au.add_argument("--purpose", default="", help="Short description")
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_automation)
