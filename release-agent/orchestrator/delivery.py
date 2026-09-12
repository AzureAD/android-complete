"""Release-local delivery contract. No transport calls; callers hold state_lock.

Preparing is not sending. A claim is never leased or replayed: an interrupted
transport requires evidence, not a timeout-based retry. The ledger and the
existing step execution record share the same execution ID.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from orchestrator.outcomes import NeedsSkill
from orchestrator.delivery_retention import (
    is_progress_receipt, receipt_descriptor, is_routine_progress, prune_progress,
)


TRANSPORTS = {
    "workiq_send_email": ("email", ("to",)),
    "workiq_send_chat_message": ("teams", ("chatId",)),
    "m_send_teams_message": ("teams", ("owner",)),
    "microsoft_teams-SendMessageToChannel": ("teams", ("teamId", "channelId")),
    "microsoft_teams-SendMessageToUser": ("teams", ("userIdOrUpn",)),
    "workiq_create_event": ("calendar", ("attendees",)),
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def chat_mentions(mentions):
    return [{"id": m["id"], "mentionText": m["name"],
             "mentioned": {"user": {"id": m["upn"], "displayName": m["name"],
                                     "userIdentityType": "aadUser"}}} for m in mentions]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def phase_done(orch, phase_id):
    phase = next((p for p in orch.config["phases"] if p["id"] == phase_id), None)
    return bool(phase and phase["steps"]
                and all(orch.state.is_done(phase_id, s["id"]) for s in phase["steps"]))


def scope_reason(orch, scope, *, acknowledgement=False):
    """One stop contract for a release, phase/window, or step-owned work item.

    Acknowledgements may retain evidence after halt/expiry, but never mutate a
    completed/skipped owner or advance a closed phase.
    """
    st = orch.state
    if st.status == "complete":
        return "release complete"
    if st.halted or st.blocked or st.status in ("halted", "blocked") or not st.readiness_signed:
        return "release suspended or unsigned"
    if orch.tz is None:
        return "owner timezone unavailable; repair configuration or tzdata"
    if not isinstance(scope, dict):
        return "missing/invalid lifecycle scope"
    kind = scope.get("kind")
    if kind not in ("release", "phase", "window", "step"):
        return "missing/invalid lifecycle scope"
    phases = scope.get("phases") if kind == "window" else [scope.get("phase")]
    if kind != "release":
        if not phases or orch.current_phase_id() not in phases:
            return "outside owning phase/window"
        if any(p not in {x["id"] for x in orch.config["phases"]} for p in phases):
            return "unknown owning phase"
        phase = orch._current_phase()
        if not phase or not orch._phase_due(phase):
            return "owning phase not due"
    if scope.get("step"):
        pid, sid = scope["phase"], scope["step"]
        if not orch._find_step(pid, sid):
            return "unknown owning step"
        record = st.get_step(pid, sid)
        if record.status == "skipped":
            return "owning step skipped"
        if kind == "step" and record.status == "done":
            return "owning step complete"
        if scope.get("statuses") and record.status not in scope["statuses"]:
            return "owning work no longer in expected state"
        if scope.get("until_flag") and record.data.get(scope["until_flag"]):
            return "owning work complete"
        for key, value in scope.get("step_matches", {}).items():
            if record.data.get(key) != value:
                return "owning checkpoint changed"
    for key, value in scope.get("release_matches", {}).items():
        if getattr(st, key, None) != value:
            return "release checkpoint changed"
    for match in scope.get("state_matches", []):
        value = vars(st)
        try:
            for key in match["path"]:
                value = value[key]
        except (KeyError, IndexError, TypeError):
            return "source checkpoint missing"
        if (fingerprint(value) != match["hash"] if "hash" in match else value != match["value"]):
            return "source checkpoint changed"
    for key in scope.get("until_steps", []):
        pid, sid = key.split(".", 1)
        if not orch._find_step(pid, sid):
            return "unknown closing step"
        if st.is_done(pid, sid):
            return "closing step complete"
    if not acknowledgement:
        if scope.get("date") and orch.now_local.date().isoformat() != scope["date"]:
            return "notification day expired"
        if scope.get("not_before") or scope.get("expires_at"):
            current = orch.now_local
            if current.tzinfo is None:
                current = current.replace(tzinfo=orch.tz or timezone.utc)
            if scope.get("not_before") and current < datetime.fromisoformat(scope["not_before"]):
                return "notification not due"
            if scope.get("expires_at") and current >= datetime.fromisoformat(scope["expires_at"]):
                return "notification deadline expired"
    return ""


def descriptor(st, logical_id, scope, tool, payload, completion=None):
    if tool not in TRANSPORTS:
        raise ValueError(f"Notification transport has no delivery contract: {tool}")
    channel, keys = TRANSPORTS[tool]
    payload = deepcopy(payload)
    target = {k: (st.owner_email if k == "owner" else payload.get(k)) for k in keys}
    if any(not v for v in target.values()):
        raise ValueError(f"{logical_id}: missing {channel} destination")
    for key in ("to", "cc", "bcc", "attendees"):
        if key in payload and (not isinstance(payload[key], list)
                               or any(not isinstance(v, str) or "@" not in v for v in payload[key])):
            raise ValueError(f"{logical_id}: invalid recipient list {key}")
    if any(not isinstance(v, str) or not v.strip() for k, v in target.items()
           if k not in ("to", "attendees")):
        raise ValueError(f"{logical_id}: invalid {channel} destination")
    value = {
        "id": f"{logical_id}:{channel}", "release": st.release_id,
        "scope": deepcopy(scope), "channel": channel, "target": target,
        "tool": tool, "payload": payload, "completion": deepcopy(completion or {}),
    }
    value["hash"] = fingerprint(value)
    return value


def validate_record(orch, record):
    """Old/incomplete or corrupted records require deliberate recovery, never a resend."""
    if is_progress_receipt(record):
        return receipt_descriptor(orch, record)
    item = record.get("descriptor") if isinstance(record, dict) else None
    fields = {"id", "release", "scope", "channel", "target", "tool", "payload", "completion", "hash"}
    if (not isinstance(item, dict) or not fields.issubset(item)
            or not isinstance(record.get("attempts"), list)
            or record.get("status") not in ("prepared", "claimed", "sent", "not_sent", "uncertain")):
        raise ValueError("Incomplete notification record; owner recovery required")
    if item["release"] != orch.state.release_id:
        raise ValueError("Prepared notification belongs to another release")
    if item["hash"] != fingerprint({k: v for k, v in item.items() if k != "hash"}):
        raise ValueError("Prepared snapshot hash is corrupt; owner recovery required")
    if item["completion"].get("kind") == "step" and (
            item["scope"].get("kind") != "step"
            or not item["scope"].get("step")
            or item["completion"].get("record_as") != item["scope"]["step"]):
        raise ValueError("Notification record_as does not match its owning step; owner recovery required")
    if record["status"] == "prepared" and (record["attempts"] or record.get("completion")):
        raise ValueError("Prepared notification has execution evidence; owner recovery required")
    if record["status"] != "prepared" and (not record["attempts"]
            or not all(k in record["attempts"][-1] for k in ("id", "owner", "status", "hash"))
            or record["attempts"][-1]["status"] != record["status"]
            or record["attempts"][-1]["hash"] != item["hash"]):
        raise ValueError("Incomplete notification attempt; owner recovery required")
    return item


def available(orch, item):
    if scope_reason(orch, item["scope"]):
        return False
    old = orch.state.notification_deliveries.get(item["id"])
    return not old or old.get("status") in ("prepared", "not_sent")


def offer(orch, item):
    """Refresh only never-claimed preparation; routine progress needs only its latest preview."""
    ledger = orch.state.notification_deliveries
    candidate = {"descriptor": deepcopy(item), "status": "prepared",
                 "prepared_at": now_iso(), "attempts": []}
    for field in ("delivery_status", "completion_status", "stop_reason", "permission_to_send"):
        candidate["descriptor"].pop(field, None)
    validate_record(orch, candidate)
    old = ledger.get(item["id"])
    if old is None:
        ledger[item["id"]] = candidate
    else:
        previous = validate_record(orch, old)
        if previous["id"] != item["id"]:
            raise ValueError("Notification ledger identity mismatch; owner recovery required")
        if old["status"] == "prepared" and previous["hash"] != item["hash"]:
            if not old.get("prepared_at") or not isinstance(old.get("superseded", []), list):
                raise ValueError("Incomplete preparation history; owner recovery required")
            if not is_routine_progress(previous) or not is_routine_progress(item):
                candidate["superseded"] = [*deepcopy(old.get("superseded", [])), {
                    "descriptor": deepcopy(previous), "prepared_at": old["prepared_at"],
                    "superseded_at": candidate["prepared_at"],
                }]
            ledger[item["id"]] = candidate
    return preview(orch, ledger[item["id"]])


def preview(orch, record):
    item = validate_record(orch, record)
    if is_progress_receipt(record):
        return {**deepcopy(item), "delivery_status": "sent", "completion_status": {"status": "settled"},
                "sent_receipt": deepcopy(record["sent_receipt"]), "stop_reason": "already sent",
                "permission_to_send": False}
    return {**deepcopy(item), "delivery_status": record["status"],
            "completion_status": deepcopy(record.get("completion")),
            "stop_reason": scope_reason(orch, item["scope"]), "permission_to_send": False}


def claim(orch, notification_id, approved_hash, executor):
    record = orch.state.notification_deliveries.get(notification_id)
    if not record or (not is_progress_receipt(record) and (not record.get("descriptor") or "attempts" not in record)):
        raise ValueError("Missing/incomplete preparation; owner recovery required for legacy records")
    item = validate_record(orch, record)
    if item["id"] != notification_id:
        raise ValueError("Notification ledger identity mismatch; owner recovery required")
    if approved_hash != item["hash"]:
        raise ValueError("Approved payload/recipient hash does not match the prepared snapshot")
    if item.get("release") != orch.state.release_id:
        raise ValueError("Prepared notification belongs to another release")
    if record["status"] == "sent":
        return {"status": "already_sent", "permission_to_send": False}
    if record["status"] not in ("prepared", "not_sent"):
        raise ValueError("Delivery already claimed or uncertain; do not resend. Owner review required")
    reason = scope_reason(orch, item["scope"])
    if reason:
        raise ValueError(reason)
    if not executor or not executor.strip():
        raise ValueError("executor/session identifier is required")
    scope = item["scope"]
    execution = {"id": uuid.uuid4().hex, "owner": executor.strip(), "started_at": now_iso()}
    if item["completion"].get("kind") == "step":
        # Reuse the engine reservation; no second independently stealable claim.
        action = NeedsSkill(tool=item["tool"], payload=item["payload"], outbound=True,
                            record_as=item["completion"]["record_as"])
        result = orch.reserve_step(scope["phase"], scope["step"], action, executor)
        if result.kind != "needs_skill":
            raise ValueError(getattr(result, "reason", getattr(result, "note", "Step not eligible")))
        execution = orch.step_execution(scope["phase"], scope["step"])
        step = orch.state.get_step(scope["phase"], scope["step"])
        step.data["_execution"]["notification_id"] = item["id"]
        orch.state.set_step(scope["phase"], scope["step"], step)
    record["status"] = "claimed"
    record["attempts"].append({**execution, "status": "claimed", "hash": item["hash"]})
    return {**deepcopy(item), "execution_id": execution["id"], "permission_to_send": True}


def result(orch, notification_id, execution_id, outcome, evidence, receipt=None, review=False):
    """Record a transport result, never infer success from a lack of errors."""
    record = orch.state.notification_deliveries.get(notification_id)
    if is_progress_receipt(record):
        item = validate_record(orch, record)
        if item["id"] != notification_id or record["sent_receipt"]["execution_id"] != execution_id:
            raise ValueError("Only the owning execution can acknowledge this delivery")
        return False
    if not record or not record.get("attempts"):
        raise ValueError("No delivery claim; legacy acknowledgement is not evidence")
    validate_record(orch, record)
    attempt = record["attempts"][-1]
    if attempt["id"] != execution_id:
        raise ValueError("Only the owning execution can acknowledge this delivery")
    if record["status"] == "sent":
        return False
    if not evidence or not evidence.strip():
        raise ValueError("Explicit transport evidence is required")
    if record["status"] == "uncertain" and not review:
        raise ValueError("Uncertain delivery requires owner-reviewed evidence")
    if record["status"] not in ("claimed", "uncertain"):
        raise ValueError("Execution has already finished")
    if outcome not in ("sent", "not_sent", "uncertain"):
        raise ValueError("Result must be sent, not_sent, or uncertain")
    attempt.update(status=outcome, acknowledged_at=now_iso(), evidence=evidence,
                   receipt=deepcopy(receipt), owner_review=review)
    record["status"] = outcome
    if outcome == "not_sent" and record["descriptor"]["completion"].get("kind") == "step":
        scope = record["descriptor"]["scope"]
        step = orch.state.get_step(scope["phase"], scope["step"])
        if (step.status == "running"
                and step.data.get("_execution", {}).get("id") == execution_id):
            step.data.pop("_execution")
            step.status = "pending"
            orch.state.set_step(scope["phase"], scope["step"], step)
    return True


def has_pending(orch, step_keys):
    for record in orch.state.notification_deliveries.values():
        item = validate_record(orch, record)
        if is_progress_receipt(record):
            continue
        scope = item["scope"]
        if ((record["status"] != "sent" or not record.get("completion"))
                and not scope_reason(orch, scope, acknowledgement=record["status"] == "sent")
                and f'{scope.get("phase")}.{scope.get("step")}' in step_keys):
            return True
    return False


PROTOCOL = (
    "On every run, including silent/terminal producer outcomes, discover saved work with "
    "`notification prepare --release <release> --source pending`. Finalize sent records "
    "whose completion_status is empty; do not resend them. Review eligible prepared/not_sent "
    "records for claim; leave expired/closed work unsent. Surface claimed/uncertain records "
    "for evidence-based owner recovery, never automatic replay. "
    "Ordinary Bug Bash updates use bounded retention: settled sends become unsendable compact "
    "receipts and are omitted from source pending (use --id for a retained receipt); expired "
    "unsent updates and sufficiently old settled receipts are removed. Absence after expiry "
    "is not permission to recreate an old send. Pending claims/uncertain outcomes retain their "
    "full evidence; first/final messages and invitation receipts are not compacted. "
    "Notification payloads are previews, never permission to send. For each notification, "
    "prepare it with `notification prepare --release <release> --source <source>` "
    "(step source also takes --phase/--step and the same --param inputs), review the exact "
    "target/payload, then `notification claim --release <release> --id <id> --hash <hash> "
    "--executor <session-id>`. Send ONLY when permission_to_send is true, using exactly "
    "the returned tool/payload, with no extra courtesy copies. Claims and acknowledgements use "
    "the trusted current clock, never --as-of. A changed preparation can replace only "
    "never-claimed work; review its new hash. Immediately run "
    "`notification result --release <release> --id <id> --execution-id <id> "
    "--outcome sent --evidence <provider-confirmed-success> [--receipt-file <json>]`. "
    "Use not_sent ONLY for positive proof nothing was sent; timeouts/unknown outcomes "
    "are uncertain. Never retry a claimed/uncertain send, including success followed "
    "by failed acknowledgement: retry the acknowledgement only. Owner-reviewed "
    "recovery requires --owner-review and evidence; no age-based stealing. "
    "Completion follows acknowledgement, not preparation. Run "
    "`notification finalize --release <release> --id <id>` if acknowledgement succeeded "
    "but completion failed. Inspect source pending for sent records with "
    "completion.automation.on_demand. Only after completion_status is applied, and while the "
    "named worker's configured lifecycle remains open, provision that slug if unregistered; never resend "
    "to retry provisioning. All exit paths (including silent/terminal/error) MUST execute "
    "automation cleanup; delete live automation first and deregister only after deletion succeeds. "
    "For the Scout bot transport, verify the runner's signed-in user is the descriptor's "
    "owner target BEFORE claiming; otherwise stop, never route another owner's content to yourself."
    " A done outcome with no_delivery_required:true can use record-step --status pass; "
    "the recorder revalidates that no message is needed. Never use it to acknowledge a send."
)
