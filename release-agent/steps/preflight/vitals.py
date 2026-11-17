"""Step: `vitals` — attest Play Console vitals & policy reviewed (Phase 0, S9).

Play Console has no API for Policy issues/warnings (the Reporting API covers only
technical vitals; the Console UI is behind a Google login Scout can't automate), so
this is a human attestation. No engine action and no MCP call → a `NeedsHuman`
outcome: `build()` returns the exact prompt for the owner to review and confirm; on
confirmation the skill clears it with `done --step vitals`.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsHuman

ID = "vitals"
KIND = "attest"
NAME = 'Confirm Play Console vitals & policy status reviewed'
REQUIRED_TOOLS = ()


def build(context: StepContext):
    return NeedsHuman(
        prompt=(
            "Open Google Play Console and confirm the app is healthy before I proceed:\n"
            "  • Android vitals — left panel → Monitor and improve → Android vitals → Overview: "
            "crash rate & ANR rate within acceptable bounds (no regression)\n"
            "  • Policy status — left panel → Monitor and improve → Policy and programs → Policy "
            "status: no open policy issues or warnings\n"
            "If both look acceptable, I'll mark it done. "
            "An unresolved policy issue or a vitals regression → leave it holding."),
        attest=True,
    )

KNOWLEDGE = {'summary': 'Attest that Google Play Console app health (Android vitals + Policy status) is '
            'acceptable.',
 'what': 'Play Console has no API for policy status, so this is a manual confirmation: review '
         "the app's technical health (crash rate / ANR rate) and its policy standing (open "
         'issues or warnings), and confirm both are acceptable before code complete.\n',
 'where': ["Android vitals: Play Console → left panel → 'Monitor and improve' → 'Android "
           "vitals' → Overview.",
           "Policy status: Play Console → left panel → 'Monitor and improve' → 'Policy and "
           "programs' → 'Policy status'."],
 'how': 'Open both pages, confirm crash/ANR are within acceptable bounds (no regression) and '
        'there are no open policy issues/warnings, then mark the step done. An unresolved '
        'policy issue or a vitals regression → leave it holding.\n',
 'links': [{'name': 'Google Play Console', 'url': 'https://play.google.com/console'}],
 'faqs': [{'q': 'Where do I find Android vitals?',
           'a': 'Play Console → left panel → Monitor and improve → Android vitals → Overview.'},
          {'q': 'Where is policy status?',
           'a': 'Play Console → left panel → Monitor and improve → Policy and programs → '
                'Policy status.'}]}
