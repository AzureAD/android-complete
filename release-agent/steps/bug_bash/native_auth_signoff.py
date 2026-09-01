"""Step: `native_auth_signoff` — the release owner attests they received the Native Auth
team's sign-off (Phase 3, bug_bash).

Pure human attestation — there is NO outbound action for Scout to take. The Native Auth
release engineer was notified in `notify_native_auth` (and their name captured there); they
run their own tests and report sign-off back to the owner. Here the release owner simply
CONFIRMS that sign-off was received. No message is sent and no new data is captured — the
prompt names the engineer from `notify_native_auth` so the owner knows who to expect it from,
and the owner clears it with `done --step native_auth_signoff` (leave it holding if the
sign-off hasn't arrived).
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsHuman


ID = "native_auth_signoff"
KIND = "attest"


def _notified_engineer(state):
    """The Native Auth RE captured in notify_native_auth, or None."""
    return (state.get_step("bug_bash", "notify_native_auth").data or {}).get("engineer")


def build(state):
    eng = _notified_engineer(state)
    who = (f"the Native Auth RE ({eng}, notified in notify_native_auth)" if eng
           else "the Native Auth team (see notify_native_auth for who was notified)")
    return NeedsHuman(
        prompt=(
            f"Confirm you've received the bug-bash sign-off from {who}.\n"
            "They were asked to run their tests and report back. Once their sign-off has come "
            "in, mark it done (`done --step native_auth_signoff`). If it hasn't arrived yet, "
            "leave it holding."),
        attest=True,
    )


