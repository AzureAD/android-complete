"""Automation registry command: register / list / deregister / plan provisioned
Scout automations so they can be created with step linkage and torn down cleanly at
release close."""
from __future__ import annotations
import json as _json

from orchestrator.registry import AutomationRegistry, kind_of
from orchestrator import automations as auto_plan
from orchestrator import cli_common as C


def cmd_automation(args):
    """Track Scout automations the orchestrator provisions, so they can be torn
    down at release close. This only records ids + step linkage — the skill does the
    actual Scout create/delete via m_create_automation / m_delete_automation."""
    reg = AutomationRegistry(args.runs_root, getattr(args, "release", None))
    if args.action == "plan":
        return _cmd_plan(args)
    if args.action == "sync":
        return _cmd_sync(args)
    if args.action == "register":
        if not (args.id and args.name):
            print("register needs --id and --name.")
            return 1
        try:
            e = reg.register(args.id, args.name, release=args.release,
                             shared=args.shared, purpose=args.purpose or "",
                             steps=getattr(args, "step", None) or [],
                             kind=getattr(args, "kind", None) or None,
                             schedule=getattr(args, "schedule", None) or None,
                             slug=getattr(args, "slug", None) or None)
        except ValueError as ex:
            print(f"register error: {ex}")
            return 1
        where = "shared" if e["scope"] == "shared" else f"release {e['release']}"
        drives = f" — drives {', '.join(e['steps'])}" if e.get("steps") else " — owns no steps"
        print(f"Registered automation {e['id']} [{e['kind']}] ({where}): {e['name']}{drives}")
        return 0
    if args.action == "deregister":
        if not args.id:
            print("deregister needs --id.")
            return 1
        print("Deregistered." if reg.deregister(args.id) else "No such automation id in registry.")
        return 0
    # list
    items = reg.list(release=args.release, scope=(args.scope or None),
                     step=(getattr(args, "step_filter", None) or None),
                     kind=(getattr(args, "kind", None) or None))
    if args.json:
        print(_json.dumps(items, indent=2))
        return 0
    if not items:
        print("No automations registered." if args.release is None
              else f"No automations registered for release {args.release}.")
        return 0
    for e in items:
        where = "shared" if e.get("scope") == "shared" else (e.get("release") or "?")
        k = kind_of(e)
        drives = (f"  drives: {', '.join(e.get('steps') or [])}" if e.get("steps")
                  else "  (release-level — no steps)")
        print(f"  {e['id']}  [{k}] [{where}]  {e['name']}  — {e.get('purpose','')}{drives}")
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
              f"--slug \"{a['slug']}\" --schedule \"{sched}\" "
              + " ".join(f"--step {s}" for s in a["steps"]))
    return 0 if not result["problems"] else 1


def _cmd_sync(args):
    """Detect step-driving automations whose SCHEDULE is stale vs the current CCD, and
    emit what to change so the skill can re-apply it via m_update_automation. The CCD-day
    automations are cron-pinned to the CCD; if the CCD moves (set-ccd) the live schedule
    must move with it. Matches each REGISTERED automation to its desired schedule by the
    set of steps it drives. Emits {release, ccd, updates:[{id, name, steps,
    current_schedule, desired_schedule, changed}], problems}.

    The skill: for every entry with changed=true → m_update_automation(id,
    schedule=desired_schedule), then `automation register` it again WITH --schedule
    <desired> so the registry records the newly-applied schedule."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    ccd = getattr(st, "ccd", None)
    reg = AutomationRegistry(args.runs_root, getattr(args, "release", None))
    registered = reg.list(release=args.release, kind="step-driving")
    plan = auto_plan.plan(config_path, args.release, ccd)
    desired_by_slug = {a["slug"]: a for a in plan["automations"]}

    updates = []
    for e in registered:
        spec = desired_by_slug.get(e.get("slug"))   # matched by slug (stable, unambiguous)
        if spec is None:
            continue                       # no matching desired spec — skip
        desired = spec.get("schedule")
        current = e.get("schedule")
        updates.append({
            "id": e["id"], "name": e["name"], "slug": e.get("slug") or spec["slug"],
            "steps": e.get("steps") or [],
            "current_schedule": current, "desired_schedule": desired,
            "changed": bool(desired) and desired != current,
        })
    result = {"release": args.release, "ccd": ccd, "problems": plan["problems"],
              "updates": updates}

    if args.json:
        print(_json.dumps(result, indent=2))
        return 0
    if not ccd:
        print(f"Release {args.release} has no CCD — set it before syncing automations.")
        return 1
    changed = [u for u in updates if u["changed"]]
    if not registered:
        print(f"No step-driving automations registered for release {args.release}.")
    elif not changed:
        print(f"All {len(updates)} CCD automation(s) already in sync with CCD {ccd}.")
    else:
        print(f"{len(changed)} automation(s) need a schedule update for CCD {ccd}:")
        for u in changed:
            print(f"  • {u['name']} ({u['id']}): {u['current_schedule']} → {u['desired_schedule']}")
            print(f"    m_update_automation(id={u['id']}, schedule=\"{u['desired_schedule']}\"), "
                  f"then re-register with --schedule \"{u['desired_schedule']}\"")
    return 0


def register(sub):
    au = sub.add_parser("automation", help="Track provisioned automations (plan/register/list/deregister/sync) for teardown + CCD re-pin")
    au.add_argument("action", choices=["plan", "register", "list", "deregister", "sync"])
    au.add_argument("--id", default=None, help="Scout automation id")
    au.add_argument("--name", default="", help="Automation name (for register)")
    au.add_argument("--release", default=None, help="Release scope (omit + --shared for machine-wide)")
    au.add_argument("--shared", action="store_true", help="Mark as shared/persistent (not torn down per release)")
    au.add_argument("--scope", default=None, choices=["shared", "release"], help="Filter list by scope")
    au.add_argument("--kind", default=None, choices=["release-level", "step-driving"],
                    help="For register: override the auto-derived kind. For list: filter by kind.")
    au.add_argument("--step", action="append", default=[],
                    help="For register: a '<phase>.<step>' id this automation drives (repeatable)")
    au.add_argument("--step-filter", default=None, dest="step_filter",
                    help="For list: show only automations that drive this '<phase>.<step>' id")
    au.add_argument("--purpose", default="", help="Short description")
    au.add_argument("--schedule", default=None,
                    help="For register: the Scout schedule the automation was created with (stored so `sync` can detect CCD drift)")
    au.add_argument("--slug", default=None,
                    help="For register: the stable slug from automations.yaml (sync's unambiguous match key)")
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=cmd_automation)
