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
    accepts (e.g. 'cron: 0 9 26 8 *') or None if inputs are missing/invalid."""
    if not ccd_date or not hhmm:
        return None
    try:
        t = datetime.strptime(hhmm, "%H:%M")
    except ValueError:
        return None
    return f"cron: {t.minute} {t.hour} {ccd_date.day} {ccd_date.month} *"


def _prompt_for(spec: dict, release: str) -> str:
    """A concrete instruction the automation runs. Scout resolves each step via
    step-action, executes the send/trigger, records it, and journals it. The
    localization trigger and its poller have bespoke prompts (they don't just
    record-step done)."""
    step_list = ", ".join(spec["steps"])
    steps = spec.get("steps") or []

    # Localization poller (interval) — poll the in-flight run.
    if spec.get("interval") and steps == ["ccd.localization"]:
        return (
            f"Release {release} — localization poller.\n"
            f"If localization for {release} is in-flight (it was triggered at noon and "
            f"isn't done/blocked yet), poll it once. The ADO MCP can't reach "
            f"msazure/One, so read via az (build id is stored on the step):\n"
            f"1. status: `az pipelines build show --id <buildId> "
            f"--org https://msazure.visualstudio.com --project One "
            f"--query \"{{status:status,result:result}}\" -o json`.\n"
            f"2. if completed, find the OneLocBuild@3 log id: `az devops invoke "
            f"--org https://msazure.visualstudio.com --area build --resource timeline "
            f"--route-parameters project=One buildId=<buildId> --api-version 7.1 "
            f"--query \"records[?name=='OneLocBuild@3'].log.id | [0]\" -o tsv`, then read "
            f"it: `az devops invoke --org https://msazure.visualstudio.com --area build "
            f"--resource logs --route-parameters project=One buildId=<buildId> "
            f"logId=<logId> --api-version 7.1`.\n"
            f"3. run `check-localization --release {release} --complete <true|false> "
            f"[--logs \"<OneLocBuild@3 log>\"]`.\n"
            f"4. act on the printed decision: `timeout` → send the given email; "
            f"`complete_pr` → post the given chat message to the Code reviews chat; "
            f"`wait`/`complete_none`/`not_started`/`already_final` → nothing to send.\n"
            f"Silently journal: `journal --release {release} --source scout --kind "
            f"automation --text \"localization-poller: <decision>\"`. Stay silent if "
            f"there is nothing to do.")

    # Localization trigger (one-shot, noon) — trigger then hand off to the poller.
    if not spec.get("interval") and steps == ["ccd.localization"]:
        return (
            f"Release {release} — trigger localization.\n"
            f"1. run `step-action --release {release} --phase ccd --step localization`;\n"
            f"2. run the returned needs_skill action to start pipeline 405133 "
            f"(isCreatePrSelected=true); note the queued build id;\n"
            f"3. run `record-localization-run --release {release} --build-id <buildId>` "
            f"— this leaves the step IN-FLIGHT (do NOT record-step done; the poller "
            f"finishes it once the run completes or times out);\n"
            f"4. silently journal: `journal --release {release} --source scout --kind "
            f"automation --text \"ccd-noon triggered ccd.localization\"`.")

    # Default: send/trigger + record-step done (reminders).
    return (
        f"Release {release} — {spec['name'].format(release=release)}.\n"
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
        name = d.get("name", slug).format(release=release)
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
