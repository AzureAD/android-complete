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
NAME = 'Native Auth team sign-off (attest)'
REQUIRED_TOOLS = ()
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

KNOWLEDGE = {'summary': "The release owner attests they received the Native Auth team's sign-off.",
 'what': 'A pure human attestation (owner: human, attest). No message is sent and no new data '
         'is captured — the release owner confirms they received the bug-bash sign-off from '
         "the Native Auth team. The engineer's name is reused from notify_native_auth (where "
         'they were notified), so the prompt names who to expect the sign-off from; nothing is '
         're-collected here.\n',
 'who': 'The release owner attests. Scout takes no action — there is nothing to run.',
 'how': 'The step holds with a confirmation prompt naming the Native Auth RE from '
        'notify_native_auth. The owner does NOT touch the engine — once the sign-off has come '
        'in, they tell Scout and Scout clears the step (`done --step native_auth_signoff`). If '
        "it hasn't arrived yet, leave it holding.\n",
 'faqs': [{'q': 'What is this confirming?',
           'a': "That the release owner received the Native Auth team's sign-off — the "
                'confirmation the RE (named in notify_native_auth) was asked to send back.'},
          {'q': "Why isn't Scout doing this one?",
           'a': "There's no outbound action or new data to capture — it's an attestation the "
                "release owner makes, like the preflight vitals check. Scout can't receive the "
                "sign-off on the owner's behalf."}]}
