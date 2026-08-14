"""Step: `confirm_reminders` — attest feature-owner work is done (Phase 0, S3/S4/S5/S6).

Sending the flight & string reminders is fire-and-forget — it doesn't prove the
work landed. This attest step HOLDS until the owner confirms the reminded work is
actually done. No engine action and no MCP call, so it's a `NeedsHuman` outcome:
`build()` returns the exact confirmation prompt for the skill to put in front of
the owner; on confirmation the skill clears it with `done --step confirm_reminders`.

Depends on `flight_reminder` (only surfaces once the reminders were sent).
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsHuman
from steps.lib.context import release_ctx

ID = "confirm_reminders"
KIND = "attest"


def build(state):
    month = release_ctx(state)["month"] if state.ccd else "this"
    return NeedsHuman(
        prompt=(
            f"Confirm feature owners completed the {month}-release pre-code-complete work "
            f"before I proceed:\n"
            f"  • [Broker] local flights updated in the `release` variable group\n"
            f"  • [Broker & Auth App] every default-true flight has a flight pre-mortem doc\n"
            f"  • [Auth App] all user-facing-string PRs merged by CCD-7 (late strings called out)\n"
            f"  • [Auth App] features default-OFF (any default-ON approved in the release wiki)\n"
            f"If confirmed, I'll mark it done (`done --step confirm_reminders`). "
            f"If they can't confirm, it stays holding."),
        attest=True,
    )
