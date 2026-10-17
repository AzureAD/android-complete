"""Step: `native_auth_signoff` — the release owner attests they received the Native Auth
team's sign-off (Phase 3, bug_bash).

Pure human attestation — there is NO outbound action for Scout to take. The Native Auth
release engineer was notified in `notify_native_auth` (and their name captured there); they
run their own tests and report sign-off back to the owner. Here the release owner simply
CONFIRMS that sign-off was received. No message is sent. The source binding records
the release, owner, notified engineer and Broker plan that the confirmation covers.
Changed evidence requires a fresh confirmation rather than reusing old sign-off.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import NeedsHuman, Blocked
from orchestrator.authority import OwnStepData
from orchestrator.evidence import StepData
from orchestrator.delivery import fingerprint
from steps.build_verify._common import valid_id
from steps.bug_bash.notify_native_auth import notified_engineer


ID = "native_auth_signoff"
KIND = "attest"
EVIDENCE = (OwnStepData(),)


def build(context: StepContext):
    notice = context.evidence.step("bug_bash", "notify_native_auth")
    plan = context.evidence.step("bug_bash", "clone_plans_broker").data.get("plan_id")
    eng = notified_engineer(context)
    if notice.status != "done" or not isinstance(eng, str) or not eng.strip() or not valid_id(plan):
        return Blocked("Native Auth sign-off needs the completed release-specific notification, "
                       "release engineer and Broker plan identity first.")
    record = context.evidence.step("bug_bash", ID)
    data = thaw(record.data)
    notification_id = notice.data["notification_id"]
    delivery = context.evidence.notification_deliveries[notification_id]
    binding = fingerprint({
        "release": context.release.release_id, "ccd": context.release.ccd,
        "owner": context.release.owner_email, "engineer": eng, "plan_id": plan,
        "notification_id": notification_id,
        "notification_execution_id": notice.data["notification_execution_id"],
        "descriptor_hash": delivery["descriptor"]["hash"],
        "engineer_source": notice.data["engineer_source"],
    })
    if (data.get("attestation_binding") and data["attestation_binding"] != binding
            and not record.invalidated_at):
        return Blocked("The Native Auth sign-off request changed. Reopen this confirmation "
                       "and obtain sign-off for the current release engineer and plan.")
    data.update(attestation_binding=binding, gate_preconditions=[
        {"path": ["release_id"], "value": context.release.release_id},
        {"path": ["ccd"], "value": context.release.ccd},
        {"path": ["owner_email"], "value": context.release.owner_email},
        {"path": ["steps", "bug_bash.notify_native_auth", "status"], "value": "done"},
        {"path": ["steps", "bug_bash.notify_native_auth", "data", "engineer"], "value": eng},
        {"path": ["steps", "bug_bash.notify_native_auth", "data", "notification_id"],
         "value": notice.data["notification_id"]},
        {"path": ["steps", "bug_bash.notify_native_auth", "data", "notification_execution_id"],
         "value": notice.data["notification_execution_id"]},
        {"path": ["steps", "bug_bash.notify_native_auth", "data", "engineer_source"],
         "value": notice.data["engineer_source"]},
        {"path": ["notification_deliveries", notification_id, "descriptor", "hash"],
         "value": delivery["descriptor"]["hash"]},
        {"path": ["notification_deliveries", notification_id, "status"], "value": "sent"},
        {"path": ["notification_deliveries", notification_id, "completion", "status"], "value": "applied"},
        {"path": ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"], "value": plan},
    ])
    who = f"the Native Auth release engineer ({eng}) for Broker plan {plan}"
    return NeedsHuman(
        prompt=(
            f"Confirm you've received the bug-bash sign-off from {who}.\n"
            "They were asked to run their tests and report back. Once their sign-off comes in, "
            "just let me know and I'll mark this complete for you. If it hasn't arrived yet, "
            "leave it holding."),
        attest=True, updates=(StepData(data),),
    )
