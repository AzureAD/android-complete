"""Step: `rc_report` — consolidate the RC data, email the report, and make the Phase-2
go/hold decision (Phase 2, build_verify). This is the terminal Phase-2 step and the single
decision point — the verification steps only CAPTURE data; this step decides. No separate
human approval gate.

When the four verification steps have resolved the chain, this step composes the
Phase-2 RC report (checker → orchestrator → ECS/Local MRWP + per-run test failures)
from LIVE pipeline data and emails it to the release owner, so the engineer wakes to
the report on CCD+1. The report is ALWAYS sent (the owner gets the dashboard of
failures + links either way). The step's OUTCOME is then decided by TWO independent gates:
(1) the three-tier MRWP UI gate on the combined UI-automation pass rate across both MRWP
runs (100% clean; >= RC_UI_PASS_THRESHOLD (90%) but < 100% warn — proceed + investigate in
parallel; < 90% hold); and (2) the Authenticator ECS gate (both Firebase suites >= 90% and
the auth build succeeded). The release AUTO-ADVANCES into Phase 3 only when BOTH gates clear;
if EITHER holds, the step records `attention` and BLOCKS (the release WAITS for human
attestation).

Sending email needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` composes the email deterministically and returns a
NeedsSkill(workiq_send_email); the payload names the `record-rc-report` follow-up
command, which re-reads the model, applies BOTH gates, records pass|attention, and
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
    auth = K.auth_report_gate(model)
    v = gate["verdict"]
    if v == "clean":
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate CLEAN (100%)")
    elif v == "warn":
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate PASS with warning ({gate['pass_pct']}%); proceed + investigate "
                   f"failing UI tests in parallel")
    else:
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate FAIL ({gate['pass_pct']}% < {int(K.RC_UI_PASS_THRESHOLD)}%); "
                   f"will hold for investigation")
    if auth["present"]:
        summary += (f" · Auth ECS {'PASS' if auth['verdict'] == 'clean' else 'HOLD'}")
    note = gate["detail"] + (f"\n\n{auth['detail']}" if auth["present"] else "")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": [to],
            "subject": subject,
            "body": html,
            "isHtml": True,
            "_plain_body": plain,
            # After sending, DON'T blind-record pass: run this engine command instead — it
            # consolidates the MRWP UI gate AND the Authenticator-ECS gate (pass|attention)
            # and stashes the run links.
            "followup_command": "record-rc-report",
        },
        record_as=ID,
        summary=summary,
        note=note,
        outbound=True,
    )


def automation_prompt(release: str, spec: dict) -> str:
    """Bespoke instruction for the interval RC poller (owned here, like localization's).
    Only the poller shape is used — rc_report has no time-of-day automation."""
    if not spec.get("interval"):
        return ""       # rc_report is driven by next/tick, not a one-shot automation
    return (
        f"Release {release} — RC verification poller (Phase 2, every 30 min).\n"
        f"Only act if Build & RC Verification is holding on an IN-FLIGHT re-triggered RC "
        f"(the human ran `rc-retriggered`). Poll it once:\n"
        f"1. run `poll-rc --release {release}`.\n"
        f"2. act on the printed decision:\n"
        f"   • waiting  → still running; send nothing.\n"
        f"   • nudge    → running past 6h; send the courtesy heads-up in decision.nudge "
        f"(email decision.nudge.email to the owner AND post decision.nudge.teams.text to "
        f"the owner's Scout chat). It is stamped, so it goes out at most once.\n"
        f"   • resolved → the new RC completed and PASSED the gate; Phase 2 advanced. "
        f"Deregister THIS poller (`automation deregister --id <this automation's id>`) — "
        f"it is no longer needed — and report the pass.\n"
        f"   • blocked  → the new RC completed but re-blocked the UI gate (still failing). "
        f"Surface the block to the owner (the 3-exit choice: re-trigger / cherry-pick / "
        f"override) and leave the poller in place for the next re-trigger.\n"
        f"   • idle     → nothing in-flight; stay silent.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"rc-poller: <decision>\"`. Stay silent when there is nothing to send.")
