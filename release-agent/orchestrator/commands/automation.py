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
    ensuring = getattr(args, "ensure", False)
    if args.shared:
        if ensuring:
            raise ValueError("Ensure cannot manage shared/manual automations")
        return
    from orchestrator.revision import assert_current
    _, orch = C.load_orch(args.runs_root, args.release, args.config)
    assert_current(orch)
    if ensuring:
        errors = [issue.message for issue in orch.invariant_violations() if issue.severity == "error"]
        if errors:
            raise ValueError("Invalid release state: " + "; ".join(errors))
    key = stable_key(args.release, args.slug)
    existing = reg.get(key=key)
    if existing and (preparing or existing["status"] != "prepared") and not ensuring:
        return
    config = args.config
    definition = next((d for d in auto_plan.load_defs(config) if d["slug"] == args.slug), None)
    if not definition:
        if ensuring:
            raise ValueError("Ensure requires a declared canonical worker")
        return  # Custom workers still require the entire explicit provider spec.
    if definition.get("on_demand") and args.on_demand != args.slug:
        raise ValueError("On-demand workers require --on-demand <slug>; never provision at startup")
    st = C.load_state(args.runs_root, args.release)
    if not definition.get("every") and not definition.get("daily_at"):
        confirmed = (st.readiness_items.get("ccd_confirmed") or {}).get("status") == "pass"
        if st.ccd_conflict or not confirmed:
            raise ValueError("CCD workers require owner-confirmed CCD with no conflict")
    from orchestrator.engine import Orchestrator
    selection = Orchestrator(config, st, mocks={}).scheduling()
    if selection.suspension in ("halted", "denied"):
        raise ValueError("Cannot create a worker while the release has an explicit hold")
    if selection.status == "cancelled" or selection.frontier is None:
        raise ValueError("Cannot provision workers for a terminal release")
    planned = auto_plan.plan(config, args.release, st.ccd, owner_timezone=st.timezone)
    desired = next(a for a in planned["automations"] if a["slug"] == args.slug)
    if planned["problems"] or desired["problems"]:
        raise ValueError("; ".join(planned["problems"] + desired["problems"]))
    if desired["provider_spec"] != spec:
        raise ValueError("Known worker spec differs from the complete canonical plan")
    if ensuring:
        reason = auto_plan.ensure_reason(st, desired, config, selection)
        if reason:
            raise ValueError(reason)
    if preparing:
        registration = desired["registration"]
        rules = lambda v: sorted(v if isinstance(v, list) else [v])
        if (args.name != registration["name"] or args.schedule != registration["schedule"]
                or args.purpose != registration["purpose"] or args.step != registration["steps"]
                or (args.kind and args.kind != registration["kind"])
                or rules(args.cleanup_when) != rules(registration["cleanup_when"])):
            raise ValueError("Known worker metadata differs from canonical registration")
    return desired["registration"]


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
    if getattr(args, "ensure", False) and args.action != "reconcile-create":
        raise ValueError("--ensure belongs only to reconcile-create")
    reg = AutomationRegistry(args.runs_root, getattr(args, "release", None))
    if args.action == "plan":
        return _cmd_plan(args)
    if args.action == "sync":
        return _cmd_sync(args)
    if args.action == "obligations":
        return _cmd_obligations(args)
    if args.action == "cleanup":
        return _cmd_cleanup(args)
    if args.action == "record-setup":
        return _cmd_record_setup(args)
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
            registration = None
            if args.claim or getattr(args, "ensure", False):
                registration = _canonical_policy(args, reg, spec)
            result = reg.reconcile_create(
                key,
                _observations(args),
                spec=spec,
                executor=args.executor,
                claim=args.claim,
                ensure=getattr(args, "ensure", False),
                registration=registration,
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
    elif getattr(args, "slug", None):
        result["automations"] = [a for a in result["automations"] if a["slug"] == args.slug]
        if not result["automations"]:
            result["problems"].append(f"Unknown automation slug '{args.slug}'")
    else:
        result["automations"] = [a for a in result["automations"] if not a["on_demand"]]
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
        sched = a.get("schedule") or "(schedule unknown)"
        mode_note = (
            f"(one-shot; fires {a.get('fire_at')} on CCD {a.get('ccd_date')})"
            if a.get("one_shot")
            else "(optional recurring accelerator)"
        )
        print(f"• {a['name']}")
        print(f"    slug:     {a['slug']}")
        print(f"    schedule: {sched}   {mode_note}")
        print(f"    drives:   {', '.join(a['steps']) or '(release-wide)'}")
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


def _cmd_obligations(args):
    """Report optional background accelerators and any unresolved registry recoveries."""
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    entries = AutomationRegistry(args.runs_root, args.release).list(
        release=args.release)
    result = auto_plan.provisioning_obligations(st, entries, config_path)
    if args.json:
        print(_json.dumps(result, indent=2))
    else:
        for spec in result.get("optional", []):
            print(f"OPTIONAL {spec['slug']} ({spec.get('schedule')}) — {spec['purpose']}")
        for entry in result["recoveries"]:
            print(f"RECOVER {entry['slug']} — lifecycle is {entry['status']}")
        for problem in result["problems"]:
            print(f"PROBLEM: {problem}")
        if not result.get("optional") and not result["recoveries"] and not result["problems"]:
            print("No optional accelerators pending; no unresolved recoveries.")
    return 1 if result["problems"] else 0


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


def morning_drain_setup_hint(runs_root: str, release: str, config_path: str, state) -> dict | None:
    """Return one-time setup metadata for `morning-step-drain` when Readiness is signed and
    no registry entry exists yet; returns None once recorded (even if later disabled/deleted)."""
    if not release or not runs_root:
        return None
    reg = AutomationRegistry(runs_root, release)
    if reg.get(key=stable_key(release, "morning-step-drain")) is not None:
        return None
    planned = auto_plan.plan(config_path, release, state.ccd, owner_timezone=state.timezone)
    worker = next((a for a in planned["automations"] if a["slug"] == "morning-step-drain"), None)
    if not worker or worker.get("problems") or not worker.get("provider_spec"):
        return None
    return {
        "slug": "morning-step-drain",
        "name": worker["name"],
        "schedule": worker["schedule"],
        "optional": True,
        "provider_spec": worker["provider_spec"],
        "plan_command": f"automation plan --release {release} --slug morning-step-drain --json",
        "record_command": f"automation record-setup --release {release} --slug morning-step-drain --id <created-id> --json",
        "skip_command": f"automation record-setup --release {release} --slug morning-step-drain --skipped \"<reason>\" --json",
        "instruction": (
            "Integrated one-time setup: call `m_list_automations` — if an automation named "
            f"`{worker['name']}` already exists, record its ID with `record_command`; otherwise "
            "call `m_create_automation` once with `provider_spec` and record the returned ID with "
            "`record_command`. If `m_create_automation` is unavailable or fails, run `skip_command` "
            "and continue immediately into Phase 0 (non-blocking)."
        ),
    }


def ccd_watcher_setup_hint(entries: list[dict] | None, release: str, config_path: str, state, selection, now=None) -> dict | None:
    """Return one-time setup metadata for `ccd-localization-watcher` once Phase 1 (`ccd`) is reached
    and before CCD 10:00 PM; returns None once recorded in the registry."""
    if not release or entries is None:
        return None
    if selection.frontier is None or selection.frontier.id != "ccd" or state.cancellation or state.halt:
        return None
    target_key = stable_key(release, "ccd-localization-watcher")
    if any(item.get("key") == target_key for item in entries):
        return None
    planned = auto_plan.plan(config_path, release, state.ccd, owner_timezone=state.timezone, now=now)
    worker = next((a for a in planned["automations"] if a["slug"] == "ccd-localization-watcher"), None)
    if not worker or worker.get("problems") or not worker.get("provider_spec"):
        return None
    # Do not offer setup if CCD 10:00 PM has already passed or Phase 1 is complete.
    probe_entry = {**worker["registration"], "id": "probe", "scope": "release", "status": "active"}
    cleanup = auto_plan.cleanup_plan(state, [probe_entry], config_path, now=now)
    if cleanup["removals"] or cleanup["problems"]:
        return None
    return {
        "slug": "ccd-localization-watcher",
        "name": worker["name"],
        "schedule": worker["schedule"],
        "optional": True,
        "plan_command": f"automation plan --release {release} --slug ccd-localization-watcher --json",
        "record_command": f"automation record-setup --release {release} --slug ccd-localization-watcher --id <created-id> --json",
        "skip_command": f"automation record-setup --release {release} --slug ccd-localization-watcher --skipped \"<reason>\" --json",
        "instruction": (
            "SILENT ONE-TIME PHASE 1 SETUP (`requires_approval: false`): Run `command` "
            f"(`automation plan --release {release} --slug ccd-localization-watcher --json`) to read "
            "`automations[0].provider_spec`, call `m_list_automations` (reuse ID if "
            f"`{worker['name']}` already exists, else call `m_create_automation` once with that exact "
            "`provider_spec`), then record via "
            f"`automation record-setup --release {release} --slug ccd-localization-watcher --id <created-id> --json` "
            f"(or `--skipped \"<reason>\"` if `m_create_automation` is unavailable — non-blocking!). "
            "Do NOT call `m_ask_user`!"
        ),
    }


def _cmd_record_setup(args):
    """Record the one-time release-start setup outcome for a canonical optional accelerator."""
    if not args.release or not args.slug:
        raise ValueError("record-setup requires --release and --slug")
    if bool(args.id) == bool(args.skipped):
        raise ValueError("record-setup requires exactly one of --id <provider-id> or --skipped <reason>")
    config_path = getattr(args, "config", None) or C.DEFAULT_CONFIG
    st = C.load_state(args.runs_root, args.release)
    planned = auto_plan.plan(config_path, args.release, st.ccd, owner_timezone=st.timezone)
    worker = next((a for a in planned["automations"] if a["slug"] == args.slug), None)
    if worker is None:
        raise ValueError(f"Unknown canonical automation slug: {args.slug!r}")
    if planned["problems"] or worker["problems"] or not worker.get("provider_spec"):
        raise ValueError("; ".join(planned["problems"] + worker["problems"]) or "Invalid provider spec")
    reg = AutomationRegistry(args.runs_root, args.release)
    out = reg.record_setup(
        release=args.release,
        slug=args.slug,
        registration=worker["registration"],
        spec=worker["provider_spec"],
        automation_id=args.id,
        skipped_reason=args.skipped,
        executor=args.executor or "release-setup",
    )
    C.elog(args.runs_root, args.release).log(
        "automation_setup_recorded",
        slug=args.slug,
        status=out["entry"]["status"],
        automation_id=out["entry"].get("id"),
    )
    if args.json:
        print(_json.dumps(out, indent=2))
    else:
        entry = out["entry"]
        if entry["status"] == "active":
            print(f"Recorded {entry['name']} ({entry['id']}) — runs {entry['schedule']}.")
        else:
            print(f"Recorded non-blocking setup skip for {entry['name']}; workflow continues normally.")
    return 0


def register(sub):
    au = sub.add_parser("automation", help="Track provisioned automations and reconcile lifecycle cleanup")
    au.add_argument("action", choices=[
        "plan", "prepare", "reconcile-create", "create-result", "record-setup",
        "confirm-absent", "abandon-prepared", "claim-delete", "delete-result",
        "register", "list", "deregister", "sync", "obligations", "cleanup",
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
    au.add_argument("--ensure", action="store_true",
                    help="Reconcile a currently desired canonical worker; safely replace proven-absent settled generations")
    au.add_argument("--attempt-id", default=None,
                    help="Owning create/delete attempt id")
    au.add_argument("--outcome", choices=[
        "created", "not_created", "uncertain", "deleted", "not_deleted"])
    au.add_argument("--evidence", default=None)
    au.add_argument("--reason", default=None)
    au.add_argument("--skipped", default=None,
                    help="For record-setup: non-blocking reason when optional accelerator setup is skipped")
    au.add_argument("--confirm-absent", action="store_true")
    au.add_argument("--confirm-no-inflight", action="store_true",
                    help="Owner verified original runner AND provider operation have terminated")
    au.set_defaults(func=cmd_automation)
