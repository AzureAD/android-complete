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
    return isinstance(rule, str) and (rule in _CLEANUP_RULES
                                     or rule.startswith(("phase_done:", "step_flag:")))


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


def load_defs(config_path: str) -> list:
    return _definition_document(config_path)["automations"]


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
    """{ '<phase>.<step>': fire_at_local } for every discovered module that declares
    a fire_at_local — i.e. every step that a timed automation must own."""
    out = {}
    for key, mod in steps_pkg.discover().items():
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
            "slug", "label", "phase", "scope", "steps", "every", "on_demand",
            "provision_when", "cleanup_when", "purpose", "prompt_kind",
        }:
            problems.append(f"automation '{slug}' has unknown settings")
        provision_when = d.get("provision_when")
        if provision_when is not None and (
            not d.get("on_demand")
            or not isinstance(provision_when, str)
            or not provision_when.startswith(("step_status:", "step_flag:"))
        ):
            problems.append(
                f"automation '{slug}' has invalid provision_when")
        rules = _cleanup_rules(d.get("cleanup_when"))
        if not rules or not all(_valid_cleanup_rule(rule) for rule in rules):
            problems.append(f"automation '{slug}' has invalid/missing cleanup_when")
        s_steps = d.get("steps", []) or []
        if not s_steps and d.get("prompt_kind") not in ("push-reminders", "daily-status-email"):
            problems.append(f"automation '{slug}' has no steps")
            continue
        interval = bool(d.get("every"))
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
    protocol = delivery.PROTOCOL.replace("<release>", release)
    provisioning = (
        "\nProvision workers only through `automation plan` and the exact complete "
        "provider_spec/registration it returns. Write the exact provider_spec once to "
        "a fresh temporary JSON file outside release state, then call `automation prepare` "
        "with --spec-file <path>. Obtain a fresh exhaustive provider list and full details; "
        "losslessly write {observed_at:<UTC>,complete:true,automations:[{id:<id>,"
        "spec:<complete-provider-kwargs>}]} to a second temporary JSON file. Call "
        "`automation reconcile-create` with the SAME --spec-file, --observed-file, "
        "--claim and --executor. Only permission_to_create:true permits "
        "m_create_automation with exactly returned spec. Immediately acknowledge "
        "create-result with owning --attempt-id, the SAME --spec-file and receipt evidence "
        "of that exact invocation. Unknown outcomes are uncertain, never retry them. "
        "Pass --on-demand <slug> for on-demand provisioning. Never register directly "
        "or update in place; review delete/recreate. Delete both temporary files only "
        "after the owning result is durably recorded. Do not persist prompts/specs or "
        "raw provider responses in release state, registry, journals, or evidence text."
    )

    if spec.get("prompt_kind") == "push-reminders":
        return (
            f"Release {release} — advance this pinned release autonomously.\n"
            f"1. Run `status --release {release} --json`. Missing, unsigned, halted, "
            "blocked, cancelled or complete means skip new work, NOT cleanup.\n"
            f"2. Loop `next --release {release} --json`; resolve scout_pending via "
            f"`step-action --release {release} --phase <phase> --step <step>`. "
            "Non-notification gather/trigger actions retain domain follow-ups and "
            "owning effect recovery; never blind-record pass or start another write.\n"
            f"3. Run `automation obligations --release {release} --json` after the drain "
            "and on every later run, even when scout_pending is empty. Provision every "
            "required on-demand worker through its exact plan; reconcile any listed "
            "recovery instead of creating a duplicate. This durable check is mandatory "
            "when another worker won a trigger race. Stop on problems.\n"
            f"4. Run `tick --release {release} --json`, then `notification prepare "
            f"--release {release} --source digest`. Independently deliver eligible "
            "owner email, owner Teams and Core alerts through claim/result, not raw "
            "message blocks. Core alerts are scoped to active preflight after 9 AM on "
            "the previous business day or CCD. Inspect source pending for completion "
            "and provisioning recovery; never resend claimed/uncertain/sent records. "
            "Never add courtesy copies. Respect mocks.local.yaml redirects.\n"
            + protocol + provisioning + cleanup
        )
    if spec.get("prompt_kind") == "daily-status-email":
        return (
            f"Release {release} — hourly partner status-email check. The command sends "
            "only on the first eligible tick at/after 17:00 in the stored owner timezone "
            "(weekdays excluding US holidays), during Phases 2–4; hourly polling avoids "
            "host/owner DST drift. Never use --force in this worker.\n"
            f"Run `notification prepare --release {release} --source status-email`. "
            "For an isolated TEST release use --send-to <verified-test-address>. "
            "Empty/stopped/claimed/uncertain/sent means no send; no automatic retry. "
            "The final-status step has its own closing send. Respect mocks.local.yaml.\n"
            + protocol + provisioning + cleanup
        )

    # Single-step automation whose step owns a bespoke prompt → delegate to the module.
    if len(steps) == 1:
        phase, _, sid = steps[0].partition(".")
        mod = steps_pkg.get_step(phase, sid)
        fn = getattr(mod, "automation_prompt", None)
        if callable(fn):
            prompt = fn(release, spec)
            if prompt:
                return protocol + "\n" + prompt + provisioning + cleanup

    # Default: notification claim/result, or the existing non-notification follow-up.
    return (
        f"Release {release} — {spec['name']}.\n"
        f"It is Code Complete Day. For EACH of these steps in order: {step_list} —\n"
        f"1. run `step-action --release {release} --phase {spec['phase']} --step <step>`;\n"
        f"2. done means no send (record-step only for no_delivery_required:true); "
        f"blocked/error means skip sending and proceed to cleanup. "
        f"For notifications use source step with phase {spec['phase']} and the step ID. "
        f"{protocol}\n"
        f"3. Non-notification actions retain their existing reservation and domain follow-up.\n"
        f"4. silently journal it: `journal --release {release} --source scout "
        f"--kind automation --text \"<slug> ran <step>\"`.\n"
        f"Respect the mocks.local.yaml redirects if present. Report a one-line summary."
    ) + provisioning + cleanup


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
        # STANDARD name: '<release> · <scope> — <label>'. `label` is the short purpose; `scope`
        # is the phase display name (or an explicit `scope:` override for non-phase automations).
        label = d.get("label", slug)
        scope = d.get("scope") or phase_label(config_path, d.get("phase"))
        name = automation_name(release, scope, label)
        if interval:
            fire_at, sched, one_shot = None, f"every {interval}", False
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
            "ccd_date": ccd_date.isoformat() if ccd_date else None,
            "weekday": weekday,
            "schedule": sched,          # one-shot on the CCD date, or an interval poller
            "one_shot": one_shot,
            "interval": interval or None,
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


def _provision_rule_matches(state, rule: str) -> bool:
    """Evaluate a declarative on-demand provisioning condition from live release state."""
    kind, sep, remainder = str(rule or "").partition(":")
    step_key, sep2, expected = remainder.rpartition(":")
    if not sep or not sep2 or "." not in step_key or not expected:
        return False
    phase, step_id = step_key.split(".", 1)
    step = state.get_step(phase, step_id)
    if kind == "step_status":
        return step.status == expected
    if kind == "step_flag":
        return bool(step.data.get(expected))
    return False


def provisioning_obligations(state, entries: list[dict], config_path: str) -> dict:
    """Return missing or unresolved on-demand workers required by current state.

    This is computed from durable release state plus the automation registry, so the
    obligation survives whichever scheduled worker wins the triggering action.
    """
    planned = plan(
        config_path, state.release_id, state.ccd, owner_timezone=state.timezone)
    by_slug = {entry.get("slug"): entry for entry in entries}
    required = []
    recoveries = []
    active = []
    problems = list(planned["problems"])
    for spec in planned["automations"]:
        rule = spec.get("provision_when")
        if not spec["on_demand"] or not rule or not _provision_rule_matches(state, rule):
            continue
        problems.extend(f"{spec['slug']}: {problem}" for problem in spec["problems"])
        existing = by_slug.get(spec["slug"])
        if existing is None:
            required.append(spec)
        elif existing.get("status") == "active":
            active.append(spec["slug"])
        else:
            recoveries.append({
                "slug": spec["slug"],
                "key": existing.get("key"),
                "id": existing.get("id"),
                "status": existing.get("status"),
            })
    return {
        "release": state.release_id,
        "required": required,
        "recoveries": recoveries,
        "active": active,
        "problems": problems,
    }


def cleanup_plan(state, entries: list, config_path: str) -> dict:
    """Decide which registered automations reached their declared lifecycle end."""
    with open(config_path, "r", encoding="utf-8") as fh:
        phases = {p["id"]: p for p in (yaml.safe_load(fh) or {}).get("phases", [])}
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(config_path, state, mocks={})
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
        if selection.status in ("halted", "blocked", "cancelled") and not due:
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
        kinds.get(r["id"]) == "release-level", r.get("slug") == "push-reminders", r["name"]
    ))
    return {"release": state.release_id, "removals": removals, "problems": problems}
