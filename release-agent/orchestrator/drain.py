# Copyright (c) Microsoft Corporation. All rights reserved.
"""Unified Release Drainer, structured `drain-runs.jsonl` journal, and CTA pacing engine."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from html import escape
import hashlib
import json
import os
import time as _monotonic
import uuid
from typing import Any, Optional

from orchestrator import automations as auto_plan
from orchestrator import cli_common as C
from orchestrator import delivery as D
from orchestrator import notifications as notif
from orchestrator import schedule
from orchestrator.agency_mcp import MCPCaller
from orchestrator.effects import EffectMode
from orchestrator.workflow import StepKind

DRAIN_SCHEMA_VERSION = 1
DEFAULT_WINDOW_SPEC = "07:00-19:00/2h"


def journal_path(runs_root: str, release: str) -> str:
    return os.path.join(os.path.dirname(C.state_path(runs_root, release)), "drain-runs.jsonl")


def active_run_path(runs_root: str, release: str) -> str:
    return os.path.join(os.path.dirname(C.state_path(runs_root, release)), "drain-active.json")


def read_drain_runs(runs_root: str, release: str, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
    path = journal_path(runs_root, release)
    if not os.path.isfile(path):
        return []
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            txt = line.strip()
            if not txt:
                continue
            try:
                obj = json.loads(txt)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    if limit is not None and limit >= 0:
        return records[-limit:] if limit > 0 else []
    return records


def _append_drain_record(runs_root: str, release: str, record: dict[str, Any]) -> None:
    path = journal_path(runs_root, release)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_active_run(runs_root: str, release: str, data: dict[str, Any]) -> None:
    path = active_run_path(runs_root, release)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _pop_active_run(runs_root: str, release: str) -> Optional[dict[str, Any]]:
    path = active_run_path(runs_root, release)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    try:
        os.unlink(path)
    except OSError:
        pass
    return data if isinstance(data, dict) else None


def _slot_datetimes_between(
    start_local: datetime,
    end_local: datetime,
    *,
    window_spec: str = DEFAULT_WINDOW_SPEC,
) -> list[datetime]:
    slots_hm = auto_plan.daily_window_slots(window_spec)
    tz = end_local.tzinfo or start_local.tzinfo
    out: list[datetime] = []
    cur_d = start_local.date() - timedelta(days=1)
    end_d = end_local.date() + timedelta(days=1)
    while cur_d <= end_d:
        for h, m in slots_hm:
            dt = datetime.combine(cur_d, time(h, m), tz)
            if start_local <= dt <= end_local:
                out.append(dt)
        cur_d += timedelta(days=1)
    return out


def compute_slot_status(
    state,
    prior_runs: list[dict[str, Any]],
    now_local: datetime,
    *,
    window_spec: str = DEFAULT_WINDOW_SPEC,
    trigger: str = "scheduled",
) -> tuple[Optional[str], list[str]]:
    """Compute `(expected_slot_iso, missed_slot_isos)` on the owner's wall clock."""
    tz = now_local.tzinfo or schedule.get_tz(state.timezone)
    slots_hm = auto_plan.daily_window_slots(window_spec)

    # Identify the slot matching `now_local` (within [-15m, +105m] of a slot start).
    expected_dt: Optional[datetime] = None
    if trigger == "scheduled":
        candidates: list[datetime] = []
        for day_offset in (-1, 0, 1):
            d = now_local.date() + timedelta(days=day_offset)
            for h, m in slots_hm:
                candidates.append(datetime.combine(d, time(h, m), tz))
        matching = [
            dt for dt in candidates
            if timedelta(minutes=-15) <= (now_local - dt) <= timedelta(minutes=105)
        ]
        if matching:
            expected_dt = min(matching, key=lambda dt: abs((now_local - dt).total_seconds()))

    scheduled_runs = [r for r in prior_runs if r.get("trigger") == "scheduled"]
    covered_instants: list[datetime] = []
    for r in scheduled_runs:
        for field_name in ("expected_slot", "started_at"):
            dt = schedule.parse_iso_utc(r.get(field_name))
            if dt is not None:
                covered_instants.append(dt.astimezone(tz) if tz else dt)

    if scheduled_runs:
        last_dt = schedule.parse_iso_utc(
            scheduled_runs[-1].get("started_at") or scheduled_runs[-1].get("finished_at")
        )
        anchor_local = last_dt.astimezone(tz) if (last_dt and tz) else now_local
    else:
        created_dt = schedule.parse_iso_utc(getattr(state, "created_at", None))
        anchor_local = created_dt.astimezone(tz) if (created_dt and tz) else now_local

    upper_bound = (expected_dt - timedelta(minutes=20)) if expected_dt else (now_local - timedelta(minutes=20))
    lower_bound = anchor_local + timedelta(minutes=20)
    if upper_bound < lower_bound:
        return (expected_dt.isoformat() if expected_dt else None), []

    missed: list[str] = []
    for slot_dt in _slot_datetimes_between(lower_bound, upper_bound, window_spec=window_spec):
        if any(abs((seen - slot_dt).total_seconds()) <= 45 * 60 for seen in covered_instants):
            continue
        missed.append(slot_dt.isoformat())
    return (expected_dt.isoformat() if expected_dt else None), missed


def _status_map(orch) -> dict[str, tuple[str, str]]:
    """Map every readiness item + workflow step to `(status, note)` for transition diffing."""
    out: dict[str, tuple[str, str]] = {}
    for item_id, rec in (orch.state.readiness_items or {}).items():
        if isinstance(rec, dict):
            out[f"readiness.{item_id}"] = (str(rec.get("status") or "pending"), str(rec.get("message") or ""))
    for phase in orch.workflow.phases:
        for step in phase.steps:
            rec = orch.state.get_step(phase.id, step.id)
            out[step.key] = (rec.status, rec.note or "")
    return out


def _step_friendly_names(orch) -> dict[str, str]:
    """Map `<phase>.<step>` and `readiness.<id>` keys to human-friendly titles."""
    names: dict[str, str] = {}
    chk = orch.gate.checklist()
    for it in chk.get("items", []):
        names[f"readiness.{it['id']}"] = it.get("label") or it.get("text") or it["id"]
    for phase in orch.workflow.phases:
        for step in phase.steps:
            names[step.key] = step.name
    return names


def capture_snapshot(orch) -> dict[str, Any]:
    selection = orch.scheduling()
    frontier = selection.frontier
    included_steps = [
        item for item in selection.steps
        if selection.phase(item.definition.phase_id).included
    ]
    done = sum(1 for item in included_steps if item.complete)
    total = len(included_steps)
    if not orch.gate.signed:
        chk = orch.gate.checklist()
        r_items = chk.get("items", [])
        active_steps = [
            f"readiness.{it['id']}"
            for it in r_items
            if not it.get("satisfied")
        ]
        phase_id: Optional[str] = "readiness"
        phase_name = "Entry Gate (Readiness)"
        phase_done = sum(1 for it in r_items if it.get("satisfied"))
        phase_total = len(r_items)
        phase_steps = [
            {
                "step": f"readiness.{it['id']}",
                "name": it.get("label") or it.get("text") or it["id"],
                "status": (
                    "done" if it.get("satisfied")
                    else "blocked" if it.get("status") in ("fail", "unable", "needs_input")
                    else "human_input" if it.get("verify") != "auto"
                    else "pending"
                ),
            }
            for it in r_items
        ]
    elif frontier is not None:
        f_steps = [
            item for item in selection.steps
            if item.definition.phase_id == frontier.id
        ]
        active_steps = [
            item.definition.key
            for item in f_steps
            if not item.complete and item.prerequisites_met
        ]
        phase_id = frontier.id
        phase_name = frontier.name
        phase_done = sum(1 for item in f_steps if item.complete)
        phase_total = len(f_steps)
        phase_steps = []
        for item in f_steps:
            step_def = item.definition
            rec = orch.state.get_step(step_def.phase_id, step_def.id)
            if item.complete:
                st_label = "skipped" if rec.status == "skipped" else "done"
            elif rec.status == "blocked":
                st_label = "blocked"
            elif rec.status == "in_flight":
                st_label = "in_flight"
            elif item.prerequisites_met and not item.time_ready:
                st_label = "scheduled"
            elif item.prerequisites_met and (
                step_def.is_gate
                or step_def.is_human_action
                or (step_def.kind == StepKind.EXTERNAL and step_def.requires_approval)
            ):
                st_label = "human_input"
            else:
                st_label = "pending"
            phase_steps.append({
                "step": step_def.key,
                "name": step_def.name,
                "status": st_label,
            })
    else:
        active_steps = []
        phase_id = None
        phase_name = "Complete"
        phase_done = total
        phase_total = total
        phase_steps = []
    return {
        "lifecycle": selection.status,
        "phase": phase_id,
        "phase_name": phase_name,
        "phase_done": phase_done,
        "phase_total": phase_total,
        "phase_steps": phase_steps,
        "active_steps": active_steps,
        "done": done,
        "total": total,
    }


def _diff_executed_steps(
    before: dict[str, tuple[str, str]],
    after: dict[str, tuple[str, str]],
    names: Optional[dict[str, str]] = None,
) -> list[dict[str, str]]:
    changed: list[dict[str, str]] = []
    name_map = names or {}
    for key, (after_status, after_note) in after.items():
        before_status, before_note = before.get(key, ("pending", ""))
        if (before_status, before_note) != (after_status, after_note):
            changed.append({
                "step": key,
                "name": name_map.get(key) or key,
                "from_status": before_status,
                "to_status": after_status,
                "note": after_note,
            })
    return changed


def _collect_pending_host_work(orch) -> list[dict[str, Any]]:
    """Return `requires_approval: false` Scout/MCP host tasks remaining in this drain turn."""
    if orch.state.halt or orch.state.cancellation:
        return []
    if not orch.gate.signed:
        chk = orch.gate.checklist()
        pending = (chk.get("dispatch") or {}).get("pending_auto_checks") or []
        return [
            {
                "scope": "readiness",
                "step": f"readiness.{item['id']}",
                "id": item["id"],
                "name": item["id"],
                "source": item.get("source"),
                "verifier": item.get("verifier"),
                "probe": item.get("probe"),
                "identity_probe": item.get("identity_probe"),
                "instructions": item.get("instructions"),
                "requires_approval": False,
                "command": item.get("record_command"),
                "input_command": item.get("input_command"),
            }
            for item in pending
            if item.get("source") == "scout"
        ]
    rep = orch.status_report()
    pending_work = (rep.get("work_dispatch") or {}).get("pending_work") or []
    return [
        {
            "scope": "phase",
            "step": item.get("key") or f"{item.get('phase')}.{item.get('step')}",
            "phase": item.get("phase"),
            "step_id": item.get("step"),
            "name": item.get("name") or item.get("step"),
            "requires_approval": False,
            "command": item.get("command"),
        }
        for item in pending_work
        if not item.get("requires_approval") and not item.get("outbound_notification")
    ]


def _waiting_minutes(since_iso: Optional[str], now_utc: datetime) -> Optional[int]:
    parsed = schedule.parse_iso_utc(since_iso)
    if parsed is None:
        return None
    return max(0, int((now_utc - parsed.astimezone(timezone.utc)).total_seconds() // 60))


def classify_waiting(orch, now_utc: datetime) -> dict[str, Any]:
    """Classify all currently eligible waiting items into `blocked`, `human_input`, `in_flight`, `unavailable`."""
    items: list[dict[str, Any]] = []
    selection = orch.scheduling()

    if selection.status in ("complete", "cancelled"):
        return {
            "blocked_count": 0,
            "human_input_count": 0,
            "in_flight_count": 0,
            "unavailable_count": 0,
            "items": [],
        }

    if not orch.gate.signed:
        chk = orch.gate.checklist()
        for it in chk.get("items", []):
            if it.get("satisfied"):
                continue
            raw_rec = (orch.state.readiness_items or {}).get(it["id"]) or {}
            since = raw_rec.get("at") or orch.state.created_at
            status = it.get("status")
            label = it.get("label") or it.get("text") or it["id"]
            links = list(it.get("links") or [])
            if it.get("verify") == "auto":
                if status in ("fail", "blocked", "needs_input", "unable"):
                    items.append({
                        "step": f"readiness.{it['id']}",
                        "name": label,
                        "category": "blocked",
                        "reason": it.get("message") or f"Readiness check '{it['id']}' ({status}) needs owner attention.",
                        "waiting_since": since,
                        "waiting_minutes": _waiting_minutes(since, now_utc),
                        "available_at": None,
                        "links": links,
                    })
            else:
                is_declined = status == "unable"
                items.append({
                    "step": f"readiness.{it['id']}",
                    "name": label,
                    "category": "blocked" if is_declined else "human_input",
                    "reason": (
                        f"Declined by owner: {it.get('message') or 'blocked'}"
                        if is_declined
                        else (it.get("message") or "Awaiting release owner readiness attestation.")
                    ),
                    "waiting_since": since,
                    "waiting_minutes": _waiting_minutes(since, now_utc),
                    "available_at": None,
                    "links": links,
                })
    elif selection.frontier is not None:
        frontier = selection.frontier
        f_ready = selection.phase(frontier.id)
        if not f_ready.due:
            first_step = frontier.steps[0]
            items.append({
                "step": first_step.key,
                "name": f"{frontier.name} ({first_step.name})",
                "category": "unavailable",
                "reason": f_ready.unavailable_reason or f"Not available before {f_ready.available_at}",
                "waiting_since": orch.state.updated_at,
                "waiting_minutes": _waiting_minutes(orch.state.updated_at, now_utc),
                "available_at": f_ready.available_at,
                "links": [],
            })
        else:
            for s_ready in selection.steps:
                step = s_ready.definition
                if step.phase_id != frontier.id or s_ready.complete or not s_ready.prerequisites_met:
                    continue
                rec = orch.state.get_step(step.phase_id, step.id)
                links = list(rec.links or [])
                since = rec.completed_at or (rec.execution or {}).get("started_at") or orch.state.updated_at
                gate_denied = bool(
                    step.is_gate
                    and (orch._projection().latest_gate_decision(step) or {}).get("decision") == "denied"
                )
                if rec.status == "blocked" or gate_denied:
                    reason = rec.note or (
                        (orch._projection().latest_gate_decision(step) or {}).get("comment")
                        if gate_denied else "Step is blocked and requires owner attention."
                    )
                    items.append({
                        "step": step.key,
                        "name": step.name,
                        "category": "blocked",
                        "reason": reason,
                        "waiting_since": since,
                        "waiting_minutes": _waiting_minutes(since, now_utc),
                        "available_at": None,
                        "links": links,
                    })
                elif not s_ready.time_ready:
                    items.append({
                        "step": step.key,
                        "name": step.name,
                        "category": "unavailable",
                        "reason": s_ready.unavailable_reason or f"Scheduled for {s_ready.available_at}",
                        "waiting_since": since,
                        "waiting_minutes": _waiting_minutes(since, now_utc),
                        "available_at": s_ready.available_at,
                        "links": links,
                    })
                elif rec.status == "in_flight":
                    items.append({
                        "step": step.key,
                        "name": step.name,
                        "category": "in_flight",
                        "reason": rec.note or "Async pipeline/operation is still running.",
                        "waiting_since": since,
                        "waiting_minutes": _waiting_minutes(since, now_utc),
                        "available_at": None,
                        "links": links,
                    })
                elif step.is_gate or step.is_human_action or (step.kind == StepKind.EXTERNAL and step.requires_approval):
                    default_reason = (
                        "Awaiting release owner approval decision."
                        if step.is_gate
                        else "Awaiting release owner confirmation/input."
                    )
                    items.append({
                        "step": step.key,
                        "name": step.name,
                        "category": "human_input",
                        "reason": rec.note or default_reason,
                        "waiting_since": since,
                        "waiting_minutes": _waiting_minutes(since, now_utc),
                        "available_at": None,
                        "links": links,
                    })

    return {
        "blocked_count": sum(1 for i in items if i["category"] == "blocked"),
        "human_input_count": sum(1 for i in items if i["category"] == "human_input"),
        "in_flight_count": sum(1 for i in items if i["category"] == "in_flight"),
        "unavailable_count": sum(1 for i in items if i["category"] == "unavailable"),
        "items": items,
    }


def detect_external_cutoff(orch) -> Optional[str]:
    """Check true external calendar cutoffs only (never rigid per-phase target dates)."""
    selection = orch.scheduling()
    frontier_id = selection.frontier.id if selection.frontier else None
    ccd_date = schedule.parse_date(orch.state.ccd)
    if ccd_date is not None:
        if (not orch.gate.signed or frontier_id == "preflight") and (
            orch.as_of >= schedule.previous_business_day(ccd_date)
        ):
            return f"Code Complete Day ({ccd_date.isoformat()})"
        if frontier_id == "ccd" and (
            orch.as_of > ccd_date
            or (orch.as_of == ccd_date and orch.now_local.hour >= 15)
        ):
            return f"CCD 11:00 PM branch cut ({ccd_date.isoformat()} 23:00)"

    rollout_done = selection.phase("rollout_start").complete if orch.workflow.phase("rollout_start") else True
    if not rollout_done:
        lockdown_data = orch.state.get_step("preflight", "lockdown").data or {}
        candidates: list[date] = []
        for key in ("next_freeze_start", "freeze_start"):
            d = schedule.parse_date(lockdown_data.get(key))
            if d is not None:
                candidates.append(d)
        for coll_key in ("overlaps", "windows", "upcoming_windows"):
            for entry in lockdown_data.get(coll_key) or ():
                if isinstance(entry, dict):
                    d = schedule.parse_date(entry.get("start") or entry.get("start_date"))
                    if d is not None:
                        candidates.append(d)
        for freeze_d in sorted(set(candidates)):
            bdays = schedule.business_days_until(orch.as_of, freeze_d)
            if 0 <= bdays <= 2:
                return f"Upcoming CCOA freeze ({freeze_d.isoformat()})"
    return None


def _actionable_waiting_items(waiting: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in waiting.get("items") or []:
        if item["category"] in ("blocked", "human_input"):
            out.append(item)
        elif item["category"] == "in_flight" and (item.get("waiting_minutes") or 0) >= 240:
            out.append({
                **item,
                "category": "blocked",
                "reason": f"In-flight for {item['waiting_minutes']}m (4h+ stall threshold): {item['reason']}",
            })
    return out


def _hold_signature(actionable: list[dict[str, Any]]) -> str:
    canonical = sorted((item["step"], item["category"]) for item in actionable)
    digest = hashlib.sha256(
        json.dumps(canonical, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _format_duration_minutes(minutes: Optional[int]) -> str:
    if minutes is None or minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h {rem}m" if rem else f"{hours}h"


def render_cta_messages(
    state,
    snapshot: dict[str, Any],
    executed_steps: list[dict[str, str]],
    actionable: list[dict[str, Any]],
    risk: dict[str, Any],
) -> tuple[str, str, str]:
    """Return `(subject, scout_markdown, email_html)` for a Release Drainer Call-to-Action.

    Uses phase-scoped progress (`3 of 4 phase steps complete` + current-phase checklist)
    and human-friendly step names everywhere (never raw `<phase>.<step>` IDs).
    """
    level = risk["level"]
    badge_map = {
        "cutoff_at_risk": "🔴 Cutoff At Risk",
        "stalled": "🔴 Stalled — Needs Owner",
        "attention": "🟡 Waiting 1d+ — Needs Attention",
        "normal": "🔵 Action Needed",
    }
    badge = badge_map.get(level, "🔵 Action Needed")
    phase_label = snapshot.get("phase_name") or snapshot.get("phase") or "Release"
    p_done = snapshot.get("phase_done", snapshot.get("done", 0))
    p_total = snapshot.get("phase_total", snapshot.get("total", 0))
    phase_progress = f"{p_done} of {p_total} phase steps complete"
    subject = f"[{badge}] Release {state.release_id} — {phase_label} ({p_done}/{p_total} done · {len(actionable)} pending)"

    blocked_items = [i for i in actionable if i["category"] == "blocked"]
    human_items = [i for i in actionable if i["category"] == "human_input"]
    completed_this_run = [
        s for s in executed_steps
        if s.get("to_status") in ("done", "skipped", "pass", "attested")
    ]
    phase_steps = snapshot.get("phase_steps") or []

    status_icon_map = {
        "done": ("✅", "Done"),
        "skipped": ("⏭️", "Skipped"),
        "blocked": ("⛔", "Blocked"),
        "human_input": ("👤", "Needs your input"),
        "in_flight": ("⏳", "Running"),
        "scheduled": ("📅", "Scheduled"),
        "pending": ("⬜", "Pending"),
    }

    md_lines = [
        f"**{badge} · Android Release {state.release_id}**",
        f"Current Phase: **{phase_label}** (**{phase_progress}**)",
    ]
    if risk.get("external_cutoff"):
        md_lines.append(f"⚠️ **External cutoff:** {risk['external_cutoff']}")
    elif risk.get("unattended_business_hours", 0) >= 8:
        md_lines.append(
            f"⏱️ **Unattended business time:** {risk['unattended_business_hours']:.1f}h without owner progress"
        )

    if completed_this_run:
        done_names = ", ".join(f"**{s.get('name') or s['step']}**" for s in completed_this_run[:6])
        md_lines.append(f"\n✅ **Completed automatically in this run:** {done_names}")

    if phase_steps:
        md_lines.append(f"\n📋 **{phase_label} Checklist ({p_done}/{p_total}):**")
        for ps in phase_steps:
            icon, st_word = status_icon_map.get(ps.get("status", "pending"), ("⬜", "Pending"))
            md_lines.append(f"- {icon} **{ps['name']}** — {st_word}")

    if blocked_items:
        md_lines.append(f"\n⛔ **Blocked — Action Required ({len(blocked_items)}):**")
        for item in blocked_items:
            friendly = item.get("name") or item["step"]
            wait_txt = _format_duration_minutes(item.get("waiting_minutes"))
            links_md = " · ".join(
                f"[{lnk['name']}]({lnk['url']})"
                for lnk in (item.get("links") or [])
                if lnk.get("name") and lnk.get("url")
            )
            suffix = f" — {links_md}" if links_md else ""
            md_lines.append(f"- **{friendly}** *(waiting {wait_txt})*: {item['reason']}{suffix}")

    if human_items:
        md_lines.append(f"\n👤 **Needs Your Input ({len(human_items)}):**")
        for item in human_items:
            friendly = item.get("name") or item["step"]
            wait_txt = _format_duration_minutes(item.get("waiting_minutes"))
            md_lines.append(f"- **{friendly}** *(waiting {wait_txt})*: {item['reason']}")

    focus_names = ", ".join(f"**{i.get('name') or i['step']}**" for i in actionable[:2])
    md_lines.append(
        f"\n👉 **Next step:** Open **Scout** and run `/release-agent` (`continue` for release `{state.release_id}`) "
        f"to resolve {focus_names or 'the pending step(s)'}."
    )
    markdown_body = "\n".join(md_lines)

    banner_bg = "#FEF3F2" if level in ("cutoff_at_risk", "stalled") else (
        "#FFFAEB" if level == "attention" else "#EFF8FF"
    )
    banner_border = "#F04438" if level in ("cutoff_at_risk", "stalled") else (
        "#F79009" if level == "attention" else "#2E90FA"
    )

    def _html_items(rows: list[dict[str, Any]]) -> str:
        parts = []
        for item in rows:
            friendly = item.get("name") or item["step"]
            wait_txt = _format_duration_minutes(item.get("waiting_minutes"))
            links_html = " &middot; ".join(
                f'<a href="{escape(str(lnk["url"]), quote=True)}">{escape(str(lnk["name"]))}</a>'
                for lnk in (item.get("links") or [])
                if lnk.get("name") and lnk.get("url")
            )
            link_part = f" &mdash; {links_html}" if links_html else ""
            parts.append(
                f"<li style=\"margin-bottom:6px;\"><b>{escape(str(friendly))}</b> "
                f"<span style=\"color:#475467;\">(waiting {escape(wait_txt)})</span>: "
                f"{escape(str(item['reason']))}{link_part}</li>"
            )
        return "".join(parts)

    risk_html = ""
    if risk.get("external_cutoff"):
        risk_html = f"<p style=\"margin:6px 0 0;\"><b>External cutoff:</b> {escape(str(risk['external_cutoff']))}</p>"
    elif risk.get("unattended_business_hours", 0) >= 8:
        risk_html = (
            f"<p style=\"margin:6px 0 0;\"><b>Unattended business time:</b> "
            f"{risk['unattended_business_hours']:.1f}h</p>"
        )

    drained_html = ""
    if completed_this_run:
        drained_items = "".join(
            f"<li><b>{escape(str(s.get('name') or s['step']))}</b></li>"
            for s in completed_this_run[:8]
        )
        drained_html = f"<p><b>Completed automatically in this run:</b></p><ul>{drained_items}</ul>"

    checklist_html = ""
    if phase_steps:
        checklist_rows = "".join(
            f"<li>{status_icon_map.get(ps.get('status', 'pending'), ('⬜', 'Pending'))[0]} "
            f"<b>{escape(str(ps['name']))}</b> &mdash; "
            f"<span style=\"color:#475467;\">{escape(status_icon_map.get(ps.get('status', 'pending'), ('⬜', 'Pending'))[1])}</span></li>"
            for ps in phase_steps
        )
        checklist_html = (
            f"<p><b>{escape(str(phase_label))} Checklist ({p_done}/{p_total}):</b></p>"
            f"<ul style=\"list-style:none;padding-left:4px;\">{checklist_rows}</ul>"
        )

    blocked_html = (
        f"<p><b>Blocked &mdash; Action Required ({len(blocked_items)}):</b></p><ul>{_html_items(blocked_items)}</ul>"
        if blocked_items else ""
    )
    human_html = (
        f"<p><b>Needs Your Input ({len(human_items)}):</b></p><ul>{_html_items(human_items)}</ul>"
        if human_items else ""
    )

    email_html = (
        "<div style=\"font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;color:#101828;\">"
        f"<div style=\"background:{banner_bg};border-left:4px solid {banner_border};padding:12px 14px;margin-bottom:14px;\">"
        f"<div><b>{escape(badge)} &middot; Android Release {escape(str(state.release_id))}</b></div>"
        f"<div style=\"margin-top:4px;\">Current Phase: <b>{escape(str(phase_label))}</b> "
        f"(<b>{escape(phase_progress)}</b>)</div>"
        f"{risk_html}</div>"
        f"{drained_html}{checklist_html}{blocked_html}{human_html}"
        "<p style=\"margin-top:16px;\"><b>Call to Action:</b> Open <b>Scout</b> and run "
        f"<code>/release-agent</code> (<code>continue</code> for release <code>{escape(str(state.release_id))}</code>) "
        "to resolve the pending step(s).</p></div>"
    )
    return subject, markdown_body, email_html


def _build_scout_bot_cta(release_id: str, offered_bot: dict[str, Any], subject: str) -> dict[str, Any]:
    nid = offered_bot["id"]
    nhash = offered_bot["hash"]
    msg = (offered_bot.get("payload") or {}).get("message") or ""
    return {
        "action_required": True,
        "notification_id": nid,
        "hash": nhash,
        "tool": "m_send_teams_message",
        "message": msg,
        "subject": subject,
        "claim_command": (
            f"python -m orchestrator.cli --scheduled notification claim --release {release_id} "
            f"--id \"{nid}\" --hash \"{nhash}\" --executor release-drainer --json"
        ),
        "ack_sent_command": (
            f"python -m orchestrator.cli --scheduled notification result --release {release_id} "
            f"--id \"{nid}\" --execution-id <execution_id> --outcome sent "
            f"--evidence \"Scout bot DM sent via m_send_teams_message\""
        ),
    }


def _pending_scout_bot_cta(orch, sig: str) -> Optional[dict[str, Any]]:
    """Find any already-prepared but still-unsent Scout bot CTA for the active hold signature."""
    prefix = f"drain-cta:{orch.state.release_id}:{sig[:19]}:"
    for nid, rec in reversed(list((orch.state.notification_deliveries or {}).items())):
        if not nid.startswith(prefix) or not isinstance(rec, dict):
            continue
        desc = rec.get("descriptor") or {}
        if desc.get("tool") != "m_send_teams_message":
            continue
        if rec.get("status") in ("prepared", "not_sent") and D.available(orch, desc):
            return _build_scout_bot_cta(
                orch.state.release_id, desc, "Pending Scout Bot Call-to-Action"
            )
    return None


def _cta_channels(config_path: str) -> dict[str, bool]:
    """Load configured channels; default to BOTH Teams (Scout bot) + Email when unconfigured."""
    p = notif.notifications_path(config_path)
    if os.path.isfile(p):
        return notif.channels(notif.load_config(config_path))
    return {"email": True, "teams": True}


def evaluate_risk_and_cta(
    orch,
    snapshot: dict[str, Any],
    executed_steps: list[dict[str, str]],
    waiting: dict[str, Any],
    *,
    runs_root: str,
    config_path: str,
    now_utc: datetime,
    deliver: bool = False,
    mcp_caller: MCPCaller | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Optional[dict[str, Any]]]:
    """Evaluate Idle-Stall + External-Cutoff risk and stage/deliver due CTA notifications."""
    state = orch.state
    selection = orch.scheduling()
    if selection.status in ("complete", "cancelled", "halted"):
        if selection.status in ("complete", "cancelled"):
            state.drain_tracker = {}
        return (
            {
                "level": "normal",
                "unattended_business_hours": 0.0,
                "external_cutoff": None,
                "reason": selection.status,
            },
            {"sent": False, "due": False, "reason": f"release_{selection.status}", "channels": []},
            None,
        )

    actionable = _actionable_waiting_items(waiting)
    external_cutoff = detect_external_cutoff(orch)
    if not actionable:
        state.drain_tracker = {}
        return (
            {
                "level": "cutoff_at_risk" if external_cutoff else "normal",
                "unattended_business_hours": 0.0,
                "external_cutoff": external_cutoff,
                "reason": "no_actionable_hold",
            },
            {"sent": False, "due": False, "reason": "no_actionable_hold", "channels": []},
            None,
        )

    sig = _hold_signature(actionable)
    tracker = dict(state.drain_tracker or {})
    is_new_signature = tracker.get("hold_signature") != sig
    if is_new_signature:
        first_seen_iso = now_utc.isoformat()
        last_sent_iso: Optional[str] = None
        send_count = 0
    else:
        first_seen_iso = str(tracker.get("hold_first_seen_at") or now_utc.isoformat())
        last_sent_iso = tracker.get("last_sent_at")
        send_count = int(tracker.get("send_count") or 0)

    first_seen_dt = schedule.parse_iso_utc(first_seen_iso) or now_utc
    owner_touch_dt = schedule.parse_iso_utc(state.last_owner_interaction_at)
    effective_start = (
        max(first_seen_dt, owner_touch_dt)
        if owner_touch_dt is not None
        else first_seen_dt
    )
    unattended_bh = schedule.business_hours_between(
        effective_start,
        orch.now_local,
        tz=orch.tz,
        day_start_hour=7,
        day_end_hour=19,
    )

    if external_cutoff:
        level = "cutoff_at_risk"
        risk_reason = f"External cutoff approaching: {external_cutoff}"
    elif unattended_bh >= 24.0:
        level = "stalled"
        risk_reason = f"Holding {unattended_bh:.1f} business hours without owner progress (>24h threshold)"
    elif unattended_bh >= 8.0:
        level = "attention"
        risk_reason = f"Holding {unattended_bh:.1f} business hours without owner progress (8-24h threshold)"
    else:
        level = "normal"
        risk_reason = "Actionable step waiting on release owner"

    risk = {
        "level": level,
        "unattended_business_hours": unattended_bh,
        "external_cutoff": external_cutoff,
        "reason": risk_reason,
    }

    last_sent_dt = schedule.parse_iso_utc(last_sent_iso)
    hours_since_last = (
        (now_utc - last_sent_dt.astimezone(timezone.utc)).total_seconds() / 3600.0
        if last_sent_dt is not None
        else None
    )
    last_sent_local_date = (
        last_sent_dt.astimezone(orch.tz).date()
        if (last_sent_dt is not None and orch.tz is not None)
        else (last_sent_dt.date() if last_sent_dt is not None else None)
    )
    hour_local = orch.now_local.hour

    due = False
    cta_reason = f"paced_{level}_cooldown"
    if last_sent_dt is None:
        due = True
        cta_reason = "immediate_new_hold"
    elif level in ("stalled", "cutoff_at_risk"):
        if (hours_since_last or 0.0) >= 3.5 and 7 <= hour_local <= 20:
            due = True
            cta_reason = f"escalated_{level}_4h"
    elif level == "attention":
        if (hours_since_last or 0.0) >= 5.5 and (7 <= hour_local < 11 or 15 <= hour_local < 19):
            due = True
            cta_reason = "attention_twice_daily"
    else:
        if (
            7 <= hour_local < 11
            and last_sent_local_date != orch.now_local.date()
            and (hours_since_last or 0.0) >= 12.0
        ):
            due = True
            cta_reason = "daily_morning_digest"

    tracker.update({
        "hold_signature": sig,
        "hold_first_seen_at": first_seen_iso,
        "risk_level": level,
        "unattended_business_hours": unattended_bh,
        "last_evaluated_at": now_utc.isoformat(),
    })

    if not due:
        state.drain_tracker = tracker
        pending_bot = _pending_scout_bot_cta(orch, sig) if deliver else None
        if pending_bot is not None:
            C.save_state(state, runs_root, state.release_id)
            return (
                risk,
                {
                    "sent": False,
                    "due": True,
                    "reason": "retry_unsent_scout_bot_cta",
                    "channels": ["teams:scout_bot_staged"],
                },
                pending_bot,
            )
        return risk, {"sent": False, "due": False, "reason": cta_reason, "channels": []}, None

    ch = _cta_channels(config_path)
    ncfg = notif.load_config(config_path) if os.path.isfile(notif.notifications_path(config_path)) else {
        "channels": ch,
        "teams": {"target": "scout"},
    }
    subject, md_text, html_text = render_cta_messages(
        state, snapshot, executed_steps, actionable, risk
    )
    episode = send_count + 1
    logical_base = f"drain-cta:{state.release_id}:{sig[:19]}:ep{episode}"
    scope = {
        "kind": "phase" if snapshot.get("phase") not in (None, "readiness") else "release",
        **({"phase": snapshot["phase"]} if snapshot.get("phase") not in (None, "readiness") else {}),
        "date": orch.now_local.date().isoformat(),
        "release_matches": {"owner_email": state.owner_email},
    }

    sent_channels: list[str] = []
    scout_bot_cta: Optional[dict[str, Any]] = None

    if not deliver:
        planned_channels = [
            name for name in ("teams", "email")
            if ch.get(name) and (name != "email" or bool(state.owner_email))
        ]
        state.drain_tracker = tracker
        return (
            risk,
            {"sent": False, "due": True, "reason": cta_reason, "channels": planned_channels},
            None,
        )

    python_descriptors: list[dict[str, Any]] = []
    if ch.get("email") and state.owner_email:
        python_descriptors.append(
            D.descriptor(
                state,
                f"{logical_base}:email",
                scope,
                "workiq_send_email",
                {"to": [state.owner_email], "subject": subject, "body": html_text, "isHtml": True},
            )
        )

    if ch.get("teams"):
        t_block = notif.teams_delivery(ncfg, html_text, md_text, md_text)
        if t_block:
            if t_block["via"] == "scout_bot":
                bot_desc = D.descriptor(
                    state,
                    f"{logical_base}:teams",
                    scope,
                    "m_send_teams_message",
                    {"message": t_block["text"]},
                )
                if D.available(orch, bot_desc):
                    offered_bot = D.offer(orch, bot_desc)
                    scout_bot_cta = _build_scout_bot_cta(state.release_id, offered_bot, subject)
                    sent_channels.append("teams:scout_bot_staged")
            else:
                python_descriptors.append(
                    D.descriptor(
                        state,
                        f"{logical_base}:teams",
                        scope,
                        "workiq_send_chat_message",
                        {k: v for k, v in t_block.items() if k != "via"},
                    )
                )

    eligible_py = [d for d in python_descriptors if D.available(orch, d)]
    C.save_state(state, runs_root, state.release_id)
    if eligible_py:
        from orchestrator.commands.deliver_cmd import deliver_descriptor_items
        py_results = deliver_descriptor_items(
            runs_root,
            state.release_id,
            config_path,
            eligible_py,
            executor="release-drainer-cta",
            mcp_caller=mcp_caller,
            as_of=orch.now_local,
        )
        # Reload latest state after deliver_descriptor_items persisted delivery receipts.
        reloaded = C.load_state(runs_root, state.release_id)
        state.notification_deliveries = reloaded.notification_deliveries
        for res in py_results:
            if res.get("status") == "done":
                sent_channels.append("email" if "email" in str(res.get("notification_id")) else "teams")

    if sent_channels:
        tracker["last_sent_at"] = now_utc.isoformat()
        tracker["send_count"] = episode
    state.drain_tracker = tracker
    C.save_state(state, runs_root, state.release_id)
    return (
        risk,
        {
            "sent": bool(sent_channels),
            "due": True,
            "reason": cta_reason,
            "channels": sent_channels,
            "subject": subject,
        },
        scout_bot_cta,
    )


def _drain_python_loop(
    runs_root: str,
    release: str,
    config_path: str,
    *,
    now=None,
    as_of=None,
    deliver: bool = False,
    mcp_caller: MCPCaller | None = None,
):
    """Advance all eligible Python readiness checks, Python `auto` steps, `in_flight` reconciliations,
    and `requires_approval: false` notifications until quiescent."""
    st, orch = C.load_orch(runs_root, release, config_path, now or as_of)
    if st.halt or st.cancellation or orch._projection().revision_problem:
        return st, orch

    if not orch.gate.signed:
        orch.gate.verify(pending_only=True)
        C.save_state(st, runs_root, release)
        return st, orch

    reconciled_once: set[str] = set()
    for _ in range(16):
        before = _status_map(orch)
        actions = orch.run_until_gate(pending_only=True)
        if actions:
            C.save_state(st, runs_root, release)
            C.log_actions(C.elog(runs_root, release), actions, state=st)

        if deliver and (mcp_caller is not None or (now is None and as_of is None)):
            from orchestrator.commands.deliver_cmd import drain_auto_notifications
            delivered = drain_auto_notifications(
                runs_root,
                release,
                config_path,
                executor="release-drainer-mcp",
                mcp_caller=mcp_caller,
            )
            if delivered:
                st, orch = C.load_orch(runs_root, release, config_path, now or as_of)

        # Reconcile any eligible read_only `in_flight` (or externally-resolvable `ccd.localization_pr`)
        # step at most once in this drain run.
        selection = orch.scheduling()
        if selection.frontier and selection.phase(selection.frontier.id).due:
            for s_ready in selection.steps:
                step = s_ready.definition
                if (
                    step.phase_id != selection.frontier.id
                    or s_ready.complete
                    or not s_ready.prerequisites_met
                    or not s_ready.time_ready
                    or step.key in reconciled_once
                ):
                    continue
                rec = st.get_step(step.phase_id, step.id)
                can_recheck = (
                    step.kind == StepKind.AUTO
                    and step.effect_mode == EffectMode.READ_ONLY
                    and (
                        rec.status == "in_flight"
                        or (step.key == "ccd.localization_pr" and rec.status == "blocked")
                    )
                )
                if can_recheck:
                    reconciled_once.add(step.key)
                    act = orch.run_task(step.phase_id, step.id, retry=True)
                    C.save_state(st, runs_root, release)
                    C.log_actions(C.elog(runs_root, release), [act], state=st)

        after = _status_map(orch)
        if after == before:
            break
    return st, orch


def _derive_run_status(
    orch,
    executed_steps: list[dict[str, str]],
    waiting: dict[str, Any],
) -> str:
    lifecycle = orch.scheduling().status
    if lifecycle == "complete":
        return "complete"
    if lifecycle in ("halted", "cancelled"):
        return lifecycle
    if executed_steps:
        has_hold = bool(
            waiting["blocked_count"]
            or waiting["human_input_count"]
            or waiting["in_flight_count"]
            or waiting["unavailable_count"]
        )
        return "advanced_then_parked" if has_hold else "advanced"
    return "parked"


def run_drain(
    runs_root: str,
    release: str,
    config_path: str,
    *,
    trigger: str = "scheduled",
    finalize_run: Optional[str] = None,
    deliver: bool = False,
    now: Optional[datetime] = None,
    as_of: Optional[date] = None,
    mcp_caller: MCPCaller | None = None,
) -> dict[str, Any]:
    """Execute or finalize one unified Release Drainer pass and record `drain-runs.jsonl`."""
    t_mono_start = _monotonic.monotonic()
    st_initial, orch_initial = C.load_orch(runs_root, release, config_path, now or as_of)
    now_local = (
        now.astimezone(orch_initial.tz)
        if (now is not None and orch_initial.tz is not None)
        else (now or orch_initial.now_local)
    )
    now_utc = now_local.astimezone(timezone.utc) if now_local.tzinfo else datetime.now(timezone.utc)
    prior_runs = read_drain_runs(runs_root, release)

    active = _pop_active_run(runs_root, release)
    if finalize_run:
        if not active or active.get("run_id") != finalize_run:
            raise ValueError(f"No matching in-progress drain run '{finalize_run}' to finalize.")
        run_id = active["run_id"]
        trigger = active.get("trigger", trigger)
        started_at = active["started_at"]
        expected_slot = active.get("expected_slot")
        missed_slots = list(active.get("missed_slots") or [])
        entered = active["entered"]
        before_statuses = {
            k: tuple(v) for k, v in (active.get("before_statuses") or {}).items()
        }
        prior_ms = int(active.get("elapsed_ms") or 0)
    else:
        if active is not None:
            # Close any interrupted provisional run so the journal never drops a slot.
            interrupted_rec = {
                "schema_version": DRAIN_SCHEMA_VERSION,
                "run_id": active.get("run_id") or f"drain-{uuid.uuid4().hex[:10]}",
                "release_id": release,
                "trigger": active.get("trigger", "scheduled"),
                "expected_slot": active.get("expected_slot"),
                "started_at": active.get("started_at") or now_utc.isoformat(),
                "finished_at": now_utc.isoformat(),
                "duration_ms": int(active.get("elapsed_ms") or 0),
                "status": "interrupted",
                "missed_slots": list(active.get("missed_slots") or []),
                "missed_slot_count": len(active.get("missed_slots") or []),
                "entered": active.get("entered") or {},
                "exited": capture_snapshot(orch_initial),
                "executed_steps": [],
                "waiting": classify_waiting(orch_initial, now_utc),
                "risk": {
                    "level": "normal",
                    "unattended_business_hours": 0.0,
                    "external_cutoff": None,
                    "reason": "superseded_unfinalized_run",
                },
                "notification": {"sent": False, "due": False, "reason": "interrupted", "channels": []},
            }
            _append_drain_record(runs_root, release, interrupted_rec)
            prior_runs.append(interrupted_rec)

        run_id = f"drain-{now_local.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        started_at = now_utc.isoformat()
        expected_slot, missed_slots = compute_slot_status(
            st_initial, prior_runs, now_local, trigger=trigger
        )
        entered = capture_snapshot(orch_initial)
        before_statuses = _status_map(orch_initial)
        prior_ms = 0

    st, orch = _drain_python_loop(
        runs_root,
        release,
        config_path,
        now=now,
        as_of=as_of,
        deliver=deliver,
        mcp_caller=mcp_caller,
    )
    elapsed_ms = prior_ms + max(1, int((_monotonic.monotonic() - t_mono_start) * 1000))
    pending_host_work = [] if finalize_run else _collect_pending_host_work(orch)

    friendly_names = _step_friendly_names(orch)
    if pending_host_work:
        _write_active_run(runs_root, release, {
            "run_id": run_id,
            "trigger": trigger,
            "started_at": started_at,
            "expected_slot": expected_slot,
            "missed_slots": missed_slots,
            "entered": entered,
            "before_statuses": {k: list(v) for k, v in before_statuses.items()},
            "elapsed_ms": elapsed_ms,
        })
        return {
            "schema_version": DRAIN_SCHEMA_VERSION,
            "run_id": run_id,
            "release_id": release,
            "trigger": trigger,
            "status": "awaiting_host_work",
            "finalized": False,
            "expected_slot": expected_slot,
            "missed_slots": missed_slots,
            "missed_slot_count": len(missed_slots),
            "entered": entered,
            "current": capture_snapshot(orch),
            "executed_steps": _diff_executed_steps(before_statuses, _status_map(orch), friendly_names),
            "pending_host_work": pending_host_work,
            "finalize_command": (
                f"drain --release {release} --finalize-run {run_id}"
                + (" --deliver" if deliver else "")
                + " --json"
            ),
            "scout_bot_cta": None,
        }

    after_statuses = _status_map(orch)
    executed_steps = _diff_executed_steps(before_statuses, after_statuses, friendly_names)
    exited = capture_snapshot(orch)
    waiting = classify_waiting(orch, now_utc)
    risk, notif_summary, scout_bot_cta = evaluate_risk_and_cta(
        orch,
        exited,
        executed_steps,
        waiting,
        runs_root=runs_root,
        config_path=config_path,
        now_utc=now_utc,
        deliver=deliver,
        mcp_caller=mcp_caller,
    )
    finished_at = (
        now_utc + timedelta(milliseconds=elapsed_ms)
        if now is not None
        else datetime.now(timezone.utc)
    ).isoformat()
    run_status = _derive_run_status(orch, executed_steps, waiting)

    record = {
        "schema_version": DRAIN_SCHEMA_VERSION,
        "run_id": run_id,
        "release_id": release,
        "trigger": trigger,
        "expected_slot": expected_slot,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": elapsed_ms,
        "status": run_status,
        "missed_slots": missed_slots,
        "missed_slot_count": len(missed_slots),
        "entered": entered,
        "exited": exited,
        "executed_steps": executed_steps,
        "waiting": waiting,
        "risk": risk,
        "notification": notif_summary,
    }
    _append_drain_record(runs_root, release, record)
    C.elog(runs_root, release).log(
        "drain_run",
        run_id=run_id,
        trigger=trigger,
        status=run_status,
        executed_count=len(executed_steps),
        blocked_count=waiting["blocked_count"],
        human_input_count=waiting["human_input_count"],
        missed_slot_count= len(missed_slots),
        risk_level=risk["level"],
        cta_sent=notif_summary["sent"],
    )
    return {
        **record,
        "finalized": True,
        "pending_host_work": [],
        "scout_bot_cta": scout_bot_cta,
    }


def drain_log_summary(
    runs_root: str,
    release: str,
    config_path: str,
    *,
    limit: int = 25,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Read structured drain history plus live unrecorded missed slots for external tools."""
    st, orch = C.load_orch(runs_root, release, config_path, now)
    now_local = (
        now.astimezone(orch.tz)
        if (now is not None and orch.tz is not None)
        else (now or orch.now_local)
    )
    all_runs = read_drain_runs(runs_root, release)
    _, live_missed = compute_slot_status(
        st, all_runs, now_local, trigger="audit"
    )
    selected = all_runs[-limit:] if (limit and limit > 0) else all_runs
    return {
        "schema_version": DRAIN_SCHEMA_VERSION,
        "release_id": release,
        "journal_path": journal_path(runs_root, release),
        "total_runs": len(all_runs),
        "live_missed_slots": live_missed,
        "live_missed_slot_count": len(live_missed),
        "current": capture_snapshot(orch),
        "runs": selected,
    }
