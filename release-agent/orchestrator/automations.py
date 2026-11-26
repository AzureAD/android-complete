"""Per-release unified automation planning + cleanup (`release-drainer`).

`config/automations.yaml` declares the optional per-release `release-drainer`
accelerator (`daily_window: "07:00-19:00/2h"`, `cleanup_when: release_done`).
This module validates definitions, compiles the Scout cron schedule + prompt,
and computes provisioning/cleanup plans without calling Scout APIs directly.
"""
from __future__ import annotations

import os
from datetime import datetime, time as _time, timezone

import yaml

import steps as steps_pkg
from orchestrator import delivery, schedule

_CLEANUP_RULES = {"release_done", "manual"}


def _cleanup_rules(value) -> list:
    return list(value) if isinstance(value, list) else [value]


def _valid_cleanup_rule(rule) -> bool:
    return isinstance(rule, str) and rule in _CLEANUP_RULES


def _parse_daily_window(value) -> tuple[int, int, int, int] | None:
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
    """`config/automations.yaml` sits next to `phases.yaml` (`config_path`)."""
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
    return [
        {**dict(item), "steps": list(item.get("steps") or ())}
        for item in _definition_document(config_path)["automations"]
    ]


def _provider_defaults(config_path: str) -> dict:
    defaults = _definition_document(config_path)["provider_defaults"]
    if not isinstance(defaults, dict) or set(defaults) != {
        "model", "enabled", "triggerType", "conditionCheckInterval", "browserHeadless", "teamsNotify",
    }:
        raise ValueError("Canonical provider_defaults require exactly the explicit supported flags")
    return defaults


def phase_label(config_path: str, phase_id: str) -> str:
    """Display name of a phase from `phases.yaml` (or `'Release-wide'` when unset)."""
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
    """Standard automation title: `'<release-id> · <scope> — <label>'`."""
    scope = (scope or "Release-wide").strip()
    return f"{release} · {scope} — {str(label).strip()}"


def fire_at(phase_id: str, step_id: str):
    """Public: the `fire_at_local` (`HH:MM`) a step declares, or `None`."""
    mod = steps_pkg.get_step(phase_id, step_id)
    if mod is None:
        return None
    return (getattr(mod, "CONFIG", {}) or {}).get("fire_at_local")


def validate(config_path: str) -> list:
    """Validate `config/automations.yaml` definitions (returns list of problem strings)."""
    problems = []
    defs = load_defs(config_path)
    slugs = set()
    allowed_keys = {
        "slug", "label", "phase", "scope", "steps",
        "daily_window", "cleanup_when", "purpose", "prompt_kind",
    }
    for d in defs:
        slug = d.get("slug", "?")
        if slug in slugs:
            problems.append(f"duplicate automation slug '{slug}'")
        slugs.add(slug)
        if set(d) - allowed_keys:
            problems.append(f"automation '{slug}' has unknown settings")
        if _parse_daily_window(d.get("daily_window")) is None:
            problems.append(
                f"automation '{slug}' has invalid/missing daily_window {d.get('daily_window')!r} (expected HH:MM-HH:MM/<N>h)"
            )
        if d.get("prompt_kind") != "release-drainer":
            problems.append(f"automation '{slug}' must use prompt_kind 'release-drainer'")
        rules = _cleanup_rules(d.get("cleanup_when"))
        if not rules or not all(_valid_cleanup_rule(rule) for rule in rules):
            problems.append(f"automation '{slug}' has invalid/missing cleanup_when")
    return problems


def daily_window_slots(window_spec: str = "07:00-19:00/2h") -> tuple[tuple[int, int], ...]:
    """Return ordered `(hour, minute)` local slot tuples for a `'HH:MM-HH:MM/<N>h'` window."""
    parsed = _parse_daily_window(window_spec)
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
    parsed = _parse_daily_window(window_spec)
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
    ref_utc = now if (now is not None and now.tzinfo is not None) else datetime.now(timezone.utc)
    ref_date = ref_utc.astimezone(owner).date()
    host_pts = [
        datetime.combine(ref_date, _time(h, minute), owner).astimezone(host)
        for h in range(start_h, end_h + 1, step_h)
    ]
    mins = sorted({dt.minute for dt in host_pts})
    hrs = sorted({dt.hour for dt in host_pts})
    return f"cron: {','.join(map(str, mins))} {','.join(map(str, hrs))} * * *"


def _prompt_for(release: str) -> str:
    """Build the unified `release-drainer` instruction prompt."""
    cleanup = (
        f"\nFinally run `automation cleanup --release {release} --json`. For each removal "
        f"IN ORDER, claim it with `automation claim-delete --id <id> --executor <session> "
        f"--json`; call `m_delete_automation` only when permission_to_delete is true, then "
        f"record `automation delete-result --id <id> --attempt-id <attempt> --outcome "
        f"deleted|not_deleted|uncertain --evidence <provider evidence>`. Never delete or "
        f"deregister without a durable claim. STOP the cleanup loop on any barrier, "
        f"error or uncertain deletion; retain the recovery worker. Terminal ID-less "
        f"intents need owner-reviewed abandon-prepared with fresh absence evidence. "
        f"This is a finally block: execute it even on silence, stopped work, or errors. "
        f"Halts suspend, not delete."
    )
    protocol = (
        "Before every mutating or scheduled release command, pass the global --scheduled flag before its "
        "subcommand so execution serializes cleanly on `<runs-root>/<release>/.state.lock` with any "
        "concurrent interactive session. Attempt each operation at most once per unchanged progress "
        "snapshot; no progress returns a concrete wait/input/failure instead of another loop.\n"
        + delivery.PROTOCOL.replace("<release>", release)
        + "\nFor every release-advancing task, inspect work_dispatch after each result. "
        "A sibling failure/owner gate does not cancel independent pending_work. "
        "Drain only eligible non-approval work through its owning protocol; never bypass scope, consent, "
        "dependency/time guards or unknown-write recovery. Before finishing, run "
        f"`status --release {release} --handoff --json --no-pipeline-check`. "
        "For a Scout task genuinely needing human approval (`requires_approval: true`), input, or "
        "unresolved recovery, use --hold-step phase.step=<actual-reason> on that read-only guard after "
        "independent non-approval work is exhausted so the release parks cleanly at the human gate. "
        "Never resurrect or recreate automations that the owner disabled or deleted."
    )
    return (
        f"Release {release} — Unified Release Drainer (runs every 2h from 7:00 AM to 7:00 PM).\n"
        f"1. Run `python -m orchestrator.cli --scheduled drain --release {release} --trigger scheduled --deliver --json`.\n"
        "   - Python automatically drains all eligible Python `auto` steps, reconciles `in_flight` / `read_only` "
        "steps, and delivers `requires_approval: false` notification steps silently via Agency MCP "
        "(NEVER call `workiq_send_*` or `m_ask_user`).\n"
        "2. Inspect `pending_host_work` in the JSON output IN ORDER:\n"
        "   - For `scope: \"readiness\"` or `scope: \"phase\"` items (e.g. Entry Gate Scout auto checks, "
        "`preflight.lockdown` Playwright CCOA scrape, or `ccd.localization` via "
        "`launch-localization --execute --auto-approve`), execute each non-approval item ONCE via its "
        "owning command. Never start `requires_approval: true` writes.\n"
        "   - For `scope: \"ai_diagnosis\"` items (deep pre-CTA root-cause investigation for blocked steps or "
        "RC test failures):\n"
        "     a) Run `diagnose_command` (`diagnose --json`), which extracts the build IDs from links/state, "
        "fetches the ADO `/timeline` (`root_failed_stages`, `failed_tasks`, `log_excerpt`), follows any child "
        "pipelines queued via `queue-build.ps1` (`child_builds`), and loads matching `AuthClientAndroidPipelines/` "
        "YAML templates (`matched_yaml_snippets`).\n"
        "     b) If `timeline_fetched` is false or task logs need deeper lines, query ADO directly (READ-ONLY) "
        "via ADO MCP (`pipelines_build` `action='get_status'` + `pipelines_build_log` `action='list'`/`'get_content'` "
        "on `project` + `build_id`, following any child build IDs in `queue-build.ps1` logs!). Cross-check the "
        "referenced `AuthClientAndroidPipelines/` YAML/script or recent branch commits when relevant, and note "
        "if sibling steps (`mrwp_ecs` + `mrwp_local`) failed on the same upstream stage.\n"
        "     c) **ANTI-REDUNDANCY RULE:** `record-diagnosis` REJECTS parroted summaries (`'<Step> is blocked'` or "
        "`'15 stages never ran — review failed stage outputs'`). Your `--headline` MUST name the exact failed "
        "stage/task/child-build or exception, `--root-cause` MUST explain WHY it failed citing the log error, and "
        "`--action` MUST state the concrete fix (e.g. `Retry stage '<StageName>' in build #<id>` if transient "
        "infra/timeout vs the exact branch/version/feed/secret fix). Never retry builds, mutate branches, or "
        "approve gates during diagnosis.\n"
        "   - For `scope: \"scout_notification\"` items (e.g. `build_verify.rc_report_notify`): run `command` "
        "(`deliver-step --json`, which sends any companion HTML email via Agency MCP and returns `scout_bot_delivery`), "
        "then run `scout_bot_delivery.claim_command`, call `m_send_teams_message` when `permission_to_send: true`, "
        "and run `scout_bot_delivery.ack_sent_command`.\n"
        "   - After finishing `pending_host_work`, finalize the journaled run with "
        f"`python -m orchestrator.cli --scheduled drain --release {release} --finalize-run <run_id> --deliver --json` "
        "(which runs any newly unlocked steps, writes `<runs-root>/<release>/drain-runs.jsonl`, and delivers CTA "
        "notifications WITH the recorded AI root-cause diagnosis embedded).\n"
        "3. **Deliver Scout Bot Teams Call-to-Action (`scout_bot_cta`):**\n"
        "   - ALWAYS inspect `scout_bot_cta` on the finalized drain payload:\n"
        "   - When `scout_bot_cta` is non-null (`action_required: true`), run its exact `claim_command` "
        f"(`python -m orchestrator.cli --scheduled notification claim --release {release} --id <notification_id> "
        "--hash <hash> --executor release-drainer --json`).\n"
        "   - When that `claim` returns `permission_to_send: true`, IMMEDIATELY call `m_send_teams_message` "
        "with `message = scout_bot_cta.message` so the release owner receives the Scout Teams bot DM, then run "
        "`scout_bot_cta.ack_sent_command` (substituting `<execution_id>` from `claim`).\n"
        "   - NOTE: On `notification prepare --source pending`, `permission_to_send: false` is expected because "
        "`prepare` is a preview — for any `delivery_status: \"prepared\"` or `\"not_sent\"` `m_send_teams_message` "
        "record, run `notification claim` (which grants `permission_to_send: true`), call `m_send_teams_message`, "
        "and record `notification result --outcome sent`!\n"
        + protocol + cleanup
    )


def plan(config_path: str, release: str, ccd: str, *, owner_timezone=None,
         scheduler_timezone=None, now=None) -> dict:
    """Concrete provisioning specs for a release (`{release, ccd, problems, automations:[...]}`)."""
    problems = validate(config_path)
    defs = load_defs(config_path)
    defaults = _provider_defaults(config_path)
    scheduler_timezone = scheduler_timezone or schedule.detect_local_tz()
    ccd_date = schedule.parse_date(ccd) if ccd else None
    weekday = ccd_date.strftime("%A") if ccd_date else None
    agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    out = []
    for d in defs:
        entry_problems = []
        slug = d.get("slug", "?")
        s_steps = d.get("steps", []) or []
        daily_window = d.get("daily_window")
        label = d.get("label", slug)
        scope = d.get("scope") or phase_label(config_path, d.get("phase"))
        name = automation_name(release, scope, label)
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

        prompt = (
            f"Work from the authoritative release-agent root: {agent_root}. "
            "All command fragments below mean `python -m orchestrator.cli` in this root. "
            "Use ONE authoritative runs directory, never a copied release snapshot.\n"
            "Automations are optional non-blocking accelerators: never recreate or re-enable "
            "automations that the owner disabled or deleted, never repeat uncertain creation/deletion, "
            "and never start `requires_approval: true` writes without live owner consent.\n"
            + _prompt_for(release)
        )
        from orchestrator.registry import provider_spec
        prov_spec = None
        if sched:
            try:
                prov_spec = provider_spec({
                    **defaults, "name": name, "description": d.get("purpose", ""),
                    "prompt": prompt, "schedule": sched, "oneShot": False,
                })
            except ValueError as exc:
                entry_problems.append(str(exc))
        spec = {
            "slug": slug,
            "name": name,
            "phase": d.get("phase"),
            "steps": s_steps,
            "kind": "release-level",
            "prompt_kind": d.get("prompt_kind"),
            "purpose": d.get("purpose", ""),
            "fire_at": daily_window,
            "daily_window": daily_window,
            "ccd_date": ccd_date.isoformat() if ccd_date else None,
            "weekday": weekday,
            "schedule": sched,
            "one_shot": False,
            "optional": True,
            "cleanup_when": d.get("cleanup_when"),
            "prompt": prompt,
            "provider_spec": prov_spec,
            "problems": entry_problems,
            "registration": {
                "name": name, "release": release, "purpose": d.get("purpose", ""),
                "steps": s_steps, "kind": "release-level", "schedule": sched, "slug": slug,
                "cleanup_when": d.get("cleanup_when"),
            },
        }
        out.append(spec)
    return {"release": release, "ccd": ccd, "problems": problems, "automations": out,
            "owner_timezone": owner_timezone, "scheduler_timezone": scheduler_timezone}


def ensure_reason(state, spec, config_path, selection=None, *, ignore_holds=False):
    """Why a declared optional accelerator is not eligible now; no provider calls or mutations."""
    if selection is None:
        from orchestrator.engine import Orchestrator
        selection = Orchestrator(config_path, state, step_defaults={}).scheduling()
    if selection.frontier is None or state.cancellation:
        return "The release is terminal; do not recreate its automations"
    if not ignore_holds and selection.suspension in ("halted", "denied", "blocked"):
        return "The release has an explicit hold; reconcile receipts but do not create workers"
    if "manual" in _cleanup_rules(spec["cleanup_when"]):
        return "Manual workers are not automatically ensured"
    target = {**spec["registration"], "id": None, "scope": "release", "status": "active"}
    cleanup = cleanup_plan(state, [target], config_path)
    if cleanup["problems"]:
        return "; ".join(cleanup["problems"])
    if cleanup["removals"]:
        return "The worker's objective is already complete; do not recreate it"
    return ""


def provisioning_obligations(state, entries: list[dict], config_path: str) -> dict:
    """Return optional background accelerators and any unresolved registry recoveries."""
    planned = plan(
        config_path, state.release_id, state.ccd, owner_timezone=state.timezone)
    by_slug = {
        entry.get("slug"): entry for entry in entries
        if entry.get("release") == state.release_id and entry.get("scope", "release") == "release"
    }
    from orchestrator.engine import Orchestrator
    selection = Orchestrator(config_path, state, step_defaults={}).scheduling()
    terminal = selection.frontier is None or bool(state.cancellation)
    optional, recoveries, active, ensure = [], [], [], []
    problems = list(planned["problems"])
    duplicates = {
        slug for slug in by_slug
        if sum(entry.get("slug") == slug and entry.get("release") == state.release_id
               and entry.get("scope", "release") == "release" for entry in entries) > 1
    }
    for spec in planned["automations"]:
        if terminal:
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
        if existing is None or existing.get("status") in ("absent", "deleted", "abandoned"):
            if not spec["problems"]:
                optional.append(spec)
        elif existing.get("status") == "active" and existing.get("id"):
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
        "required": [],
        "optional": optional,
        "recoveries": recoveries,
        "active": active,
        "problems": problems,
        "ensure": ensure,
    }


def cleanup_plan(state, entries: list, config_path: str, *, now=None) -> dict:
    """Decide which registered automations reached their declared lifecycle end."""
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(config_path, state, step_defaults={}, now=now)
    selection = orch.scheduling()
    release_terminal = selection.frontier is None or selection.status == "cancelled"
    terminal_reason = (
        "release cancelled"
        if selection.status == "cancelled"
        else "release complete"
    )
    removals, problems = [], []

    for entry in entries:
        rules = _cleanup_rules(entry.get("cleanup_when"))
        if "manual" in rules or entry.get("scope") == "shared":
            continue
        if entry.get("scope") != "release" or entry.get("release") != state.release_id:
            problems.append(f"{entry.get('id')}: explicit release scope required; owner recovery needed")
            continue
        if release_terminal:
            removals.append({
                "id": entry["id"], "name": entry["name"], "slug": entry.get("slug"),
                "cleanup_when": entry.get("cleanup_when"),
                "reason": f"{terminal_reason} (universal backstop)",
            })
            continue
        if selection.status == "halted":
            continue
        if not rules or not all(_valid_cleanup_rule(r) for r in rules):
            problems.append(f"{entry.get('id')}: invalid/missing cleanup_when; owner recovery required")
            continue
    removals.sort(key=lambda r: r["name"])
    return {"release": state.release_id, "removals": removals, "problems": problems}
