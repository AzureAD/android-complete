"""Read-only host dispatch and handoff policy for every workflow phase."""
from __future__ import annotations

from orchestrator.release_contract import DispatchAction, LifecycleStatus


def _held_resume_command(release: str, step, execution: dict | None) -> str:
    execution = execution or {}
    execution_id = execution.get("id")
    if not execution_id:
        return f"step-action --release {release} --phase {step.phase_id} --step {step.id}"
    if getattr(step, "write_command", None) == "publish-rc-report":
        return (
            f"publish-rc-report --release {release} --execution-id {execution_id} "
            "--receipt-file <receipt.json>"
        )
    return (
        f"step-action --release {release} --phase {step.phase_id} --step {step.id} "
        f"--execution-id {execution_id}"
    )


def phase_plan(orch, selection, readiness):
    release = orch.state.release_id
    base = {
        "phase": selection.frontier.id if selection.frontier else None,
        "as_of": orch.as_of.isoformat(),
        "pending_work": [], "held_work": [], "deferred_work": [],
        "handoff_command": f"status --release {release} --handoff --json --no-pipeline-check",
        "permission_to_execute": False, "permission_to_send": False,
    }
    if selection.suspension in (
        LifecycleStatus.WORKFLOW_REVISION.value,
        LifecycleStatus.HALTED.value,
        LifecycleStatus.CANCELLED.value,
        "denied",
    ):
        return {**base, "next_action": DispatchAction.RESOLVE_RELEASE_HOLD.value, "continue_automatically": False,
                "may_return_to_user": True,
                "instruction": f"Release hold ({selection.suspension}); no new phase work is authorized."}
    if not readiness["signed"]:
        plan = readiness["dispatch"]
        return {**base, "next_action": DispatchAction.READINESS.value, "readiness_dispatch": plan,
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
                execution = orch.step_execution(step.phase_id, step.id)
                held.append({
                    "key": step.key, "phase": step.phase_id, "step": step.id,
                    "status": item.status, "owned": item.reserved,
                    "execution_id": execution.get("id") if execution else None,
                    "resume_command": _held_resume_command(release, step, execution),
                    "instruction": "Preserve ownership and use the owning recovery/poll/input protocol; no automatic replay.",
                })
    action = (
        DispatchAction.RUN_PHASE_WORK.value if pending else
        DispatchAction.RESOLVE_PHASE_HOLD.value if held else
        DispatchAction.COMPLETE.value if selection.frontier is None else
        DispatchAction.SCHEDULED.value if selection.status == LifecycleStatus.SCHEDULED.value
        else DispatchAction.OWNER_OR_WAIT.value
    )
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
    if holds and plan["next_action"] not in (
        DispatchAction.RUN_PHASE_WORK.value,
        DispatchAction.PROVISION_STARTUP_AUTOMATION.value,
    ):
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
        "next_action": (
            DispatchAction.RUN_PHASE_WORK.value
            if remaining
            else DispatchAction.RESOLVE_HOST_INPUT.value
        ),
        "continue_automatically": bool(remaining), "may_return_to_user": not remaining,
        "instruction": plan["instruction"] if remaining else
        "Independent work is exhausted. Show these exact host-reported input/consent/recovery needs. "
        "Deferred tasks remain incomplete; this handoff grants no write, send, retry or approval permission.",
    }


def owner_prompt_payload(report: dict) -> dict | None:
    """Structured owner prompt derived from a status snapshot."""
    plan = report.get("work_dispatch") or {}
    if plan.get("pending_work") or not plan.get("may_return_to_user", True):
        return None
    if plan.get("next_action") != DispatchAction.OWNER_OR_WAIT.value:
        return None
    if plan.get("held_work") or plan.get("deferred_work"):
        return None

    gate = report.get("gate")
    if gate and gate.get("kind") != "denied":
        return {
            "ready": True,
            "kind": "gate_decision",
            "phase": gate.get("phase"),
            "step": gate.get("step"),
            "question": (
                f"Decide gate '{gate.get('step_name')}' for release {report.get('release_id')}."
            ),
            "answers": [
                {
                    "title": "Approve gate",
                    "description": "Continue release progression past this gate.",
                    "action": "approve",
                },
                {
                    "title": "Deny gate",
                    "description": "Stop progression and keep the release on hold.",
                    "action": "deny",
                },
                {
                    "title": "Keep on hold",
                    "description": "Leave this gate unchanged for now.",
                    "action": "hold",
                },
            ],
            "recommendedIndex": 2,
        }

    action = report.get("action")
    if not action:
        return None
    if action.get("reason") == "blocked":
        return {
            "ready": True,
            "kind": "blocked_step",
            "phase": action.get("phase"),
            "step": action.get("step"),
            "question": (
                f"How should Scout handle blocked step '{action.get('step_name')}' "
                f"for release {report.get('release_id')}?"
            ),
            "answers": [
                {
                    "title": "Fixed — rerun",
                    "description": "Rerun the owning check after a real fix.",
                    "action": "rerun",
                },
                {
                    "title": "Override",
                    "description": "Skip with an explicit owner reason.",
                    "action": "override",
                },
                {
                    "title": "Keep blocked",
                    "description": "Leave this blocked hold in place.",
                    "action": "hold",
                },
            ],
            "recommendedIndex": 2,
        }
    if action.get("execution"):
        return None
    return {
        "ready": True,
        "kind": "owner_action",
        "phase": action.get("phase"),
        "step": action.get("step"),
        "question": (
            f"Did you complete '{action.get('step_name')}' for release {report.get('release_id')}?"
        ),
        "answers": [
            {
                "title": "Completed",
                "description": "Mark the step done and continue.",
                "action": "done",
            },
            {
                "title": "Not yet",
                "description": "Keep this owner action on hold.",
                "action": "hold",
            },
            {
                "title": "Need help",
                "description": "Show guidance for this step before deciding.",
                "action": "help",
            },
        ],
        "recommendedIndex": 1,
    }


def decorate_handoff_contract(report: dict) -> dict:
    """Attach machine-readable handoff cues to a status snapshot."""
    plan = report.get("work_dispatch") or {}
    report["handoff_ready"] = bool(plan.get("may_return_to_user"))
    report["must_continue_automatic_work"] = not report["handoff_ready"]
    owner_prompt = owner_prompt_payload(report)
    if owner_prompt:
        report["owner_prompt"] = owner_prompt
    else:
        report.pop("owner_prompt", None)
    return report


def startup_plan(orch, selection):
    """Registry-backed startup bookkeeping, not a live provider availability claim."""
    reader = orch.automation_entries
    if reader is None:
        return None
    from orchestrator.automations import load_defs, _provider_defaults
    from orchestrator.registry import has_creation_receipt
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
        if existing is None or existing["status"] in ("prepared", "missing"):
            required.append({"slug": slug, "name": existing["name"] if existing else slug})
        elif has_creation_receipt(existing):
            active.append(slug)
        else:
            recoveries.append({
                "slug": slug,
                "key": existing.get("key"),
                "id": existing.get("id"),
                "name": existing.get("name") or slug,
                "status": existing.get("status"),
                "command": f"automation plan --release {orch.state.release_id} --slug {slug} --json",
            })
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
    if plan["next_action"] == DispatchAction.RESOLVE_RELEASE_HOLD.value or startup["complete"]:
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
    for item in startup["recoveries"]:
        tasks.append({
            "key": f"automation:{item['slug']}", "kind": "automation", "name": item["name"],
            "command": item["command"],
            "instruction": (
                "Recover the existing startup driver without creating duplicates. Obtain its canonical plan and "
                "fresh exhaustive Scout list/details, then reconcile-create --ensure --claim with the exact spec. "
                "Preserve existing ownership for unknown create/delete outcomes and acknowledge only the owning result."),
        })
    entry = plan["next_action"] == DispatchAction.READINESS.value
    queued = tasks
    entry_checks = entry and plan["continue_automatically"]
    return {
        **base, "pending_work": queued, "phase_work_waiting": plan["pending_work"],
        "next_action": (
            DispatchAction.PROVISION_STARTUP_AUTOMATION.value if tasks else
            DispatchAction.READINESS.value if entry_checks else
            DispatchAction.RECOVER_STARTUP_AUTOMATION.value
        ),
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
