"""Step: `native_auth_signoff` — the release engineer attests the Native Auth team finished
their tests and signed off (Phase 3, bug_bash).

This is the bug-bash sign-off. No message is
sent — the release engineer simply confirms the Native Auth team completed their tests and
signed off (the confirmation they were asked to send back in `notify_native_auth`). The skill
captures who signed off.

Scout-assisted: build() returns a NeedsSkill telling the skill to confirm the sign-off with
the release engineer and record it via `record-native-auth-signoff --release <id> --by
'<who>'` (or without --by to hold the step until the sign-off arrives). Idempotent — once
recorded it reports done.
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done

ID = "native_auth_signoff"
KIND = "scout"


def native_auth_signed_by(state):
    """Who signed off Native Auth, or None."""
    return (state.get_step("bug_bash", ID).data or {}).get("by")


def build(state):
    if not state.ccd:
        return Blocked("native_auth_signoff: no CCD set — can't identify the release month.")

    who = native_auth_signed_by(state)
    if who:
        return Done(f"Native Auth sign-off recorded for {state.release_id} ({who}).")

    month_year = schedule.parse_date(state.ccd).strftime("%B %Y")
    instructions = (
        f"Confirm the Native Auth sign-off for the {month_year} bug bash.\n"
        f"1. Check with the release engineer that the Native Auth team finished their tests "
        f"and signed off (this is the confirmation the Native Auth RE was asked to send back "
        f"in notify_native_auth). Capture who signed off.\n"
        f"2. Record it: `record-native-auth-signoff --release {state.release_id} --by "
        f"'<who>'`. If it hasn't come in yet, run WITHOUT --by to hold the step.")

    return NeedsSkill(
        tool="record-native-auth-signoff",
        payload={
            "release": state.release_id,
            "followup_command": (f"record-native-auth-signoff --release {state.release_id} "
                                 f"--by '<who>'"),
            "_gather": {"month_year": month_year, "instructions": instructions},
        },
        record_as=ID,
        summary=f"Attest the Native Auth team signed off for the {month_year} bug bash",
        note="awaiting Native Auth sign-off (release engineer attests)",
    )
