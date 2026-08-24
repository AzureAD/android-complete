"""Step: `notify_native_auth` — one-shot Teams ping to the Native Auth release engineer that
the bug bash is ready (Phase 3, bug_bash).

The Broker test plan now carries a "Manual Tests (Native Auth)" folder (see
clone_plans_broker). This step sends ONE Teams message to the month's Native Auth Release
Engineer — from the release-engineer schedule — telling them the bug bash is ready, asking
them to run their Native Auth tests at their earliest convenience, and to send the release
owner a confirmation when they finish. It is fired once and done — no poller, no automation.

Scout-assisted (skill-driven), three tiers:
  1. RESOLVE — read the Native Auth Release Engineer for <release month> from the schedule
     doc (link below). The inline NATIVE_AUTH_RE hint seeds the likely alias; the skill
     confirms/overrides it from the live doc.
  2. SEND — resolve that engineer's UPN (workiq_search_people) and send them the 1:1 Teams
     message (workiq_send_chat_message / SendMessageToUser) with the text the engine composed.
  3. HUMAN FALLBACK — if the engineer can't be resolved, ask the owner who the Native Auth RE
     is (m_ask_user), or hold the step.

The skill records completion with `record-nativeauth-notify --release <id> --engineer <who>`
(or without --engineer to hold the step for the owner).

Depends on clone_plans_broker (for the Native Auth suite link). Idempotent — once recorded
with an engineer it reports done without re-sending.
"""
from __future__ import annotations

from orchestrator import schedule
from orchestrator.outcomes import NeedsSkill, Blocked, Done
from tools import testplans as T

ID = "notify_native_auth"
KIND = "scout"

SCHEDULE_DOC = ("https://eng.ms/docs/microsoft-security/identity/entra-developer-application-"
                "platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/"
                "internal-release-checklist/release-engineer-schedule")

# Native Auth Release Engineer per release month, seeded from the schedule doc. A HINT only —
# the skill confirms against the live doc and may override. Keyed 'Month YYYY'.
NATIVE_AUTH_RE = {
    "March 2026": "mmizrak", "April 2026": "mmizrak", "May 2026": "mmizrak",
    "June 2026": "mmizrak", "July 2026": "mmizrak",
    "August 2026": "silviu.petrescu", "September 2026": "mmizrak",
    "October 2026": "silviu.petrescu", "November 2026": "mmizrak",
    "January 2027": "silviu.petrescu", "February 2027": "mmizrak", "March 2027": "silviu.petrescu",
}


def notified_engineer(state):
    """The alias/UPN the step recorded as notified, or None."""
    return (state.get_step("bug_bash", ID).data or {}).get("engineer")


def _message_html(month_year, owner_name, plan_url):
    who = owner_name or "the release owner"
    return (
        f'<div style="font-family:\'Segoe UI\',Arial,sans-serif;font-size:14px;">'
        f'<p>Hi \u2014 the <b>{month_year} Android release Bug Bash</b> is ready to go.</p>'
        f'<p>This month the Broker test plan includes a '
        f'<b><a href="{plan_url}">Manual Tests (Native Auth)</a></b> folder with your Native '
        f'Auth test cases. Could you please <b>run your Native Auth tests at your earliest '
        f'convenience</b> and mark pass/fail in the test plan?</p>'
        f'<p>When you\u2019re done, please <b>send {who} a quick confirmation message</b> so '
        f'we can close out the bash. Thanks!</p>'
        f'</div>')


def build(state):
    if not state.ccd:
        return Blocked("notify_native_auth: no CCD set — can't identify the release month.")
    broker_plan = (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")
    if not broker_plan:
        return Blocked("notify_native_auth: the Broker test plan isn't built yet "
                       "(clone_plans_broker) — run that first.")

    # Idempotent: already recorded as notified → done, no re-send.
    already = notified_engineer(state)
    if already:
        return Done(f"Native Auth RE ({already}) already notified the {ID} for "
                    f"{state.release_id}.")

    month_year = schedule.parse_date(state.ccd).strftime("%B %Y")
    re_hint = NATIVE_AUTH_RE.get(month_year, "")
    plan_url = T.plan_web_url(broker_plan)
    html = _message_html(month_year, state.owner_name, plan_url)

    hint_txt = (f"The schedule lists '{re_hint}' as the Native Auth RE for {month_year} "
                f"(confirm against the doc)." if re_hint
                else f"The inline hint has no entry for {month_year} — read it from the doc.")
    instructions = (
        f"Notify the Native Auth Release Engineer that the {month_year} bug bash is ready.\n"
        f"1. RESOLVE the Native Auth RE for '{month_year}' from the schedule doc "
        f"({SCHEDULE_DOC}) — the 'Native Auth Release Engineer' column (aliases are in "
        f"parens, e.g. 'Silviu (silviu.petrescu)'). {hint_txt}\n"
        f"2. SEND the message below to that engineer as a 1:1 Teams message: resolve their "
        f"UPN with `workiq_search_people` (or the alias@microsoft.com), then "
        f"`workiq_send_chat_message` (or SendMessageToUser). contentType html.\n"
        f"3. HUMAN FALLBACK if you can't resolve them: `m_ask_user` for the Native Auth RE's "
        f"alias/email, then send.\n"
        f"4. Record: `record-nativeauth-notify --release {state.release_id} --engineer "
        f"'<alias-or-upn>'`. If it truly can't be sent, run WITHOUT --engineer to hold it.")

    return NeedsSkill(
        tool="record-nativeauth-notify",
        payload={
            "release": state.release_id,
            "engineer_hint": re_hint,
            "content": html,
            "contentType": "html",
            "followup_command": (f"record-nativeauth-notify --release {state.release_id} "
                                 f"--engineer '<alias-or-upn>'"),
            "_gather": {"month_year": month_year, "schedule_doc": SCHEDULE_DOC,
                        "instructions": instructions},
        },
        record_as=ID,
        summary=(f"Notify the {month_year} Native Auth RE"
                 + (f" ({re_hint})" if re_hint else "")
                 + " that the bug bash is ready + ask for a confirmation when done"),
        note="awaiting Native Auth RE notification (resolve from schedule → send → record)",
        outbound=True,
    )
