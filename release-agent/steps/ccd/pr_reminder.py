"""Step: `pr_reminder` — CCD PR-merge reminder to the "Code reviews" chat (Phase 1, P1-1b).

The morning-of Code Complete Day Teams message to the "Code reviews" chat, telling
engineers: every PR required for this release must be MERGED today before the branch
is cut automatically at 11:00 PM; if a required PR won't make it, request either a
CCD delay or a post-CCD cherry-pick (both need Moumita's approval); and merge any
PR carrying user-facing strings before the localization pipeline runs at noon.

Posting to Teams needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` resolves the message + target and returns a
NeedsSkill(workiq_send_chat_message). Redirect for tests with the `send_to` mock
knob (keeps the post real, points it at your own chat). See EXTERNAL-REFERENCES.md
for the fixed chat id.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib import templating as T
from steps.lib.context import release_ctx, resolve_chat_target, SELF_CHAT_ID
from tools.coordinates import coords

ID = "pr_reminder"
KIND = "scout"

# Step config (co-located). Posts to the real "Code reviews" chat (redirect for
# tests via send_to). The two deadlines below are single-sourced here and echoed by
# the `localization` step. fire_at_local is read by the CCD-morning automation.
_CHAT = coords.team("code_reviews")
CONFIG = {
    "live_chat_id": _CHAT["chat"],
    "live_chat_name": _CHAT["name"],
    "fire_at_local": "09:00",                # morning of CCD (automation-driven)
    "branch_cut_local": "11:00 PM",          # release branch auto-cuts at this time
    "localization_local": "noon",            # localization pipeline triggers at this time
    "approver_name": "Moumita Ghosh",        # CCD-delay / cherry-pick approver
    "approver_email": "moghosh@microsoft.com",
}

# Knobs this step exposes to mocks.local.yaml (see `mock-spec`). Keeps the Teams
# post REAL but redirects it — the applier rewrites payload.chatId.
MOCKABLE = {
    "send_to": {
        "kind": "payload", "sets": "chatId", "aliases": {"me": SELF_CHAT_ID, "self": SELF_CHAT_ID},
        "desc": "Post to Teams for real, but to this chat ('me' = your own chat).",
    },
}


def _html(ctx: dict, cfg: dict) -> str:
    """Teams-friendly HTML: today's PR-merge deadline, the escalation path, and the
    localization cutoff."""
    cut = T.esc(cfg.get("branch_cut_local", "11:00 PM"))
    loc = T.esc(cfg.get("localization_local", "noon"))
    approver = T.esc(cfg.get("approver_name", "the release manager"))
    approver_email = cfg.get("approver_email", "")
    approver_ref = (f'{approver} (<a href="mailto:{approver_email}">{T.esc(approver_email)}</a>)'
                    if approver_email else approver)
    return f"""\
<p><b>Code Complete is TODAY — {T.esc(ctx['month'])} Release ({T.esc(ctx['ccd_long'])})</b></p>
<p>Hi all, a few time-sensitive reminders for today:</p>
<ol>
  <li><b>Merge every required PR by {cut}.</b> The release branch is cut
      <b>automatically at {cut}</b> tonight — any required PR not merged by then will
      <b>not</b> be in this release.</li>
  <li><b>If a required PR won't make the {cut} cut,</b> and it must ship, take one of
      these — <b>both require {approver_ref}'s approval</b>:
      <ul>
        <li><b>Request a Code Complete delay</b> (push the branch cut), or</li>
        <li><b>Request a post-CCD cherry-pick</b> (merge to <code>working</code> now,
            cherry-pick into the release branch after the cut).</li>
      </ul></li>
  <li><b>Localization runs at {loc}.</b> The localization pipeline is triggered at
      <b>{loc} today</b> — if your PR adds or changes <b>user-facing strings</b>,
      <b>merge it before {loc}</b> so those strings are picked up for translation.
      Strings merged after {loc} are <b>not guaranteed</b> to be localized this release.</li>
</ol>
<p>Thanks,<br>{T.esc(ctx['owner'])}</p>"""


def build(state):
    """Resolve the reminder into a NeedsSkill(workiq_send_chat_message), or Blocked
    if the release has no CCD."""
    if not state.ccd:
        return Blocked("no CCD set for this release")

    cfg = CONFIG
    ctx = release_ctx(state)
    html = _html(ctx, cfg)
    chat_id, target_note, prefix = resolve_chat_target(
        state, cfg.get("live_chat_id"), cfg.get("live_chat_name", "the group chat"))

    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload={"chatId": chat_id, "content": html, "contentType": "html"},
        record_as=ID,
        summary=f"Post the CCD PR-merge reminder to {target_note}",
        note=f"posted to {target_note}",
        outbound=True,
    )
