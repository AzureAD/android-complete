"""Step: `did_signoff` — reach out to the DID point of contact for the DID sign-off
(Phase 3, bug_bash).

The second of the two bug-bash sign-offs (the first is `native_auth_signoff`). Scout sends a
1:1 Teams message to the DID point of contact (Sowmya Malayanur) asking for the DID sign-off,
then records it when it comes back. The step completes once the DID sign-off is captured.

Scout-assisted: build() composes the DID request and instructs the skill to send it to the
DID contact, then record via `record-did-signoff --release <id> --by '<who>'` (or without
--by to hold the step). Idempotent — once recorded it reports done without re-sending.

Depends on clone_plans_broker (for the test-plan link).
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from tools import testplans as T

ID = "did_signoff"
KIND = "scout"

# DID (Directory / Identity) sign-off point of contact.
DID_CONTACT = {"name": "Sowmya Malayanur", "email": "Sowmya.Malayanur@microsoft.com"}


def did_signed_by(state):
    """Who gave the DID sign-off, or None."""
    return (state.get_step("bug_bash", ID).data or {}).get("by")


def _did_request_html(month_year, plan_url):
    plan_line = (f' Test plan: <a href="{plan_url}">{month_year} Broker test plan</a>.'
                 if plan_url else "")
    return (
        f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
        f'<p>Hi {DID_CONTACT["name"].split()[0]} \u2014 the <b>{month_year} Android release '
        f'Bug Bash</b> testing is complete on our side (Broker + Native Auth).</p>'
        f'<p>Could you please provide the <b>DID sign-off</b> when you get a chance?{plan_line}'
        f' Thanks!</p></div>')


def build(state):
    if not state.ccd:
        return Blocked("did_signoff: no CCD set — can't identify the release month.")

    who = did_signed_by(state)
    if who:
        return Done(f"DID sign-off recorded for {state.release_id} ({who}).")

    month_year = schedule.parse_date(state.ccd).strftime("%B %Y")
    broker_plan = (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")
    plan_url = T.plan_web_url(broker_plan) if broker_plan else ""
    html = _did_request_html(month_year, plan_url)

    instructions = (
        f"Request the DID sign-off for the {month_year} bug bash.\n"
        f"1. Send the message below to the DID point of contact ({DID_CONTACT['name']}, "
        f"{DID_CONTACT['email']}) as a 1:1 Teams message (`workiq_create_chat_by_email` "
        f"\u2192 `workiq_send_chat_message`, contentType html).\n"
        f"2. When the DID sign-off comes back, record it: `record-did-signoff --release "
        f"{state.release_id} --by '<who>'`. If it's still pending, run WITHOUT --by to hold "
        f"the step (the request having been sent).")

    return NeedsSkill(
        tool="record-did-signoff",
        payload={
            "release": state.release_id,
            "content": html,
            "contentType": "html",
            "did_contact": DID_CONTACT,
            "followup_command": (f"record-did-signoff --release {state.release_id} "
                                 f"--by '<who>'"),
            "_gather": {"month_year": month_year, "did_contact": DID_CONTACT,
                        "instructions": instructions},
        },
        record_as=ID,
        summary=(f"Request the DID sign-off from {DID_CONTACT['name']} for the "
                 f"{month_year} bug bash"),
        note="awaiting DID sign-off (from Sowmya Malayanur)",
        outbound=True,
    )
