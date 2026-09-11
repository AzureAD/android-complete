"""Bounded retention for ordinary Bug Bash updates, not first/final or other notifications.

Pending/uncertain deliveries keep their exact approved snapshot. Settled routine sends
keep a small receipt for one day beyond expiry, covering all supported (<=24h) cadence
changes. Unsent expired updates need no receipt. Referenced records are never rewritten.
"""
from __future__ import annotations

from datetime import datetime, timedelta

_PREFIX = "bugbash:update:"
_SCOPE = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates", "until_flag": "poll_complete"}
_RECEIPT_GRACE = timedelta(days=1)


def _date(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _window(notification_id, expires_at):
    if (not isinstance(notification_id, str) or not notification_id.startswith(_PREFIX)
            or not notification_id.endswith(":teams")):
        return None
    start = _date(notification_id[len(_PREFIX):-len(":teams")])
    end = _date(expires_at)
    if start is None or end is None or not timedelta(0) < end - start <= timedelta(days=1):
        return None
    return start, end


def is_routine_progress(item):
    """Only the existing no-side-effect, expiring progress-message contract opts in."""
    if not isinstance(item, dict):
        return False
    scope, payload, target = item.get("scope"), item.get("payload"), item.get("target")
    return (isinstance(scope, dict) and isinstance(payload, dict) and isinstance(target, dict)
            and all(scope.get(k) == v for k, v in _SCOPE.items())
            and set(scope) <= set(_SCOPE) | {"state_matches", "expires_at"}
            and item.get("completion") == {} and item.get("tool") == "workiq_send_chat_message"
            and item.get("channel") == "teams" and bool(target.get("chatId"))
            and target == {"chatId": payload.get("chatId")}
            and _window(item.get("id"), scope.get("expires_at")) is not None)


def is_progress_receipt(record):
    return isinstance(record, dict) and "sent_receipt" in record


def receipt_descriptor(orch, record):
    """Validate a settled receipt and expose only enough context for an unsendable preview."""
    receipt = record.get("sent_receipt")
    required = {"id", "release", "hash", "chatId", "execution_id", "sent_at", "expires_at"}
    if (set(record) != {"status", "sent_receipt"} or record["status"] != "sent"
            or not isinstance(receipt, dict) or not required <= set(receipt)
            or not all(isinstance(receipt[k], str) and receipt[k].strip() for k in required)
            or receipt["release"] != orch.state.release_id
            or len(receipt["hash"]) != 64 or any(c not in "0123456789abcdef" for c in receipt["hash"])
            or _date(receipt["sent_at"]) is None
            or _window(receipt["id"], receipt["expires_at"]) is None
            or not any(isinstance(receipt.get(k), str) and receipt[k].strip()
                       for k in ("message_id", "evidence"))):
        raise ValueError("Invalid compact progress receipt; owner recovery required")
    return {"id": receipt["id"], "release": receipt["release"], "hash": receipt["hash"],
            "channel": "teams", "target": {"chatId": receipt["chatId"]},
            "tool": "workiq_send_chat_message", "scope": {**_SCOPE, "expires_at": receipt["expires_at"]},
            "completion": {}}


def _referenced_ids(state):
    ledger = state.notification_deliveries
    protected = set()
    for record in ledger.values():
        if not isinstance(record, dict):
            continue
        descriptors = [record.get("descriptor") or {}]
        descriptors += [row.get("descriptor") or {} for row in record.get("superseded", [])
                        if isinstance(row, dict)]
        for descriptor in descriptors:
            for match in (descriptor.get("scope") or {}).get("state_matches", []):
                path = match.get("path") if isinstance(match, dict) else None
                if isinstance(path, list) and path and path[0] == "notification_deliveries":
                    protected.update(ledger if len(path) == 1 else [path[1]])
    for step in state.steps.values():
        notification_id = ((step.get("data") or {}).get("_execution") or {}).get("notification_id")
        if notification_id:
            protected.add(notification_id)
    return protected


def prune_progress(orch):
    """Return whether retention changed state; caller persists under the normal state lock."""
    from orchestrator import delivery as D

    now = _date(D.now_iso())  # Trusted clock, never a caller's --now/--as-of.
    if now is None:
        raise ValueError("Current delivery clock must include a timezone")
    ledger, protected = orch.state.notification_deliveries, _referenced_ids(orch.state)
    changes = {}
    for key, record in ledger.items():
        if key in protected:
            continue
        compact = is_progress_receipt(record)
        if not compact and not is_routine_progress(record.get("descriptor") if isinstance(record, dict) else None):
            continue
        item = D.validate_record(orch, record)
        if item["id"] != key:
            raise ValueError("Notification ledger identity mismatch; owner recovery required")
        expires = _date(item["scope"]["expires_at"])
        if compact:
            if now >= expires + _RECEIPT_GRACE:
                changes[key] = None
        elif record["status"] in ("prepared", "not_sent") and now >= expires:
            changes[key] = None
        elif record["status"] == "sent" and (record.get("completion") or {}).get("status") in ("applied", "suppressed"):
            attempt = record["attempts"][-1]
            receipt = {"id": key, "release": item["release"], "hash": item["hash"],
                       "chatId": item["target"]["chatId"], "execution_id": attempt["id"],
                       "sent_at": attempt.get("acknowledged_at"), "expires_at": item["scope"]["expires_at"]}
            provider = attempt.get("receipt")
            message_id = provider.get("id") if isinstance(provider, dict) else None
            if isinstance(message_id, (str, int)) and not isinstance(message_id, bool) and str(message_id).strip():
                receipt["message_id"] = str(message_id)
            else:
                receipt["evidence"] = attempt.get("evidence")
            reduced = {"status": "sent", "sent_receipt": receipt}
            receipt_descriptor(orch, reduced)
            changes[key] = None if now >= expires + _RECEIPT_GRACE else reduced
        elif record["status"] == "prepared" and record.get("superseded"):
            changes[key] = {k: v for k, v in record.items() if k != "superseded"}
    for key, value in changes.items():
        if value is None:
            del ledger[key]
        else:
            ledger[key] = value
    return bool(changes)
