"""Owner-reviewed closure of a proven, differently delivered daily email.

The delivery remains uncertain, frozen and ineligible for retry or completion.
This records an incident disposition, never an exact-payload send receipt.
"""
from copy import deepcopy
from dataclasses import asdict
from difflib import SequenceMatcher

from orchestrator import delivery as D, revision


def _eligible(record):
    item = record["descriptor"]
    return (
        record["status"] == "uncertain"
        and item["tool"] == "workiq_send_email"
        and item["scope"].get("kind") == "window"
        and item["id"] == f'status-email:{item["scope"].get("date")}:email'
        and item["completion"].get("kind") is None
        and item["completion"].get("release_field") == "last_status_email_date"
        and item["completion"].get("date") == item["scope"].get("date")
        and set(item["completion"]) == {"release_field", "date"}
        and not record.get("completion")
    )


def validate_resolution(state, record):
    receipt = record.get("mismatch_resolution")
    if receipt is None:
        return False
    plan = receipt.get("plan") if isinstance(receipt, dict) else None
    item = record["descriptor"]
    attempt = record["attempts"][-1] if record["attempts"] else {}
    if (
        not _eligible(record) or not isinstance(plan, dict)
        or receipt.get("hash") != revision.digest(plan)
        or plan.get("version") != 1 or plan.get("release") != state.release_id
        or plan.get("notification_id") != item["id"]
        or plan.get("descriptor_hash") != item["hash"]
        or plan.get("claimed_payload_hash") != D.fingerprint(item["payload"])
        or plan.get("attempt_hash") != D.fingerprint(attempt)
        or plan.get("execution_id") != attempt["id"]
        or not revision.is_hash("sha256:" + str(plan.get("observed_payload_hash")))
        or plan["observed_payload_hash"] == plan["claimed_payload_hash"]
        or not plan.get("body_changes")
        or not isinstance(state.owner_email, str) or not state.owner_email.strip()
        or receipt.get("by") != state.owner_email
        or any(not isinstance(receipt.get(key), str) or not receipt[key].strip()
               for key in ("at", "reason"))
    ):
        raise ValueError("Invalid daily-email mismatch disposition")
    return True


def preview(orch, notification_id, execution_id, observed, *, registry_entries=()):
    revision.assert_automation_recovery_compatible(orch)
    record = orch.state.notification_deliveries.get(notification_id)
    if not record:
        raise ValueError("Notification not found")
    item = D.validate_record(orch, record)
    if not _eligible(record) or record.get("mismatch_resolution"):
        raise ValueError("Only an unresolved uncertain daily status email can be reviewed")
    attempt = record["attempts"][-1]
    if attempt["id"] != execution_id:
        raise ValueError("Mismatch review must retain the original owning execution")
    actual = observed.get("payload")
    provider = observed.get("provider_receipt")
    if (
        observed.get("tool") != item["tool"] or not isinstance(actual, dict)
        or not isinstance(provider, dict) or provider.get("transport") != item["tool"]
        or provider.get("success") is not True
        or not all(isinstance(provider.get(key), str) and provider[key].strip()
                   for key in ("tool_call_id", "event_id", "evidence"))
    ):
        raise ValueError("Original invocation payload and successful provider evidence are required")
    expected = item["payload"]
    if ({key: value for key, value in actual.items() if key != "body"}
            != {key: value for key, value in expected.items() if key != "body"}):
        raise ValueError("Recipients, subject, format and other non-body fields must match exactly")
    if (not isinstance(actual.get("body"), str) or not isinstance(expected.get("body"), str)
            or actual["body"] == expected["body"]):
        raise ValueError("A proven body difference is required; exact sends use notification result")
    changes = [
        {"expected": expected["body"][a:b], "delivered": actual["body"][c:d]}
        for tag, a, b, c, d in SequenceMatcher(
            None, expected["body"], actual["body"], autojunk=False).get_opcodes()
        if tag != "equal"
    ]
    plan = {
        "version": 1, "release": orch.state.release_id, "notification_id": notification_id,
        "execution_id": execution_id, "descriptor_hash": item["hash"],
        "claimed_payload_hash": D.fingerprint(expected),
        "observed_payload_hash": D.fingerprint(actual),
        "attempt_hash": D.fingerprint(attempt), "provider": deepcopy(provider),
        "body_changes": changes, "state_hash": revision.digest(asdict(orch.state)),
        "registry_hash": revision.digest(registry_entries),
        "source_revision": revision.revision_id(orch.state.workflow_revision),
        "target_revision": revision.revision_id(revision.current_revision(orch)),
    }
    # Show the exact subsequent adoption impact without changing live state.
    before = deepcopy(record)
    try:
        record["mismatch_resolution"] = {
            "plan": deepcopy(plan), "hash": revision.digest(plan),
            "at": D.now_iso(), "by": orch.state.owner_email, "reason": "Preview only",
        }
        adoption = revision.adoption_preview(orch, registry_entries=registry_entries)
    finally:
        record.clear()
        record.update(before)
    return {
        "plan": plan, "review_hash": revision.digest(plan), "adoption_after_resolution": adoption,
        "impact": {
            "delivery_status": "uncertain (preserved)", "resend": False,
            "completion": False, "provider_operations": [], "rollout_started": False,
        },
        "permission_to_send": False,
    }


def resolve(orch, notification_id, execution_id, observed, reviewed_hash, *,
            by, reason, confirm_runner_stopped, registry_entries=()):
    if (by != orch.state.owner_email or not isinstance(reason, str) or not reason.strip()
            or not confirm_runner_stopped):
        raise ValueError("Resolution requires the release owner, reason and stopped-runner confirmation")
    review = preview(orch, notification_id, execution_id, observed,
                     registry_entries=registry_entries)
    if reviewed_hash != review["review_hash"]:
        raise ValueError("Stale mismatch review; preview again")
    record = orch.state.notification_deliveries[notification_id]
    record["mismatch_resolution"] = {
        "plan": deepcopy(review["plan"]), "hash": reviewed_hash,
        "by": by, "reason": reason.strip(), "at": D.now_iso(),
    }
    validate_resolution(orch.state, record)
    return {**review, "resolved": True}
