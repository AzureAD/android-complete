"""Read-only host dispatch and handoff policy for every workflow phase."""
from __future__ import annotations


def phase_plan(orch, selection, readiness):
    release = orch.state.release_id
    base = {
        "phase": selection.frontier.id if selection.frontier else None,
        "as_of": orch.as_of.isoformat(),
        "pending_work": [], "held_work": [], "deferred_work": [],
        "handoff_command": f"status --release {release} --handoff --json --no-pipeline-check",
        "permission_to_execute": False, "permission_to_send": False,
    }
    if selection.suspension in ("workflow_revision", "halted", "cancelled", "denied"):
        return {**base, "next_action": "resolve_release_hold", "continue_automatically": False,
                "may_return_to_user": True,
                "instruction": f"Release hold ({selection.suspension}); no new phase work is authorized."}
    if not readiness["signed"]:
        plan = readiness["dispatch"]
        return {**base, "next_action": "readiness", "readiness_dispatch": plan,
                "continue_automatically": plan["continue_automatically"],
                "may_return_to_user": plan["may_return_to_user"], "instruction": plan["instruction"]}
    pending, held, seen = [], [], set()
    for candidate in selection.runnable:
        step = candidate.step
        record = selection.step(step.phase_id, step.id)
        if record.status != "pending" or record.reserved or candidate.recovery:
            continue
        pending.append({
            "key": step.key, "phase": step.phase_id, "step": step.id,
            "name": step.name, "kind": "engine",
            "command": f"next --release {release} --pending-only --json",
            "instruction": "Run pending engine work once; do not retry recorded failures or poll in-flight work in a tight loop.",
        })
        seen.add(step.key)
    for sid in selection.scout_pending:
        step = selection.frontier.step(sid)
        record = selection.step(step.phase_id, sid)
        continuation = bool(orch.step_execution(step.phase_id, sid).get("write_progress"))
        if step.key in seen or ((record.status != "pending" or record.reserved) and not continuation):
            continue
        pending.append({
            "key": step.key, "phase": step.phase_id, "step": sid,
            "name": step.name, "kind": "scout",
            "command": f"step-action --release {release} --phase {step.phase_id} --step {sid}",
            "write_command": step.write_command,
            "continuation": continuation,
            "instruction": (
                "Inspect the owning step's result and follow its checked notification/write/read protocol. "
                "This queue is not write or send authorization. Honor exact preview, human approval and host consent; "
                "record actual evidence. Inspect an owned continuation once through its checked command; "
                "an unresolved outcome requires an exact --hold-step reason, not another loop/replay."),
        })
    if selection.frontier:
        for item in selection.steps:
            step = item.definition
            if step.phase_id != selection.frontier.id or item.complete or not item.prerequisites_met:
                continue
            if item.status in ("blocked", "running", "in_flight") or item.reserved:
                held.append({
                    "key": step.key, "phase": step.phase_id, "step": step.id,
                    "status": item.status, "owned": item.reserved,
                    "instruction": "Preserve ownership and use the owning recovery/poll/input protocol; no automatic replay.",
                })
    action = ("run_phase_work" if pending else "resolve_phase_hold" if held else
              "complete" if selection.frontier is None else
              "scheduled" if selection.status == "scheduled" else "owner_or_wait")
    return {
        **base, "pending_work": pending, "held_work": held,
        "next_action": action, "continue_automatically": bool(pending),
        "may_return_to_user": not pending,
        "instruction": (
            "Scout must exhaust every eligible independent task before returning or asking for an owner decision. "
            "A sibling block or pending gate is not a dispatch veto. Execute the queue, re-read status after each "
            "result, and discover newly eligible work. Do not retry blocked/unknown actions or bypass consent."
            if pending else
            "No unattempted phase work is executable. Preserve real holds, ownership, dependencies and scheduling; "
            "present the actual owner decision or wait, not a claim that unfinished work succeeded."),
    }


def handoff_plan(plan, holds):
    """Transient host-reported input boundaries, never completion or provider proof."""
    if holds and plan["next_action"] not in ("run_phase_work", "provision_startup_automation"):
        raise ValueError("--hold-step only applies to currently queued phase tasks")
    reasons = {}
    queued = {item["key"]: item for item in plan["pending_work"]}
    for value in holds or ():
        key, sep, reason = value.partition("=")
        if (not sep or key not in queued or queued[key]["kind"] not in ("scout", "automation")
                or key in reasons or not reason.strip()):
            raise ValueError("--hold-step requires a unique queued Scout/automation key=actual input/consent/recovery reason")
        reasons[key] = reason.strip()
    if not reasons:
        return plan
    remaining = [item for item in plan["pending_work"] if item["key"] not in reasons]
    deferred = [{**queued[key], "reason": reason, "verification": "host_reported_not_provider_verified"}
                for key, reason in reasons.items()]
    return {
        **plan, "pending_work": remaining, "deferred_work": deferred,
        "next_action": "run_phase_work" if remaining else "resolve_host_input",
        "continue_automatically": bool(remaining), "may_return_to_user": not remaining,
        "instruction": plan["instruction"] if remaining else
        "Independent work is exhausted. Show these exact host-reported input/consent/recovery needs. "
        "Deferred tasks remain incomplete; this handoff grants no write, send, retry or approval permission.",
    }


def startup_plan(orch, selection):
    """Registry-backed startup bookkeeping, not a live provider availability claim."""
    reader = orch.automation_entries
    if reader is None:
        return None
    from orchestrator.automations import load_defs, _provider_defaults
    from orchestrator.registry import has_creation_receipt, provider_check_fresh
    definitions = [item for item in load_defs(orch.config_path) if item.get("required_on_start")]
    entries = reader()
    required, recoveries, active, problems = [], [], [], []
    if selection.frontier is None or orch.state.cancellation:
        definitions = []
    for definition in definitions:
        slug = definition["slug"]
        if _provider_defaults(orch.config_path).get("enabled") is not True:
            problems.append(f"{slug}: mandatory driver cannot be disabled")
            continue
        if (definition.get("required_on_start") is not True
                or definition.get("on_demand") or not definition.get("every")):
            problems.append(f"{slug}: invalid mandatory startup worker configuration")
            continue
        matches = [item for item in entries if item.get("slug") == slug
                   and item.get("scope") == "release" and item.get("release") == orch.state.release_id]
        if len(matches) > 1:
            problems.append(f"{slug}: duplicate startup registry identities; reconcile before creating")
            continue
        existing = matches[0] if matches else None
        if (existing is None or existing["status"] in ("prepared", "missing")
                or has_creation_receipt(existing) and not provider_check_fresh(existing)):
            required.append({"slug": slug, "name": existing["name"] if existing else slug})
        elif has_creation_receipt(existing):
            active.append(slug)
        else:
            recoveries.append({key: existing.get(key) for key in ("slug", "key", "id", "status")})
    return {
        "required": [{"slug": item["slug"], "name": item["name"],
                      "command": f"automation plan --release {orch.state.release_id} --slug {item['slug']} --json"}
                     for item in required],
        "recoveries": recoveries,
        "active": active,
        "complete": not required and not recoveries and not problems,
        "problems": problems,
        "permission_to_create": False,
        "verification": "registry_receipts_not_live_provider_check",
    }


def with_startup(plan, startup):
    if startup is None:
        return plan
    base = {**plan, "startup_automation": startup}
    if plan["next_action"] == "resolve_release_hold" or startup["complete"]:
        return base
    tasks = [{
        "key": f"automation:{item['slug']}", "kind": "automation", "name": item["name"],
        "command": item["command"],
        "instruction": (
            "Ensure the required release-wide driver. Obtain its canonical plan and fresh exhaustive Scout "
            "list/details, then use automation reconcile-create --ensure --claim with that exact spec. "
            "An exact existing worker is adopted; a proven-absent settled worker is safely recreated. "
            "Acknowledge the owning create-result. Only permission_to_create:true permits creation; "
            "unknown operations are never repeated."),
    } for item in startup["required"]]
    entry = plan["next_action"] == "readiness"
    queued = tasks
    entry_checks = entry and plan["continue_automatically"]
    return {
        **base, "pending_work": queued, "phase_work_waiting": plan["pending_work"],
        "next_action": "provision_startup_automation" if tasks else
        "readiness" if entry_checks else "recover_startup_automation",
        "continue_automatically": bool(queued) or entry_checks,
        "may_return_to_user": not queued and (plan["may_return_to_user"] if entry else True),
        "instruction": (
            "Startup is incomplete: the required release-wide automation needs provisioning/reconciliation. "
            "Do not claim the release is running unattended. Exhaust independent readiness/phase work before "
            "a real input/recovery question. Unknown create/delete outcomes retain their original ownership; "
            "never create a duplicate or silently replace an existing worker."),
    }


def startup_checklist(chk, work):
    startup = work.get("startup_automation")
    if startup is None:
        return chk
    dispatch = chk["dispatch"]
    if not startup["complete"]:
        dispatch = {
            **dispatch,
            **{key: work[key] for key in (
                "next_action", "continue_automatically", "may_return_to_user", "instruction")},
            "pending_work": work["pending_work"], "startup_automation": startup,
        }
    return {**chk, "dispatch": dispatch, "startup_automation": startup, "work_dispatch": work}
