"""Step: `flight_reminder` — combined feature-owner reminders (Phase 0, S3/S4/S5).

Three checklist reminders (local flights · flight pre-mortems · user-facing
strings by CCD-7 · feature-flag freeze / default-OFF review) folded into ONE
Teams message to the Android Core Team. Posting to Teams needs the WorkIQ MCP the
engine can't reach, so this is a `scout` step: `build()` resolves the message +
target and returns a NeedsSkill(workiq_send_chat_message) for the skill to send.

Posts to the configured Android Core Team group chat. To test, the engineer's
`mocks.local.yaml` `send_to` knob redirects it to their own chat. See
EXTERNAL-REFERENCES.md for the fixed links.
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib import templating as T
from steps.lib.context import release_ctx, resolve_chat_target, SELF_CHAT_ID
from tools.coordinates import coords

ID = "flight_reminder"
KIND = "scout"

# Step config (co-located). Posts to the real Android Core Team group chat
# (redirect for tests with the send_to mock knob).
_CHAT = coords.team("android_core")
CONFIG = {
    "live_chat_id": _CHAT["chat"],
    "live_chat_name": _CHAT["name"],
    "links": {
        "variable_group": "https://identitydivision.visualstudio.com/Engineering/_library?itemType=VariableGroups&view=VariableGroupView&variableGroupId=40&path=release",
        "premortem_example": "https://microsoft-my.sharepoint-df.com/:w:/p/rapong/cQpEZp0cXp1sQYo4A4M3PQWCEgUCDj364FJa-rq-msg59WlBsw",
        "localization": "https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/combined-release-checklist/localization",
        "ecs_flight_history": "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android?path=/PhoneFactor/ExperimentationLibrary/src/main/java/com/microsoft/authenticator/experimentation/ecs/entities/EcsFlight.kt&version=GBworking",
    },
}

# Knobs this step exposes to mocks.local.yaml (see `mock-spec`). Keeps the Teams
# post REAL but redirects it — the applier rewrites payload.chatId.
MOCKABLE = {
    "send_to": {
        "kind": "payload", "sets": "chatId", "aliases": {"me": SELF_CHAT_ID, "self": SELF_CHAT_ID},
        "desc": "Post to Teams for real, but to this chat ('me' = your own chat).",
    },
}


def _html(ctx: dict, links: dict) -> str:
    """Teams-friendly HTML for the combined feature-owner reminders."""
    vg = links.get("variable_group", "#")
    pm = links.get("premortem_example", "#")
    loc = links.get("localization", "#")
    ecs = links.get("ecs_flight_history", "#")
    return f"""\
<p><b>Flight &amp; String Reminders — {T.esc(ctx['month'])} Release</b></p>
<p>Hi Android Core Team, four reminders as we approach code complete (<b>{T.esc(ctx['ccd_long'])}</b>):</p>
<ol>
  <li><b>[Broker] Update local flights.</b> Feature owners — please update your local flights in the <code>release</code> variable group. The release engineer will <b>not</b> update these directly; verify your values are current before we proceed. (<a href="{vg}">Variable group</a>)</li>
  <li><b>[Broker &amp; Auth App] Flight pre-mortem docs.</b> It is each Feature Owner's <b>sole responsibility</b> to ensure every flight shipping with a default value of <code>true</code> has a flight pre-mortem doc — explaining the change's purpose and how we plan to monitor it after the release hits PROD. (<a href="{pm}">Example pre-mortem doc</a>)</li>
  <li><b>[Auth App] Merge user-facing strings by today (CCD−7).</b> It is each Feature Owner's <b>sole responsibility</b> to ensure every Authenticator PR with a new or modified user-facing string is <b>merged by today ({T.esc(ctx['ccd7_date'])})</b>. Strings landing inside the 1-week window are <b>not guaranteed</b> to be localized — call out any late strings and escalate to the team lead if not resolved by end of day. (<a href="{loc}">Localization Instructions</a>)</li>
  <li><b>[Auth App] Feature-flag freeze &amp; default-OFF review.</b> Review all features added since the last release and ensure they are <b>default OFF</b>; any feature rolling out <b>default ON</b> requires explicit <b>Team Lead / Engineering Manager approval documented in the release wiki</b>, and the release should be <b>blocked</b> if such approval is missing. <b>No feature work is allowed after Code Complete (CCD)</b> — all feature-flag changes must land before CCD, and any flag changes after CCD require the same cherry-pick approval process as code changes. Review <code>EcsFlight.kt</code> history since the last Code Complete to verify compliance. (<a href="{ecs}">EcsFlight.kt history</a>)</li>
</ol>
<p>Thanks,<br>{T.esc(ctx['owner'])}</p>"""


def build(state):
    """Resolve the reminder into a NeedsSkill(workiq_send_chat_message), or Blocked
    if the release has no CCD."""
    if not state.ccd:
        return Blocked("no CCD set for this release")

    cfg = CONFIG
    ctx = release_ctx(state)
    ccd = schedule.parse_date(state.ccd)
    ctx["ccd7_date"] = schedule.anchor_date(ccd, "CCD-7").strftime("%m/%d/%Y")

    html = _html(ctx, cfg.get("links", {}) or {})
    chat_id, target_note, prefix = resolve_chat_target(
        state, cfg.get("live_chat_id"), cfg.get("live_chat_name", "the group chat"))

    return NeedsSkill(
        tool="workiq_send_chat_message",
        payload={"chatId": chat_id, "content": html, "contentType": "html"},
        record_as=ID,
        summary=f"Post the flight & string reminders to {target_note}",
        note=f"posted to {target_note}",
        outbound=True,
    )
