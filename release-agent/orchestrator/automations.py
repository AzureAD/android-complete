"""Per-release automation planning + validation (traceability layer).

`config/automations.yaml` declares WHICH Scout automations a release provisions and
WHICH STEPS each drives. This module turns that data into:

  * plan(release, ccd)  — complete specs for prepare/reconcile/owning create-result
    automation (name, schedule, prompt, steps, fire time). Timing is DERIVED from
    each step module's `fire_at_local`, so the step module is the single source.
  * validate()          — the self-enforcing guardrail: every step that declares a
    `fire_at_local` is owned by EXACTLY ONE automation; every automation's steps
    exist and share ONE fire time. A test runs this so the mapping can't drift.

The engine never calls Scout's automation API — the skill does. This is pure data +
computation (no IO beyond reading the two yaml files).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import yaml

import steps as steps_pkg
from orchestrator import schedule, delivery

_CLEANUP_RULES = {"steps_done", "steps_settled", "release_done", "manual"}


def _cleanup_rules(value) -> list:
    return list(value) if isinstance(value, list) else [value]


def _valid_cleanup_rule(rule) -> bool:
    if not isinstance(rule, str):
        return False
    if rule in _CLEANUP_RULES or rule.startswith(("phase_done:", "step_flag:")):
        return True
    if rule.startswith("ccd_after:"):
        hhmm = rule.split(":", 1)[1]
        try:
            return len(hhmm) == 5 and bool(datetime.strptime(hhmm, "%H:%M"))
        except ValueError:
            return False
    return False


def _parse_ccd_window(value) -> tuple[int, int, int, int] | None:
    """Parse `'HH:MM-HH:MM/<N>h'` -> `(start_hour, end_hour, step_hours, minute)`."""
    if not isinstance(value, str) or "/" not in value or "-" not in value:
        return None
    span, _, step_part = value.strip().partition("/")
    if not step_part.endswith("h") or not step_part[:-1].isdigit():
        return None
    step_hours = int(step_part[:-1])
    start_s, _, end_s = span.partition("-")
    try:
        if len(start_s) != 5 or len(end_s) != 5:
            return None
        t0 = datetime.strptime(start_s, "%H:%M")
        t1 = datetime.strptime(end_s, "%H:%M")
    except ValueError:
        return None
    if step_hours < 1 or t0.minute != t1.minute or t1.hour < t0.hour:
        return None
    return t0.hour, t1.hour, step_hours, t0.minute


def automations_path(config_path: str) -> str:
    """config/automations.yaml sits next to phases.yaml (config_path)."""
    return os.path.join(os.path.dirname(config_path), "automations.yaml")


def _definition_document(config_path: str) -> dict:
    try:
        with open(automations_path(config_path), "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("Cannot read canonical automation definitions") from exc
    if (not isinstance(doc, dict) or doc.get("version") != 2
            or set(doc) != {"version", "provider_defaults", "automations"}
            or not isinstance(doc["automations"], list)
            or not doc["automations"]
            or any(not isinstance(d, dict) or not isinstance(d.get("slug"), str)
                   or not d["slug"].strip() or not isinstance(d.get("steps", []), list)
                   or any(not isinstance(step, str) for step in d.get("steps", []))
                   for d in doc["automations"])):
        raise ValueError("Invalid canonical automation definitions; explicit version-2 data required")
    return doc


def _configured_step_keys(config_path: str) -> list[str]:
    """Ordered '<phase>.<step>' keys from `config_path` (falls back to discovered modules if unreadable)."""
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        keys = []
        for phase in doc.get("phases") or []:
            pid = phase.get("id") if isinstance(phase, dict) else None
            if not isinstance(pid, str):
                continue
            for step in phase.get("steps") or []:
                sid = step.get("id") if isinstance(step, dict) else None
                if isinstance(sid, str):
                    keys.append(f"{pid}.{sid}")
        if keys:
            return keys
    except (OSError, yaml.YAMLError):
        pass
    return list(steps_pkg.discover().keys())


def _module_automations(mod: object) -> tuple[str, ...]:
    raw_multi = getattr(mod, "AUTOMATIONS", None)
    if isinstance(raw_multi, (list, tuple)):
        return tuple(str(s) for s in raw_multi if isinstance(s, str) and s.strip())
    raw_single = getattr(mod, "AUTOMATION", None)
    if isinstance(raw_single, str) and raw_single.strip():
        return (raw_single.strip(),)
    return ()


def load_defs(config_path: str) -> list:
    raw_defs = _definition_document(config_path)["automations"]
    active_keys = _configured_step_keys(config_path)
    by_slug: dict[str, list[str]] = {}
    for key in active_keys:
        phase, _, sid = key.partition(".")
        mod = steps_pkg.get_step(phase, sid)
        if mod is None:
            continue
        for slug in _module_automations(mod):
            by_slug.setdefault(slug, []).append(key)
    resolved = []
    for item in raw_defs:
        entry = dict(item)
        if "steps" not in entry:
            entry["steps"] = list(by_slug.get(entry.get("slug", ""), ()))
        resolved.append(entry)
    return resolved


def _provider_defaults(config_path: str) -> dict:
    defaults = _definition_document(config_path)["provider_defaults"]
    if not isinstance(defaults, dict) or set(defaults) != {
        "model", "enabled", "triggerType", "conditionCheckInterval", "browserHeadless", "teamsNotify",
    }:
        raise ValueError("Canonical provider_defaults require exactly the explicit supported flags")
    return defaults


def phase_label(config_path: str, phase_id: str) -> str:
    """The display name of a phase (from phases.yaml), e.g. 'ccd' -> 'Code Complete Day'.
    Falls back to the raw id when unknown. Used to build a human automation scope."""
    if not phase_id:
        return "Release-wide"
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for p in (doc.get("phases") or []):
            if p.get("id") == phase_id:
                return p.get("name") or phase_id
    except (OSError, yaml.YAMLError):
        pass
    return phase_id


def automation_name(release: str, scope: str, label: str) -> str:
    """The STANDARD automation title: '<release-id> · <scope> — <label>', e.g.
    '2026-08 · Code Complete Day — morning reminders'. `scope` is the phase display name (or a
    clear label like 'Release-wide' / 'Phases 2-4' for automations that aren't bound to one
    phase); `label` is the automation's short purpose. Every provisioned automation — the
    config/automations.yaml ones AND the skill-provisioned push-reminder / status-email ones —
    uses this format so titles are consistent and scannable."""
    scope = (scope or "Release-wide").strip()
    return f"{release} · {scope} — {str(label).strip()}"



def _step_fire_at(step_key: str):
    """The `fire_at_local` a step module declares (or None). step_key = '<phase>.<step>'."""
    phase, _, sid = step_key.partition(".")
    mod = steps_pkg.get_step(phase, sid)
    if mod is None:
        return None
    return (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")


def fire_at(phase_id: str, step_id: str):
    """Public: the `fire_at_local` (HH:MM) a step declares, or None. Used by the engine
    to gate a timed step until its wall-clock time."""
    return _step_fire_at(f"{phase_id}.{step_id}")


def _all_scheduled_steps(config_path: str) -> dict:
    """{ '<phase>.<step>': fire_at_local } for every configured workflow step whose
    module declares a fire_at_local — i.e. every active step a timed automation must own."""
    out = {}
    for key in _configured_step_keys(config_path):
        phase, _, sid = key.partition(".")
        mod = steps_pkg.get_step(phase, sid)
        if mod is None:
            continue
        fire = (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")
        if fire:
            out[key] = fire
    return out


def validate(config_path: str) -> list:
    """Return a list of human-readable problems (empty = healthy). Enforces:
      1. every automation step exists (has a discovered module),
      2. every TIME-OF-DAY automation's steps share ONE fire_at_local (its fire time)
         and each declares fire_at_local,
      3. no step is owned by two TIME-OF-DAY automations,
      4. every scheduled step (declares fire_at_local) is owned by SOME time-of-day
         automation.
    INTERVAL automations (those with `every:`, e.g. a poller) are exempt from the
    fire_at_local accounting — they may share a step with a time-of-day automation
    and their steps need not declare fire_at_local — but their steps must still exist.
    """
    problems = []
    defs = load_defs(config_path)
    slugs = set()
    owned = {}                       # step_key -> slug (time-of-day only)
    for d in defs:
        slug = d.get("slug", "?")
        if slug in slugs:
            problems.append(f"duplicate automation slug '{slug}'")
        slugs.add(slug)
        if set(d) - {
            "slug", "label", "phase", "scope", "steps", "every", "daily_at", "daily_window", "ccd_window",
            "on_demand", "provision_when", "cleanup_when", "purpose", "prompt_kind",
        }:
            problems.append(f"automation '{slug}' has unknown settings")
        sched_modes = sum(bool(d.get(k)) for k in ("every", "daily_at", "daily_window", "ccd_window"))
        if sched_modes > 1:
            problems.append(f"automation '{slug}' cannot combine 'every', 'daily_at', 'daily_window', and 'ccd_window'")
        if d.get("daily_at") is not None:
            try:
                if not isinstance(d["daily_at"], str) or len(d["daily_at"]) != 5:
                    raise ValueError
                datetime.strptime(d["daily_at"], "%H:%M")
            except ValueError:
                problems.append(f"automation '{slug}' has invalid daily_at {d.get('daily_at')!r} (expected HH:MM)")
        if d.get("daily_window") is not None and _parse_ccd_window(d.get("daily_window")) is None:
            problems.append(
                f"automation '{slug}' has invalid daily_window {d.get('daily_window')!r} (expected HH:MM-HH:MM/<N>h)"
            )
        if d.get("ccd_window") is not None and _parse_ccd_window(d.get("ccd_window")) is None:
            problems.append(
                f"automation '{slug}' has invalid ccd_window {d.get('ccd_window')!r} (expected HH:MM-HH:MM/<N>h)"
            )
        provision_when = d.get("provision_when")
        if provision_when is not None and (
            not d.get("on_demand")
            or not isinstance(provision_when, str)
            or not provision_when.startswith(("step_status:", "step_flag:", "phase_reached:"))
        ):
            problems.append(
                f"automation '{slug}' has invalid provision_when")
        rules = _cleanup_rules(d.get("cleanup_when"))
        if not rules or not all(_valid_cleanup_rule(rule) for rule in rules):
            problems.append(f"automation '{slug}' has invalid/missing cleanup_when")
        s_steps = d.get("steps", []) or []
        if not s_steps and d.get("prompt_kind") not in ("release-drainer", "morning-step-drain"):
            problems.append(f"automation '{slug}' has no steps")
            continue
        interval = bool(d.get("every") or d.get("daily_at") or d.get("daily_window") or d.get("ccd_window"))
        fires = set()
        for sk in s_steps:
            mod = steps_pkg.get_step(*sk.split(".", 1)) if "." in sk else None
            if mod is None:
                problems.append(f"automation '{slug}' references unknown step '{sk}'")
                continue
            if interval:
                continue             # pollers are exempt from fire-time accounting
            if sk in owned:
                problems.append(f"step '{sk}' is owned by two time-of-day automations "
                                f"('{owned[sk]}' and '{slug}')")
            owned[sk] = slug
            fire = (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")
            if not fire:
                problems.append(f"step '{sk}' (in '{slug}') declares no fire_at_local")
            else:
                fires.add(fire)
        if not interval and len(fires) > 1:
            problems.append(f"automation '{slug}' groups steps with different fire "
                            f"times {sorted(fires)} — split them")

    for sk in _all_scheduled_steps(config_path):
        if sk not in owned:
            problems.append(f"scheduled step '{sk}' (has fire_at_local) is not owned "
                            f"by any time-of-day automation in automations.yaml")
    return problems


def daily_window_slots(window_spec: str = "07:00-19:00/2h") -> tuple[tuple[int, int], ...]:
    """Return ordered `(hour, minute)` local slot tuples for a `'HH:MM-HH:MM/<N>h'` window."""
    parsed = _parse_ccd_window(window_spec)
    if parsed is None:
        raise ValueError(f"Invalid daily_window specification: {window_spec!r}")
    start_h, end_h, step_h, minute = parsed
    return tuple((h, minute) for h in range(start_h, end_h + 1, step_h))


def _daily_window_cron(
    window_spec: str,
    *,
    owner_timezone=None,
    scheduler_timezone=None,
    now=None,
) -> str:
    """Compile a recurring daily window (`07:00-19:00/2h`) into Scout's cron schedule."""
    parsed = _parse_ccd_window(window_spec)
    if parsed is None:
        raise ValueError(f"Invalid daily_window specification: {window_spec!r}")
    start_h, end_h, step_h, minute = parsed
    if not owner_timezone or not scheduler_timezone or owner_timezone == scheduler_timezone:
        hour_expr = f"{start_h}-{end_h}/{step_h}" if step_h > 1 else f"{start_h}-{end_h}"
        return f"cron: {minute} {hour_expr} * * *"
    owner = schedule.get_tz(owner_timezone)
    host = schedule.get_tz(scheduler_timezone)
    if owner is None or host is None:
        raise ValueError("Owner and scheduler host IANA timezones must both be available")
    from datetime import time as _time
    ref_utc = now if (now is not None and now.tzinfo is not None) else datetime.now(timezone.utc)
    ref_date = ref_utc.astimezone(owner).date()
    host_pts = [
        datetime.combine(ref_date, _time(h, minute), owner).astimezone(host)
        for h in range(start_h, end_h + 1, step_h)
    ]
    mins = sorted({dt.minute for dt in host_pts})
    hrs = sorted({dt.hour for dt in host_pts})
    return f"cron: {','.join(map(str, mins))} {','.join(map(str, hrs))} * * *"


def _format_scout_time_12h(hour: int, minute: int) -> str:
    """Format (hour, minute) in Scout's canonical `m_create_automation` NL token (`7am`, `7:30am`, `3pm`)."""
    suffix = "am" if hour < 12 else "pm"
    h12 = 12 if hour % 12 == 0 else hour % 12
    return f"{h12}{suffix}" if minute == 0 else f"{h12}:{minute:02d}{suffix}"


def _daily_schedule(hhmm: str, *, owner_timezone=None, scheduler_timezone=None, now=None) -> str:
    """Convert an owner-local HH:MM daily trigger into Scout's canonical `every day at <time>` schedule."""
    if not isinstance(hhmm, str) or len(hhmm) != 5:
        raise ValueError("Invalid daily_at time (expected HH:MM)")
    try:
        parsed = datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        raise ValueError("Invalid daily_at time (expected HH:MM)") from None
    hour, minute = parsed.hour, parsed.minute
    if owner_timezone and scheduler_timezone and owner_timezone != scheduler_timezone:
        owner = schedule.get_tz(owner_timezone)
        host = schedule.get_tz(scheduler_timezone)
        if owner is None or host is None:
            raise ValueError("Owner and scheduler host IANA timezones must both be available")
        ref_utc = now if (now is not None and now.tzinfo is not None) else datetime.now(timezone.utc)
        ref_date = ref_utc.astimezone(owner).date()
        local_dt = datetime.combine(ref_date, parsed.time(), owner)
        host_dt = local_dt.astimezone(host)
        hour, minute = host_dt.hour, host_dt.minute
    return f"every day at {_format_scout_time_12h(hour, minute)}"


def _ccd_window_cron(ccd_date, window_spec: str, *, owner_timezone=None, scheduler_timezone=None) -> str:
    """Compile a CCD-day recurring window (`08:00-22:00/2h`) into a date-pinned Scout cron schedule."""
    if not ccd_date:
        raise ValueError("Confirmed CCD is required for CCD window automation")
    parsed = _parse_ccd_window(window_spec)
    if parsed is None:
        raise ValueError(f"Invalid ccd_window specification: {window_spec!r}")
    start_h, end_h, step_h, minute = parsed
    hours_local = list(range(start_h, end_h + 1, step_h))
    if not owner_timezone or not scheduler_timezone or owner_timezone == scheduler_timezone:
        hour_expr = f"{start_h}-{end_h}/{step_h}" if step_h > 1 else f"{start_h}-{end_h}"
        return f"cron: {minute} {hour_expr} {ccd_date.day} {ccd_date.month} *"
    owner = schedule.get_tz(owner_timezone)
    host = schedule.get_tz(scheduler_timezone)
    if owner is None or host is None:
        raise ValueError("Owner and scheduler host IANA timezones must both be available")
    from datetime import time as _time
    host_pts = [
        datetime.combine(ccd_date, _time(h, minute), owner).astimezone(host)
        for h in hours_local
    ]
    mins = sorted({dt.minute for dt in host_pts})
    hrs = sorted({dt.hour for dt in host_pts})
    days = sorted({dt.day for dt in host_pts})
    months = sorted({dt.month for dt in host_pts})
    return (
        f"cron: {','.join(map(str, mins))} {','.join(map(str, hrs))} "
        f"{','.join(map(str, days))} {','.join(map(str, months))} *"
    )


def _ccd_cron(ccd_date, hhmm: str, *, owner_timezone, scheduler_timezone, now=None):
    """Convert the owner CCD instant to host cron, rejecting past/ambiguous targets."""
    if not ccd_date or not hhmm:
        raise ValueError("Confirmed CCD and fire time are required")
    owner = schedule.get_tz(owner_timezone) if owner_timezone else None
    host = schedule.get_tz(scheduler_timezone) if scheduler_timezone else None
    if owner is None or host is None:
        raise ValueError("Owner and scheduler host IANA timezones must both be available")
    try:
        t = datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        raise ValueError("Invalid CCD fire time") from None
    local = datetime.combine(ccd_date, t.time(), owner)
    instant = local.astimezone(timezone.utc)
    if (instant.astimezone(owner).replace(tzinfo=None) != local.replace(tzinfo=None)
            or local.replace(fold=1).utcoffset() != local.utcoffset()):
        raise ValueError("CCD fire time is nonexistent or ambiguous in the owner timezone")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or instant <= now:
        raise ValueError("CCD fire time is past; never roll a missed one-shot into next year")
    target = instant.astimezone(host)
    if target.replace(fold=1).utcoffset() != target.replace(fold=0).utcoffset():
        raise ValueError("CCD fire time is ambiguous in the scheduler host timezone")
    # Cron has no year; more than one year ahead could first fire in the wrong year.
    if (instant - now).total_seconds() > 365 * 86400:
        raise ValueError("CCD target is too far ahead for a yearless one-shot cron")
    return f"cron: {target.minute} {target.hour} {target.day} {target.month} *"


def _prompt_for(spec: dict, release: str) -> str:
    """A concrete instruction the automation runs. Notifications use shared claim/result;
    other steps retain their reservation/domain follow-up and journaling.

    A step MAY OWN a bespoke prompt by declaring `automation_prompt(release, spec)` on its
    module (the single source of truth, like `fire_at_local`) — used for genuinely bespoke
    flows such as the localization trigger + poller. This keeps the planner generic: it
    never special-cases a step id. Steps without one get the default claim/result
    prompt below."""
    steps = spec.get("steps") or []
    step_list = ", ".join(steps)
    cleanup = (
        f"\nFinally run `automation cleanup --release {release} --json`. For each removal "
        f"IN ORDER, claim it with `automation claim-delete --id <id> --executor <session> "
        f"--json`; call `m_delete_automation` only when permission_to_delete is true, then "
        f"record `automation delete-result --id <id> --attempt-id <attempt> --outcome "
        f"deleted|not_deleted|uncertain --evidence <provider evidence>`. Never delete or "
        f"deregister without a durable claim. STOP the cleanup loop on any barrier, "
        f"error or uncertain deletion; retain the recovery worker. Terminal ID-less "
        f"intents need owner-reviewed abandon-prepared with fresh absence evidence. "
        f"This is a finally block: "
        f"execute it even on silence, stopped work, or errors. Halts suspend, not delete.")
    protocol = (
        "Before every mutating or scheduled release command, pass the global --scheduled flag before its "
        "subcommand so execution serializes cleanly on `<runs-root>/<release>/.state.lock` with any "
        "concurrent interactive session. Attempt each operation at most once per unchanged progress "
        "snapshot; no progress returns a concrete wait/input/failure instead of another loop.\n"
        + delivery.PROTOCOL.replace("<release>", release)) + (
        "\nFor every release-advancing task, inspect work_dispatch after each result. "
        "A sibling failure/owner gate does not cancel independent pending_work. "
        "Drain only eligible non-approval work through its owning protocol; never bypass scope, consent, "
        "dependency/time guards or unknown-write recovery. Before finishing, run "
        f"`status --release {release} --handoff --json --no-pipeline-check`. "
        "For a Scout task genuinely needing human approval (`requires_approval: true`), input, or "
        "unresolved recovery, use --hold-step phase.step=<actual-reason> on that read-only guard after "
        "independent non-approval work is exhausted so the release parks cleanly at the human gate. "
        "Never resurrect or recreate automations that the owner disabled or deleted."
    )

    if spec.get("prompt_kind") in ("release-drainer", "morning-step-drain"):
        return (
            f"Release {release} — Unified Release Drainer (runs every 2h from 7:00 AM to 7:00 PM).\n"
            f"1. Run `python -m orchestrator.cli --scheduled drain --release {release} --trigger scheduled --deliver --json`.\n"
            "   - Python automatically drains all eligible Python `auto` steps, reconciles `in_flight` / `read_only` "
            "steps, and delivers `requires_approval: false` notification steps silently via Agency MCP "
            "(NEVER call `workiq_send_*` or `m_ask_user`).\n"
            "2. Inspect `pending_host_work` in the JSON output:\n"
            "   - If `pending_host_work` is non-empty (e.g. Entry Gate Scout auto checks, `preflight.lockdown` "
            "Playwright CCOA scrape, or `ccd.localization` via `launch-localization --execute --auto-approve`), "
            "execute each non-approval item ONCE via its owning command. Never start `requires_approval: true` writes.\n"
            "   - After finishing `pending_host_work`, finalize the journaled run with "
            f"`python -m orchestrator.cli --scheduled drain --release {release} --finalize-run <run_id> --deliver --json` "
            "(which runs any newly unlocked steps, writes `<runs-root>/<release>/drain-runs.jsonl`, and evaluates CTA notifications).\n"
            "3. Inspect `scout_bot_cta` on the finalized drain payload:\n"
            "   - When `scout_bot_cta` is non-null, claim it with "
            f"`python -m orchestrator.cli --scheduled notification claim --release {release} --id <id> --hash <hash> "
            "--executor release-drainer --json`; ONLY when `permission_to_send` is true, call `m_send_teams_message` "
            "with the exact `message` text so the release owner receives the Scout bot Call-to-Action, then record "
            f"`python -m orchestrator.cli --scheduled notification result --release {release} --id <id> --execution-id "
            "<execution_id> --outcome sent|not_sent|uncertain --evidence \"<provider evidence>\"`.\n"
            + protocol + cleanup
        )

    # Single-step automation whose step owns a bespoke prompt → delegate to the module.
    if len(steps) == 1:
        phase, _, sid = steps[0].partition(".")
        mod = steps_pkg.get_step(phase, sid)
        fn = getattr(mod, "automation_prompt", None)
        if callable(fn):
            prompt = fn(release, spec)
            if prompt:
                return protocol + "\n" + prompt + cleanup

    # Default: notification claim/result, or the existing non-notification follow-up.
    return (
        f"Release {release} — {spec['name']}.\n"
        f"For EACH of these steps in order: {step_list} —\n"
        f"1. run `step-action --release {release} --phase {spec['phase']} --step <step>`;\n"
        f"2. done means no send (record-step only for no_delivery_required:true); "
        f"blocked/error means skip sending and proceed to cleanup. "
        f"For `requires_approval: false` notifications use `deliver-step --release {release} "
        f"--phase {spec['phase']} --step <step> --json`. "
        f"{protocol}\n"
        f"3. Non-notification actions retain their existing reservation and domain follow-up.\n"
        f"4. silently journal it: `journal --release {release} --source scout "
        f"--kind automation --text \"<slug> ran <step>\"`.\n"
        f"Respect the mocks.local.yaml redirects if present. Report a one-line summary."
    ) + cleanup


def plan(config_path: str, release: str, ccd: str, *, owner_timezone=None,
         scheduler_timezone=None, now=None) -> dict:
    """Concrete provisioning specs for a release. Returns
    {release, ccd, problems, automations:[...]}. Each automation spec has:
      slug, name, phase, steps, purpose, fire_at, schedule (NL for m_create_automation),
      ccd_date, weekday, prompt, registration (the `automation prepare` args)."""
    problems = validate(config_path)
    defs = load_defs(config_path)
    defaults = _provider_defaults(config_path)
    scheduler_timezone = scheduler_timezone or schedule.detect_local_tz()
    ccd_date = schedule.parse_date(ccd) if ccd else None
    weekday = ccd_date.strftime("%A") if ccd_date else None

    out = []
    for d in defs:
        entry_problems = []
        slug = d.get("slug", "?")
        s_steps = d.get("steps", []) or []
        interval = d.get("every")
        daily_at = d.get("daily_at")
        daily_window = d.get("daily_window")
        ccd_window = d.get("ccd_window")
        # STANDARD name: '<release> · <scope> — <label>'. `label` is the short purpose; `scope`
        # is the phase display name (or an explicit `scope:` override for non-phase automations).
        label = d.get("label", slug)
        scope = d.get("scope") or phase_label(config_path, d.get("phase"))
        name = automation_name(release, scope, label)
        if interval:
            fire_at, sched, one_shot = None, f"every {interval}", False
        elif daily_at:
            fire_at = daily_at
            one_shot = False
            try:
                sched = _daily_schedule(
                    daily_at,
                    owner_timezone=owner_timezone,
                    scheduler_timezone=scheduler_timezone,
                    now=now,
                )
            except ValueError as exc:
                sched = None
                entry_problems.append(str(exc))
        elif daily_window:
            fire_at = daily_window
            one_shot = False
            try:
                sched = _daily_window_cron(
                    daily_window,
                    owner_timezone=owner_timezone,
                    scheduler_timezone=scheduler_timezone,
                    now=now,
                )
            except ValueError as exc:
                sched = None
                entry_problems.append(str(exc))
        elif ccd_window:
            fire_at = ccd_window
            one_shot = False
            try:
                sched = _ccd_window_cron(
                    ccd_date,
                    ccd_window,
                    owner_timezone=owner_timezone,
                    scheduler_timezone=scheduler_timezone,
                )
            except ValueError as exc:
                sched = None
                entry_problems.append(str(exc))
        else:
            fire_at = _step_fire_at(s_steps[0]) if s_steps else None
            # Pin to the EXACT CCD date via cron — never 'every <weekday>' (which fires
            # the next matching weekday, a week early for a CCD provisioned in advance).
            try:
                sched = _ccd_cron(
                    ccd_date, fire_at, owner_timezone=owner_timezone,
                    scheduler_timezone=scheduler_timezone, now=now,
                )
            except ValueError as exc:
                sched = None
                entry_problems.append(str(exc))
            one_shot = True
        spec = {
            "slug": slug,
            "name": name,
            "phase": d.get("phase"),
            "steps": s_steps,
            "kind": "step-driving" if s_steps else "release-level",
            "prompt_kind": d.get("prompt_kind"),
            "purpose": d.get("purpose", ""),
            "fire_at": fire_at,
            "daily_at": daily_at,
            "daily_window": daily_window,
            "ccd_window": ccd_window,
            "ccd_date": ccd_date.isoformat() if ccd_date else None,
            "weekday": weekday,
            "schedule": sched,          # daily NL schedule, interval poller, or one-shot cron
            "one_shot": one_shot,
            "interval": interval or None,
            "optional": True,
            # ON-DEMAND automations (e.g. the RC poller) are NOT provisioned at release
            # start — the skill creates them only when their trigger condition arises
            # (an in-flight re-triggered RC) and tears them down when it clears.
            "on_demand": bool(d.get("on_demand")),
            "provision_when": d.get("provision_when"),
            "cleanup_when": d.get("cleanup_when"),
        }
        spec["prompt"] = _prompt_for(spec, release)
        agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec["prompt"] = (
            f"Work from the authoritative release-agent root: {agent_root}. "
            "All command fragments below mean `python -m orchestrator.cli` in this root. "
            "Use ONE authoritative runs directory, never a copied release snapshot.\n"
            "Automations are optional non-blocking accelerators: never recreate or re-enable "
            "automations that the owner disabled or deleted, never repeat uncertain creation/deletion, "
            "and never start `requires_approval: true` writes without live owner consent.\n"
            + spec["prompt"]
        )
        from orchestrator.registry import provider_spec
        spec["provider_spec"] = None
        if sched:
            try:
                spec["provider_spec"] = provider_spec({
                    **defaults, "name": name, "description": spec["purpose"],
                    "prompt": spec["prompt"], "schedule": sched, "oneShot": one_shot,
                })
            except ValueError as exc:
                entry_problems.append(str(exc))
        spec["problems"] = entry_problems
        # Exactly what to record after creating it, so linkage + schedule are captured
        # Schedule remains available for the read-only drift report.
        spec["registration"] = {
            "name": name, "release": release, "purpose": d.get("purpose", ""),
            "steps": s_steps, "kind": spec["kind"], "schedule": sched, "slug": slug,
            "cleanup_when": d.get("cleanup_when"),
        }
        out.append(spec)
    return {"release": release, "ccd": ccd, "problems": problems, "automations": out,
            "owner_timezone": owner_timezone, "scheduler_timezone": scheduler_timezone}


def _provision_rule_matches(state, rule: str, selection=None) -> bool:
    """Evaluate a declarative on-demand provisioning condition from live release state."""
    kind, sep, remainder = str(rule or "").partition(":")
    if not sep or not remainder:
        return False
    if kind == "phase_reached":
        return bool(selection and selection.frontier and selection.frontier.id == remainder)
    step_key, sep2, expected = remainder.rpartition(":")
    if not sep2 or "." not in step_key or not expected:
        return False
    phase, step_id = step_key.split(".", 1)
    step = state.get_step(phase, step_id)
    if kind == "step_status":
        return step.status == expected
    if kind == "step_flag":
        return bool(step.data.get(expected))
    return False


def _producer_applied(state, spec):
    for record in state.notification_deliveries.values():
        if record.get("status") != "sent" or (record.get("completion") or {}).get("status") != "applied":
            continue
        descriptor = record.get("descriptor") or {}
        request = ((descriptor.get("completion") or {}).get("automation") or {}).get("on_demand")
        scope = descriptor.get("scope") or {}
        phase, step = scope.get("phase"), scope.get("step")
        if request != spec["slug"] or f"{phase}.{step}" not in spec["steps"]:
            continue
        producer = state.get_step(phase, step)
        if (producer.status == "done" and scope.get("generation") == (producer.invalidated_at or "initial")
                and all(getattr(state, key, None) == value
                        for key, value in (scope.get("release_matches") or {}).items())):
            return True
    return False


def ensure_reason(state, spec, config_path, selection=None, *, ignore_holds=False):
    """Why a declared optional accelerator is not eligible now; no provider calls or mutations."""
    if selection is None:
        from orchestrator.engine import Orchestrator
        selection = Orchestrator(config_path, state, mocks={}).scheduling()
    if selection.frontier is None or state.cancellation:
        return "The release is terminal; do not recreate its automations"
    if not ignore_holds and selection.suspension in ("halted", "denied", "blocked", "workflow_revision"):
        return "The release has an explicit hold; reconcile receipts but do not create workers"
    if "manual" in _cleanup_rules(spec["cleanup_when"]):
        return "Manual workers are not automatically ensured"
    if spec["on_demand"]:
        rule = spec.get("provision_when")
        if rule:
            requested = _provision_rule_matches(state, rule, selection=selection)
        else:
            requested = _producer_applied(state, spec)
        if not requested:
            return "The on-demand worker's durable trigger has not occurred"
    target = {**spec["registration"], "id": None, "scope": "release", "status": "active"}
    cleanup = cleanup_plan(state, [target], config_path)
    if cleanup["problems"]:
        return "; ".join(cleanup["problems"])
    if cleanup["removals"]:
        return "The worker's objective is already complete; do not recreate it"
    return ""


def provisioning_obligations(state, entries: list[dict], config_path: str) -> dict:
    """Return optional background accelerators and any unresolved registry recoveries.

    In the unified hybrid model, automations are purely optional non-blocking accelerators:
    `required` is always empty so missing, disabled, or deleted automations never stall
    interactive or manual workflow execution.
    """
    planned = plan(
        config_path, state.release_id, state.ccd, owner_timezone=state.timezone)
    by_slug = {
        entry.get("slug"): entry for entry in entries
        if entry.get("release") == state.release_id and entry.get("scope", "release") == "release"
    }
    from orchestrator.engine import Orchestrator
    selection = Orchestrator(config_path, state, mocks={}).scheduling()
    terminal = selection.frontier is None or bool(state.cancellation)
    optional = []
    recoveries = []
    active = []
    ensure = []
    problems = list(planned["problems"])
    duplicates = {
        slug for slug in by_slug
        if sum(entry.get("slug") == slug and entry.get("release") == state.release_id
               and entry.get("scope", "release") == "release" for entry in entries) > 1
    }
    for spec in planned["automations"]:
        rule = spec.get("provision_when")
        eligible_trigger = (
            not spec["on_demand"]
            or (_provision_rule_matches(state, rule, selection=selection) if rule else _producer_applied(state, spec))
        )
        managed = spec["slug"] in by_slug
        if terminal or not (eligible_trigger or managed):
            continue
        if ensure_reason(state, spec, config_path, selection, ignore_holds=True):
            continue
        if spec["slug"] in duplicates:
            problems.append(f"{spec['slug']}: duplicate registry identities require reconciliation")
            continue
        problems.extend(f"{spec['slug']}: {problem}" for problem in spec["problems"])
        if not spec["problems"] and not ensure_reason(state, spec, config_path, selection):
            ensure.append(spec)
        existing = by_slug.get(spec["slug"])
        if existing is None:
            if eligible_trigger and not spec["problems"]:
                optional.append(spec)
        elif existing.get("status") == "active" and existing.get("id"):
            active.append(spec["slug"])
        elif existing.get("status") in ("absent", "deleted", "abandoned"):
            # Owner-deleted or retired workers remain optional and never demand recovery.
            if eligible_trigger and not spec["problems"]:
                optional.append(spec)
        else:
            recoveries.append({
                "slug": spec["slug"],
                "key": existing.get("key"),
                "id": existing.get("id"),
                "status": existing.get("status"),
            })
    return {
        "release": state.release_id,
        "required": [],
        "optional": optional,
        "recoveries": recoveries,
        "active": active,
        "problems": problems,
        "ensure": ensure,
    }


def cleanup_plan(state, entries: list, config_path: str, *, now=None) -> dict:
    """Decide which registered automations reached their declared lifecycle end."""
    with open(config_path, "r", encoding="utf-8") as fh:
        phases = {p["id"]: p for p in (yaml.safe_load(fh) or {}).get("phases", [])}
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(config_path, state, mocks={}, now=now)
    from orchestrator.revision import mismatch_reason
    revision_problem = mismatch_reason(orch)
    if revision_problem:
        return {"release": state.release_id, "removals": [], "problems": [revision_problem]}
    selection = orch.scheduling()
    release_complete = selection.frontier is None
    release_terminal = (
        release_complete or selection.status == "cancelled"
    )
    terminal_reason = (
        "release cancelled"
        if selection.status == "cancelled"
        else "release complete"
    )
    removals, problems = [], []
    known_steps = {f"{p['id']}.{s['id']}" for p in phases.values() for s in p["steps"]}

    def evaluate(rule, entry, steps):
        if rule == "manual":
            return False, ""
        if rule == "release_done":
            return release_terminal, terminal_reason
        if rule == "steps_done":
            return (bool(steps) and all(selection.step(*s.split(".", 1)).complete for s in steps),
                    "all driven steps done")
        if rule == "steps_settled":
            settled = []
            for step_key in steps:
                phase_id, step_id = step_key.split(".", 1)
                ready = selection.step(phase_id, step_id)
                definition = ready.definition
                record = state.get_step(phase_id, step_id)
                settled.append(
                    ready.complete
                    if definition and definition.is_gate
                    else record.status in ("done", "skipped", "blocked")
                )
            return bool(steps) and all(settled), "all driven steps settled"
        if isinstance(rule, str) and rule.startswith("phase_done:"):
            phase_id = rule.split(":", 1)[1]
            phase = phases.get(phase_id)
            if phase is None:
                problems.append(f"{entry.get('id')}: unknown cleanup phase '{phase_id}'")
                return False, ""
            return (selection.phase(phase_id).complete,
                    f"phase {phase_id} complete")
        if isinstance(rule, str) and rule.startswith("step_flag:"):
            try:
                step_key, flag = rule[len("step_flag:"):].rsplit(":", 1)
                phase_id, step_id = step_key.split(".", 1)
            except ValueError:
                problems.append(f"{entry.get('id')}: malformed cleanup rule '{rule}'")
                return False, ""
            if step_key not in known_steps or not flag:
                problems.append(f"{entry.get('id')}: unknown cleanup step/flag '{rule}'")
                return False, ""
            return (bool(state.get_step(phase_id, step_id).data.get(flag)),
                    f"{step_key}.{flag} set")
        if isinstance(rule, str) and rule.startswith("ccd_after:"):
            hhmm = rule.split(":", 1)[1]
            ccd = schedule.parse_date(state.ccd)
            if ccd is None or orch.tz is None:
                return False, ""
            try:
                parsed_t = datetime.strptime(hhmm, "%H:%M").time()
            except ValueError:
                problems.append(f"{entry.get('id')}: malformed cleanup rule '{rule}'")
                return False, ""
            cutoff = datetime.combine(ccd, parsed_t, tzinfo=orch.tz)
            return (orch.now_local >= cutoff, f"CCD {hhmm} cutoff reached")
        problems.append(f"{entry.get('id')}: invalid/missing cleanup_when")
        return False, ""

    for entry in entries:
        rules = _cleanup_rules(entry.get("cleanup_when"))
        steps = entry.get("steps") or []
        if "manual" in rules or entry.get("scope") == "shared":
            continue
        if entry.get("scope") != "release" or entry.get("release") != state.release_id:
            problems.append(f"{entry.get('id')}: explicit release scope required; owner recovery needed")
            continue
        due = release_terminal
        reason = f"{terminal_reason} (universal backstop)" if due else ""
        if selection.status == "halted" and not due:
            continue
        if not due:
            if (not isinstance(steps, list)
                    or any(not isinstance(s, str) or s not in known_steps for s in steps)):
                problems.append(f"{entry.get('id')}: invalid/unknown driven step; owner recovery required")
                continue
            if not rules:
                problems.append(f"{entry.get('id')}: invalid/missing cleanup_when; owner recovery required")
                continue
            try:
                if delivery.has_pending(orch, steps):
                    continue
            except ValueError as exc:
                problems.append(f"{entry.get('id')}: {exc}")
                continue
        for rule in rules:
            if due:
                break
            due, reason = evaluate(rule, entry, steps)
            if due:
                break
        if due:
            removals.append({
                "id": entry["id"], "name": entry["name"], "slug": entry.get("slug"),
                "cleanup_when": entry.get("cleanup_when"), "reason": reason,
            })
    kinds = {e["id"]: e.get("kind") for e in entries}
    removals.sort(key=lambda r: (
        kinds.get(r["id"]) == "release-level",
        r.get("slug") in ("release-drainer", "morning-step-drain"),
        r["name"],
    ))
    return {"release": state.release_id, "removals": removals, "problems": problems}
