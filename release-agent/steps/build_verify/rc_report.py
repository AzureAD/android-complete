"""Step: `rc_report` — email the RC verification report to the release owner
(Phase 2, build_verify), right before the go_test approval gate.

When the four verification steps have resolved the chain, this step composes the
Phase-2 RC report (checker → orchestrator → ECS/Local MRWP + per-run test failures)
from LIVE pipeline data and emails it to the release owner, so the engineer wakes to
the report on CCD+1 and can review before approving `go_test`.

Sending email needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` composes the email deterministically and returns a
NeedsSkill(workiq_send_email) for the skill to send. Redirect for tests with the
`send_to` payload knob (keeps the send real, points it at you).
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
    owner email is unknown (nowhere to send) — set it with `set-owner`."""
    to = state.owner_email
    if not to:
        return Blocked(
            "rc_report: no release owner email on record — set it with "
            "`set-owner --email <you@microsoft.com>` so the RC report can be sent.")
    try:
        subject, html, plain, model = K.rc_email(state)
    except Exception as e:                       # pragma: no cover - defensive
        return Blocked(f"rc_report: could not build the RC report ({e}).")

    probs = model.get("problems") or []
    tail = f"; {len(probs)} blocking issue(s)" if probs else ""
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": [to],
            "subject": subject,
            "body": html,
            "isHtml": True,
            "_plain_body": plain,
        },
        record_as=ID,
        summary=f"Email the RC verification report to the release owner ({to}){tail}",
        note=f"RC report emailed to {to}",
        outbound=True,
    )
