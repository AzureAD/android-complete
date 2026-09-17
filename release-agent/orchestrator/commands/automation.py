"""Hash-bound, owner-claimed automation provisioning and retirement."""
from __future__ import annotations
import json as _json

from orchestrator.registry import AutomationRegistry, kind_of, stable_key, provider_spec, _intent_hash
from orchestrator import automations as auto_plan
from orchestrator import cli_common as C


def _json_file(path):
    with open(path, "rb") as fh:
        data = fh.read()
    encoding = (
        "utf-8-sig"
        if data.startswith(b"\xef\xbb\xbf")
        else "utf-16"
        if data.startswith((b"\xff\xfe", b"\xfe\xff"))
        else "utf-8"
    )
    return _json.loads(data.decode(encoding))


def _cleanup_args(value) -> str:
    rules = value if isinstance(value, list) else [value]
    return " ".join(f'--cleanup-when "{rule}"' for rule in rules if rule)


def _observations(args):
    try:
        if getattr(args, "observed_file", None):
            rows = _json_file(args.observed_file)
        elif getattr(args, "observed_json", None):
            rows = _json.loads(args.observed_json)
        else:
            raise ValueError
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(
            "A complete observation envelope is required via --observed-file "
            "or --observed-json"
        ) from exc
    return rows


def _spec(args):
    try:
        if args.spec_file:
            value = _json_file(args.spec_file)
        elif args.spec_json:
            value = _json.loads(args.spec_json)
        else:
            raise ValueError("An explicit complete --spec-file or --spec-json is required")
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot read complete provider spec; supply --spec-file or --spec-json") from exc
    return provider_spec(value)


def _canonical_policy(args, reg, spec, *, preparing=False):
    """Check new known-worker creation only; never block an owning late receipt."""
    if args.shared:
        return
    from orchestrator.revision import assert_current
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    assert_current(orch)
    key = stable_key(args.release, args.slug)
    existing = reg.get(key=key)
    if existing and (preparing or existing["status"] != "prepared"):
        return
    config = args.config
    definition = next((d for d in auto_plan.load_defs(config) if d["slug"] == args.slug), None)
    if not definition:
        return  # Custom workers still require the entire explicit provider spec.
    if definition.get("on_demand") and args.on_demand != args.slug:
        raise ValueError("On-demand workers require --on-demand <slug>; never provision at startup")
    st = C.load_state(args.runs_root, args.release)
    if not definition.get("every"):
        confirmed = (st.readiness_items.get("ccd_confirmed") or {}).get("status") == "pass"
        if st.ccd_conflict or not confirmed:
            raise ValueError("CCD workers require owner-confirmed CCD with no conflict")
    from orchestrator.engine import Orchestrator
    selection = Orchestrator(config, st, mocks={}).scheduling()
    if selection.status == "cancelled" or selection.frontier is None:
        raise ValueError("Cannot provision workers for a terminal release")
    planned = auto_plan.plan(config, args.release, st.ccd, owner_timezone=st.timezone)
    desired = next(a for a in planned["automations"] if a["slug"] == args.slug)
    if planned["problems"] or desired["problems"]:
        raise ValueError("; ".join(planned["problems"] + desired["problems"]))
    if desired["provider_spec"] != spec:
        raise ValueError("Known worker spec differs from the complete canonical plan")
    if preparing:
        registration = desired["registration"]
        rules = lambda v: sorted(v if isinstance(v, list) else [v])
        if (args.name != registration["name"] or args.schedule != registration["schedule"]
                or args.purpose != registration["purpose"] or args.step != registration["steps"]
                or (args.kind and args.kind != registration["kind"])
                or rules(args.cleanup_when) != rules(registration["cleanup_when"])):
            raise ValueError("Known worker metadata differs from canonical registration")


def cmd_automation(args):
    try:
        return _dispatch_automation(args)
    except (ValueError, OSError) as exc:
        error = {"error": str(exc), "permission_to_create": False, "permission_to_delete": False}
        print(_json.dumps(error) if args.json else str(exc))
        return 1


def _dispatch_automation(args):
    """Track Scout automations the orchestrator provisions, so they can be torn
    down at release close. This only records ids + step linkage — the skill does the
    actual Scout create/delete via m_create_automation / m_delete_automation."""
    reg = AutomationRegistry(args.runs_root, getattr(args, "release", None))
    if args.action == "plan":
        return _cmd_plan(args)
    if args.action == "sync":
        return _cmd_sync(args)
    if args.action == "cleanup":
        return _cmd_cleanup(args)
    if args.action == "prepare":
        if not (args.name and args.slug and args.cleanup_when):
            print("prepare needs --name, --slug, and --cleanup-when.")
            return 1
        try:
            spec = _spec(args)
            _canonical_policy(args, reg, spec, preparing=True)
            entry = reg.prepare(
                args.name,
                release=args.release,
                shared=args.shared,
                purpose=args.purpose or "",
                steps=args.step or [],
                kind=args.kind,
                schedule=args.schedule,
                slug=args.slug,
                cleanup_when=args.cleanup_when,
                spec=spec,
            )
        except ValueError as exc:
            print(f"prepare error: {exc}")
            return 1
        print(_json.dumps(entry, indent=2) if args.json else
              f"Prepared automation intent {entry['key']}.")
        return 0
    if args.action == "reconcile-create":
        try:
            key = stable_key(args.release, args.slug, args.shared)
            spec = _spec(args)
            if args.claim:
                _canonical_policy(args, reg, spec)
            result = reg.reconcile_create(
                key,
                _observations(args),
                spec=spec,
                executor=args.executor,
                claim=args.claim,
            )
        except ValueError as exc:
            print(_json.dumps({"error": str(exc), "permission_to_create": False})
                  if args.json else str(exc))
            return 1
        print(_json.dumps(result, indent=2) if args.json else str(result))
        return 0 if result.get("status") != "blocked" else 1
    if args.action == "create-result":
        try:
            entry = reg.create_result(
                stable_key(args.release, args.slug, args.shared),
                args.attempt_id,
                args.outcome,
                args.evidence,
                automation_id=args.id,
                spec=_spec(args),
            )
        except ValueError as exc:
            print(_json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 1
        print(_json.dumps(entry, indent=2) if args.json else
              f"Automation create result recorded: {entry.get('status')}.")
        return 0
    if args.action in ("confirm-absent", "abandon-prepared"):
        if not args.confirm_absent:
            print("Provider absence must be explicitly confirmed with --confirm-absent.")
            return 1
        try:
            key = stable_key(args.release, args.slug, args.shared)
            if args.action == "abandon-prepared":
                def terminal(release):
                    from orchestrator.engine import Orchestrator
                    st = C.load_state(args.runs_root, release)
                    selection = Orchestrator(args.config, st, mocks={}).scheduling()
                    return selection.status == "cancelled" or selection.frontier is None
                entry = reg.abandon_prepared(
                    key, _observations(args), args.reason,
                    owner_confirmed=args.confirm_absent, terminal_check=terminal,
                )
            else:
                entry = reg.confirm_absent(
                    key, _observations(args), args.reason,
                    owner_confirmed=args.confirm_absent, no_inflight=args.confirm_no_inflight,
                )
        except ValueError as exc:
            print(_json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 1
        print(_json.dumps(entry, indent=2) if args.json else
              f"Automation absence confirmed: {entry['key']}.")
        return 0
    if args.action == "claim-delete":
        try:
            result = reg.claim_delete(args.id, args.executor)
        except ValueError as exc:
            print(_json.dumps({"error": str(exc), "permission_to_delete": False})
                  if args.json else str(exc))
            return 1
        print(_json.dumps(result, indent=2) if args.json else str(result))
        return 0
    if args.action == "delete-result":
        try:
            result = reg.delete_result(
                args.id, args.attempt_id, args.outcome, args.evidence)
        except ValueError as exc:
            print(_json.dumps({"error": str(exc)}) if args.json else str(exc))
            return 1
        print(_json.dumps(result, indent=2) if args.json else str(result))
        return 0
    if args.action == "register":
        print("Direct register is disabled; use complete-spec prepare/reconcile-create/create-result.")
        return 1
    if args.action == "deregister":
        if not args.id:
            print("deregister needs --id.")
            return 1
        print("Direct deregistration is disabled; use claim-delete and delete-result.")
        return 1
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
        print(f"  {e.get('id') or '-'}  [{e['status']}] [{k}] [{where}]  "
              f"{e['name']}  — {e.get('purpose','')}{drives}")
    return 0


def _cmd_plan(args):
    """Emit the concrete per-release automations to provision (from
    config/automations.yaml + the release CCD), each with the exact steps it drives.
    The skill must use complete-spec prepare/reconcile/owning-result, never register."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    result = auto_plan.plan(config_path, args.release, st.ccd, owner_timezone=st.timezone)
    wanted = getattr(args, "on_demand", None)
    if wanted:
        result["automations"] = [
            a for a in result["automations"] if a["on_demand"] and a["slug"] == wanted]
        if not result["automations"]:
            result["problems"].append(f"no on-demand automation named '{wanted}'")
    else:
        result["automations"] = [a for a in result["automations"] if not a["on_demand"]]
        if getattr(args, "slug", None):
            result["automations"] = [a for a in result["automations"] if a["slug"] == args.slug]
            if not result["automations"]:
                result["problems"].append("Unknown startup slug; on-demand workers require --on-demand")
    for entry in result["automations"]:
        result["problems"].extend(f"{entry['slug']}: {p}" for p in entry["problems"])
        if entry["one_shot"] and (
            st.ccd_conflict or (st.readiness_items.get("ccd_confirmed") or {}).get("status") != "pass"
        ):
            result["problems"].append(f"{entry['slug']}: CCD must be owner-confirmed without conflict")
    if args.json:
        print(_json.dumps(result, indent=2))
        return 1 if result["problems"] else 0
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
        print(f"    prepare: automation prepare --name \"{a['name']}\" "
              f"--release {args.release} --purpose \"{a['purpose']}\" "
              f"--slug \"{a['slug']}\" --schedule \"{sched}\" "
              f"{_cleanup_args(a['cleanup_when'])} --spec-file <temporary exact provider_spec.json> "
              + " ".join(f"--step {s}" for s in a["steps"]))
    return 0 if not result["problems"] else 1


def _cmd_sync(args):
    """Read-only drift report. In-place updates have no safe durable protocol."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    ccd = getattr(st, "ccd", None)
    reg = AutomationRegistry(args.runs_root, getattr(args, "release", None))
    registered = [
        entry for entry in reg.list(release=args.release)
        if entry["status"] == "active"
    ]
    plan = auto_plan.plan(config_path, args.release, ccd, owner_timezone=st.timezone)
    desired_by_slug = {a["slug"]: a for a in plan["automations"]}

    updates = []
    for e in registered:
        spec = desired_by_slug.get(e.get("slug"))   # matched by slug (stable, unambiguous)
        if spec is None:
            continue                       # no matching desired spec — skip
        desired = spec.get("schedule")
        current = e.get("schedule")
        desired_entry = {**e, **spec["registration"]}
        changed = (not spec["provider_spec"]
                   or _intent_hash(desired_entry, spec["provider_spec"]) != e["intent_hash"])
        updates.append({
            "id": e["id"], "name": e["name"], "slug": e.get("slug") or spec["slug"],
            "steps": e.get("steps") or [],
            "cleanup_when": spec.get("cleanup_when"),
            "current_schedule": current, "desired_schedule": desired,
            "changed": changed, "permission_to_update": False,
            "action": "owner-reviewed delete/recreate" if changed else "none",
            "problems": spec["problems"],
        })
    result = {"release": args.release, "ccd": ccd, "problems": plan["problems"],
              "updates": updates, "permission_to_update": False}

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
            print("    In-place update disabled. Owner must review claimed deletion and fresh provisioning.")
    return 0


def _cmd_cleanup(args):
    """Return registered automations whose declared objective is finished."""
    st = C.load_state(args.runs_root, args.release)
    entries = AutomationRegistry(args.runs_root, args.release).list(release=args.release)
    unresolved = [
        entry for entry in entries
        if entry["status"] != "active"
    ]
    result = auto_plan.cleanup_plan(
        st,
        [entry for entry in entries if entry["status"] == "active"],
        getattr(args, "config", None) or C.DEFAULT_CONFIG,
    )
    result["problems"].extend(
        f"{entry['key']}: lifecycle is {entry['status']}; reconcile before cleanup"
        for entry in unresolved
    )
    if args.json:
        print(_json.dumps(result, indent=2))
    else:
        for item in result["removals"]:
            print(f"DELETE {item['id']} — {item['name']} ({item['reason']})")
        for problem in result["problems"]:
            print(f"PROBLEM: {problem}")
        if not result["removals"] and not result["problems"]:
            print("No automations are ready for cleanup.")
    return 1 if result["problems"] else 0


def register(sub):
    au = sub.add_parser("automation", help="Track provisioned automations and reconcile lifecycle cleanup")
    au.add_argument("action", choices=[
        "plan", "prepare", "reconcile-create", "create-result",
        "confirm-absent", "abandon-prepared", "claim-delete", "delete-result",
        "register", "list", "deregister", "sync", "cleanup",
    ])
    au.add_argument("--id", default=None, help="Scout automation id")
    au.add_argument("--name", default="", help="Reviewed automation name (for prepare)")
    au.add_argument("--release", default=None, help="Release scope (omit + --shared for machine-wide)")
    au.add_argument("--shared", action="store_true", help="Mark as shared/persistent (not torn down per release)")
    au.add_argument("--scope", default=None, choices=["shared", "release"], help="Filter list by scope")
    au.add_argument("--kind", default=None, choices=["release-level", "step-driving"],
                    help="For prepare: override the auto-derived kind. For list: filter by kind.")
    au.add_argument("--step", action="append", default=[],
                    help="For prepare: a '<phase>.<step>' id this automation drives (repeatable)")
    au.add_argument("--step-filter", default=None, dest="step_filter",
                    help="For list: show only automations that drive this '<phase>.<step>' id")
    au.add_argument("--purpose", default="", help="Short description")
    au.add_argument("--schedule", default=None,
                    help="For prepare: exact reviewed provider schedule (also used by drift-only sync)")
    au.add_argument("--slug", default=None,
                    help="For prepare/plan: the stable slug from automations.yaml, or an explicit custom slug")
    au.add_argument("--cleanup-when", action="append", default=None,
                    help="Required for prepare: steps_done|steps_settled|phase_done:<id>|"
                         "step_flag:<phase.step>:<key>|release_done|manual; repeat for OR")
    au.add_argument("--on-demand", default=None, metavar="SLUG",
                    help="Select or explicitly authorize the named on-demand worker")
    au.add_argument("--json", action="store_true")
    observations = au.add_mutually_exclusive_group()
    observations.add_argument(
        "--observed-file", default=None,
        help="Read fresh complete automation observations from a temporary JSON file")
    observations.add_argument(
        "--observed-json", default=None,
        help="Inline fresh complete automation observations (small/manual inputs only)")
    specs = au.add_mutually_exclusive_group()
    specs.add_argument(
        "--spec-file", default=None,
        help="Read exact reviewed provider kwargs from a temporary JSON file (preferred)")
    specs.add_argument(
        "--spec-json", default=None,
        help="Inline complete provider kwargs (small/manual inputs only)")
    au.add_argument("--executor", default=None,
                    help="Worker/session claiming create or delete")
    au.add_argument("--claim", action="store_true",
                    help="Persist a create claim and return permission_to_create")
    au.add_argument("--attempt-id", default=None,
                    help="Owning create/delete attempt id")
    au.add_argument("--outcome", choices=[
        "created", "not_created", "uncertain", "deleted", "not_deleted"])
    au.add_argument("--evidence", default=None)
    au.add_argument("--reason", default=None)
    au.add_argument("--confirm-absent", action="store_true")
    au.add_argument("--confirm-no-inflight", action="store_true",
                    help="Owner verified original runner AND provider operation have terminated")
    au.set_defaults(func=cmd_automation)
