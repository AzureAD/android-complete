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


def _step_fire_at(step_key: str):
    """The `fire_at_local` a step module declares (or None). step_key = '<phase>.<step>'."""
    phase, _, sid = step_key.partition(".")
    mod = steps_pkg.get_step(phase, sid)
    if mod is None:
        return None
    return (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")


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
      2. all steps in one automation share ONE fire_at_local (that's its fire time),
      3. every automation step declares fire_at_local,
      4. no step is owned by two automations,
      5. every scheduled step (declares fire_at_local) is owned by SOME automation."""
    problems = []
    defs = load_defs(config_path)
    owned = {}                       # step_key -> slug
    for d in defs:
        slug = d.get("slug", "?")
        s_steps = d.get("steps", []) or []
        if not s_steps:
            problems.append(f"automation '{slug}' has no steps")
            continue
        fires = set()
        for sk in s_steps:
            if sk in owned:
                problems.append(f"step '{sk}' is owned by two automations "
                                f"('{owned[sk]}' and '{slug}')")
            owned[sk] = slug
            mod = steps_pkg.get_step(*sk.split(".", 1)) if "." in sk else None
            if mod is None:
                problems.append(f"automation '{slug}' references unknown step '{sk}'")
                continue
            fire = (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")
            if not fire:
                problems.append(f"step '{sk}' (in '{slug}') declares no fire_at_local")
            else:
                fires.add(fire)
        if len(fires) > 1:
            problems.append(f"automation '{slug}' groups steps with different fire "
                            f"times {sorted(fires)} — split them")

    for sk in _all_scheduled_steps(config_path):
        if sk not in owned:
            problems.append(f"scheduled step '{sk}' (has fire_at_local) is not owned "
                            f"by any automation in automations.yaml")
    return problems


def _fmt_time_nl(hhmm: str) -> str:
    """'09:00' -> '9am', '12:00' -> '12pm', '13:30' -> '1:30pm' (for a Scout schedule)."""
    try:
        t = datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        return hhmm
    h12 = t.strftime("%I").lstrip("0") or "12"
    ampm = t.strftime("%p").lower()
    return f"{h12}{ampm}" if t.minute == 0 else f"{h12}:{t.strftime('%M')}{ampm}"


def _prompt_for(spec: dict, release: str) -> str:
    """A concrete instruction the automation runs. Scout resolves each step via
    step-action, executes the send/trigger, records it, and journals it."""
    step_list = ", ".join(spec["steps"])
    return (
        f"Release {release} — {spec['name'].format(release=release)}.\n"
        f"It is Code Complete Day. For EACH of these steps in order: {step_list} —\n"
        f"1. run `step-action --release {release} --phase {spec['phase']} --step <step>`;\n"
        f"2. execute the returned needs_skill action (send the email / post the Teams "
        f"message / trigger the pipeline) with the given payload;\n"
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
        fire_at = _step_fire_at(s_steps[0]) if s_steps else None
        name = d.get("name", slug).format(release=release)
        sched = (f"every {weekday.lower()} at {_fmt_time_nl(fire_at)}"
                 if weekday and fire_at else None)
        spec = {
            "slug": slug,
            "name": name,
            "phase": d.get("phase"),
            "steps": s_steps,
            "purpose": d.get("purpose", ""),
            "fire_at": fire_at,
            "ccd_date": ccd_date.isoformat() if ccd_date else None,
            "weekday": weekday,
            "schedule": sched,          # one-shot on the CCD date
            "one_shot": True,
        }
        spec["prompt"] = _prompt_for(spec, release)
        # Exactly what to record after creating it, so linkage is captured.
        spec["registration"] = {
            "name": name, "release": release, "purpose": d.get("purpose", ""),
            "steps": s_steps,
        }
        out.append(spec)
    return {"release": release, "ccd": ccd, "problems": problems, "automations": out}
