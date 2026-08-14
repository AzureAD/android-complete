"""Step: `vitals` — attest Play Console vitals & policy reviewed (Phase 0, S9).

Play Console has no API for Policy issues/warnings (the Reporting API covers only
technical vitals; the Console UI is behind a Google login Scout can't automate), so
this is a human attestation. No engine action and no MCP call → a `NeedsHuman`
outcome: `build()` returns the exact prompt for the owner to review and confirm; on
confirmation the skill clears it with `done --step vitals`.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsHuman

ID = "vitals"
KIND = "attest"


def build(state):
    return NeedsHuman(
        prompt=(
            "Open Google Play Console and confirm the app is healthy before I proceed:\n"
            "  • Android vitals — crash rate & ANR rate within acceptable bounds (no regression)\n"
            "  • Policy status — no open policy issues or warnings\n"
            "If both look acceptable, I'll mark it done (`done --step vitals`). "
            "An unresolved policy issue or a vitals regression → leave it holding."),
        attest=True,
    )
