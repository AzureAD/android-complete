"""Per-release automation planning + validation (traceability layer).

`config/automations.yaml` declares WHICH Scout automations a release provisions and
WHICH STEPS each drives. This module turns that data into:

  * plan(release, ccd)  — concrete specs the skill uses to create + register each
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
from datetime import datetime

import yaml

import steps as steps_pkg
from orchestrator import schedule


def automations_path(config_path: str) -> str:
    """config/automations.yaml sits next to phases.yaml (config_path)."""
    return os.path.join(os.path.dirname(config_path), "automations.yaml")


def load_defs(config_path: str) -> list:
    p = automations_path(config_path)
    if not os.path.exists(p):
        return []
    with open(p, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    return doc.get("automations", []) or []


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
    owned = {}                       # step_key -> slug (time-of-day only)
    for d in defs:
        slug = d.get("slug", "?")
        s_steps = d.get("steps", []) or []
        if not s_steps:
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


def _ccd_cron(ccd_date, hhmm: str):
    """A cron schedule pinned to the EXACT Code Complete Date + fire time — NOT a
    recurring weekday. `every <weekday>` fires on the NEXT matching weekday, which for
    a CCD more than a week out (these are provisioned at release start) is the wrong
    date — it fired the CCD-day comms a week early. Cron `M H D Mo *` targets the CCD's
    day-of-month + month exactly, so a one-shot fires ON the CCD. Returns the NL Scout
    accepts (e.g. 'cron: 0 9 26 8 *') or None if inputs are missing/invalid.

    TIMEZONE: emit the LOCAL wall-clock time directly — do NOT convert to UTC.
    Scout's scheduler interprets cron in host-local time (empirically verified
    2026-08-20: a cron '37 9' fired at 09:37 PDT / 16:37 UTC, not 09:37 UTC). So a
    `hhmm` of '09:00' correctly fires at 09:00 local on the CCD. Adding a UTC
    conversion here would shift every CCD-day comm by the host's UTC offset."""
    if not ccd_date or not hhmm:
        return None
    try:
        t = datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        return None
    return f"cron: {t.minute} {t.hour} {ccd_date.day} {ccd_date.month} *"


def _prompt_for(spec: dict, release: str) -> str:
    """A concrete instruction the automation runs. Scout resolves each step via
    step-action, executes the send/trigger, records it, and journals it.

    A step MAY OWN a bespoke prompt by declaring `automation_prompt(release, spec)` on its
    module (the single source of truth, like `fire_at_local`) — used for genuinely bespoke
    flows such as the localization trigger + poller. This keeps the planner generic: it
    never special-cases a step id. Steps without one get the default send + record-step
    prompt below."""
    steps = spec.get("steps") or []
    step_list = ", ".join(steps)

    # Single-step automation whose step owns a bespoke prompt → delegate to the module.
    if len(steps) == 1:
        phase, _, sid = steps[0].partition(".")
        mod = steps_pkg.get_step(phase, sid)
        fn = getattr(mod, "automation_prompt", None)
        if callable(fn):
            prompt = fn(release, spec)
            if prompt:
                return prompt

    # Default: send/trigger + record-step done (reminders).
    return (
        f"Release {release} — {spec['name']}.\n"
        f"It is Code Complete Day. For EACH of these steps in order: {step_list} —\n"
        f"1. run `step-action --release {release} --phase {spec['phase']} --step <step>`;\n"
        f"2. execute the returned needs_skill action (send the email / post the Teams "
        f"message) with the given payload;\n"
        f"3. `record-step --release {release} --phase {spec['phase']} --step <step> "
        f"--status pass` (or blocked with a reason);\n"
        f"4. silently journal it: `journal --release {release} --source scout "
        f"--kind automation --text \"<slug> ran <step>\"`.\n"
        f"Respect the mocks.local.yaml redirects if present. Report a one-line summary."
    )


def plan(config_path: str, release: str, ccd: str) -> dict:
    """Concrete provisioning specs for a release. Returns
    {release, ccd, problems, automations:[...]}. Each automation spec has:
      slug, name, phase, steps, purpose, fire_at, schedule (NL for m_create_automation),
      ccd_date, weekday, prompt, registration (the `automation register` args)."""
    problems = validate(config_path)
    defs = load_defs(config_path)
    ccd_date = schedule.parse_date(ccd) if ccd else None
    weekday = ccd_date.strftime("%A") if ccd_date else None

    out = []
    for d in defs:
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
            sched = _ccd_cron(ccd_date, fire_at)
            one_shot = True
        spec = {
            "slug": slug,
            "name": name,
            "phase": d.get("phase"),
            "steps": s_steps,
            "kind": "step-driving",     # everything in automations.yaml drives steps
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
        }
        spec["prompt"] = _prompt_for(spec, release)
        # Exactly what to record after creating it, so linkage + schedule are captured
        # (schedule lets `automation sync` detect CCD drift and re-pin the cron).
        spec["registration"] = {
            "name": name, "release": release, "purpose": d.get("purpose", ""),
            "steps": s_steps, "kind": "step-driving", "schedule": sched, "slug": slug,
        }
        out.append(spec)
    return {"release": release, "ccd": ccd, "problems": problems, "automations": out}
