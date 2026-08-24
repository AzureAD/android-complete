"""Step: `signoffs` — gather the two release sign-offs (Phase 3, bug_bash).

Two sign-offs close the bug bash before the owner's final attestation (bugbash_complete):

  1. NATIVE AUTH — the release engineer attests the Native Auth team finished their tests and
     signed off (the RE received their confirmation, per notify_native_auth).
  2. DID — reach out to the DID point of contact (Sowmya Malayanur) for the DID sign-off.

Scout-assisted: build() composes the DID sign-off request and instructs the skill to send it
to the DID contact, then record both sign-offs. The step completes only when BOTH are
confirmed; otherwise it holds (the DID request having been sent).

The skill records with `record-signoffs --release <id> --native-auth-by '<who>' --did-by
'<who>'` (both → done; missing either → hold for the owner). Idempotent — once both are
recorded it reports done without re-sending.
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from tools import testplans as T

ID = "signoffs"
KIND = "scout"

# DID (Directory / Identity) sign-off point of contact.
DID_CONTACT = {"name": "Sowmya Malayanur", "email": "Sowmya.Malayanur@microsoft.com"}


def recorded_signoffs(state):
    """(native_auth_by, did_by) recorded on the step, each or None."""
    d = state.get_step("bug_bash", ID).data or {}
    return (d.get("native_auth_by"), d.get("did_by"))


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
        return Blocked("signoffs: no CCD set — can't identify the release month.")

    na_by, did_by = recorded_signoffs(state)
    if na_by and did_by:
        return Done(f"Sign-offs complete for {state.release_id}: Native Auth ({na_by}) + "
                    f"DID ({did_by}).")

    month_year = schedule.parse_date(state.ccd).strftime("%B %Y")
    broker_plan = (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")
    plan_url = T.plan_web_url(broker_plan) if broker_plan else ""
    html = _did_request_html(month_year, plan_url)

    instructions = (
        f"Gather the two {month_year} bug-bash sign-offs, then record them.\n"
        f"1. NATIVE AUTH: confirm with the release engineer that the Native Auth team "
        f"finished their tests and signed off (the RE should have received the confirmation "
        f"requested in notify_native_auth). Capture who signed off.\n"
        f"2. DID: send the message below to the DID point of contact "
        f"({DID_CONTACT['name']}, {DID_CONTACT['email']}) as a 1:1 Teams message "
        f"(`workiq_create_chat_by_email` \u2192 `workiq_send_chat_message`, contentType html), "
        f"requesting the DID sign-off. Capture their sign-off when it comes back.\n"
        f"3. Record when BOTH are in: `record-signoffs --release {state.release_id} "
        f"--native-auth-by '<who>' --did-by '<who>'`. If either is still pending, run "
        f"`record-signoffs --release {state.release_id}` (no flags) to hold the step.")

    return NeedsSkill(
        tool="record-signoffs",
        payload={
            "release": state.release_id,
            "content": html,
            "contentType": "html",
            "did_contact": DID_CONTACT,
            "followup_command": (f"record-signoffs --release {state.release_id} "
                                 f"--native-auth-by '<who>' --did-by '<who>'"),
            "_gather": {"month_year": month_year, "did_contact": DID_CONTACT,
                        "instructions": instructions},
        },
        record_as=ID,
        summary=(f"Attest Native Auth sign-off + request DID sign-off from "
                 f"{DID_CONTACT['name']} for the {month_year} bug bash"),
        note="awaiting sign-offs (Native Auth attest + DID from Sowmya Malayanur)",
        outbound=True,
    )
