"""Step: `rc_report` — email the RC verification report to the release owner AND apply
the Phase-2 UI-automation quality gate (Phase 2, build_verify), right before the
go_test approval gate.

When the four verification steps have resolved the chain, this step composes the
Phase-2 RC report (checker → orchestrator → ECS/Local MRWP + per-run test failures)
from LIVE pipeline data and emails it to the release owner, so the engineer wakes to
the report on CCD+1. The report is ALWAYS sent (the owner gets the dashboard of
failures + links either way). The step's OUTCOME is then decided by the UI gate: if the
UI-automation pass rate across both MRWP runs is >= RC_UI_PASS_THRESHOLD (90%) it
records `pass` and the flow advances to go_test; below the bar it records `attention`
(the step BLOCKS) so the owner investigates the large failure (usually a fix + MRWP
re-run) before proceeding.

Sending email needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` composes the email deterministically and returns a
NeedsSkill(workiq_send_email); the payload names the `record-rc-report` follow-up
command, which re-reads the model, applies the gate, records pass|attention, and
stashes the evaluated run links on the step. Redirect for tests with the `send_to`
payload knob (keeps the send real, points it at you).
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.build_verify import _common as K

ID = "rc_report"
KIND = "scout"

MOCKABLE = {
    "send_to": {
        "kind": "payload", "sets": "to", "as": "list", "tag_subject": True,
        "desc": "Send the RC report for real, but only to these address(es) (owner → you).",
    },
}


def build(state):
    """Compose the RC verification email → NeedsSkill(workiq_send_email). Blocks if the
    owner email is unknown (nowhere to send) — set it with `set-owner`. The email is
    always sent; the UI gate verdict (recorded by the `record-rc-report` follow-up) then
    decides whether the step passes or blocks."""
    to = state.owner_email
    if not to:
        return Blocked(
            "rc_report: no release owner email on record — set it with "
            "`set-owner --email <you@microsoft.com>` so the RC report can be sent.")
    try:
        subject, html, plain, model = K.rc_email(state)
    except Exception as e:                       # pragma: no cover - defensive
        return Blocked(f"rc_report: could not build the RC report ({e}).")

    gate = K.rc_ui_gate(model)
    if gate["verdict"] == "pass":
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"UI gate PASS")
    else:
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"UI gate FAIL ({gate['pass_pct']}% < {int(K.RC_UI_PASS_THRESHOLD)}%); "
                   f"will block for investigation")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": [to],
            "subject": subject,
            "body": html,
            "isHtml": True,
            "_plain_body": plain,
            # After sending, DON'T blind-record pass: run this engine command instead —
            # it applies the 90% UI gate (pass|attention) and stashes the run links.
            "followup_command": "record-rc-report",
        },
        record_as=ID,
        summary=summary,
        note=gate["detail"],
        outbound=True,
    )
