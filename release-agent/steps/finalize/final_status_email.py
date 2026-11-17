"""Step: `final_status_email` — send the CLOSING partner status email + close the channel
(Phase 4, finalize; the LAST step).

The daily status email (an hourly owner-local 17:00-guarded automation) covers
Phase 2 through Phase 4. This terminal step guarantees the FINAL status email goes out when
Phase 4 completes — even if Phase 4 and Phase 5 land on the same day, when the daily automation
might not fire again — and signals the skill to tear the daily automation down so no status
emails leak into Phase 5+.

Scout-assisted: `build()` composes the closing email (the Phase-4-complete snapshot) and returns
NeedsSkill(workiq_send_email); the generic notification protocol completes it after delivery.
After acknowledgement, claimed cleanup retires the daily partner status-email automation
(`<release> · Phases 2–4 — daily status email`; see the finalize phase reference / knowledge).

Mock knobs (mocks.local.yaml / tests):
  send_to : redirect recipients to these address(es) (owner → you) for a test send.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.mockctx import MISSING

STATUS_EMAIL = True
ID = "final_status_email"
KIND = "scout"
NAME = 'Send the closing partner status email + close the daily automation'
REQUIRED_TOOLS = ('workiq',)
REQUIRES_APPROVAL = False
NOTIFICATION = True

MOCKABLE = {
    "send_to": {"kind": "input",
                "desc": "Redirect the closing status email to these address(es) (owner → you)."},
}


def _broker_changes(context):
    try:
        from steps.finalize import integ_prs as IP
        gh = (IP.CONFIG.get("broker") or {}).get("gh_repo")
        bver = (getattr(context.release, "versions", None) or {}).get("broker")
        if not (gh and bver):
            return []
        ok, ch, _d = context.services.repositories.broker_change_list(gh, bver)
        return ch if ok else []
    except Exception:  # noqa: BLE001
        return []


def build(context: StepContext):
    to = context.input("send_to", MISSING)
    recipients = ([x.strip() for x in str(to).split(",") if x.strip()]
                  if to is not MISSING and to else context.services.assets.status_recipients())
    if not recipients:
        return Blocked("final_status_email: no status-email recipients configured "
                       "(config/notifications.yaml status_email.recipients).")

    res = context.services.assets.status_email(recipients, changes=_broker_changes(context))
    subject = res["subject"].replace("Daily Status", "Final Status")
    month_year = res["model"].get("month_year", "")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "subject": subject,
            "body": res["html"],
            "isHtml": True,
        },
        record_as=ID,
        summary=f"Send the CLOSING {month_year} status email to "
                f"{len(recipients)} recipient(s) + close the daily status automation",
        note="final status email (Phase 4 complete); run automation cleanup, claim-delete, then delete-result; stop on any barrier or uncertainty",
        outbound=True,
        notification={"completion": {"note": "Closing partner status email delivered; channel closed."}},
    )

KNOWLEDGE = {'summary': 'Send the closing partner status email and shut down the daily status automation.',
 'what': 'The partner-facing DAILY status email (a weekday automation running `status-email`) '
         'covers Phase 2 through Phase 4. This terminal Phase-4 step guarantees the FINAL '
         'status email — the Phase-4-complete snapshot — goes out when finalize completes, '
         'even if Phase 4 and Phase 5 land on the same day (when the daily automation might '
         'not fire again), and signals that the daily automation should be torn down so no '
         'status emails leak into Phase 5+.\n',
 'who': 'Scout composes it (a scout step) and sends it via WorkIQ to the release DLs '
        "(notifications.yaml beside the invocation's phases.yaml, status_email.recipients). An "
        'isolated test configuration is respected; the canonical production recipients are not '
        'a fallback. The `send_to` mock remains an explicit recipient override.\n',
 'where': ['recipients: config/notifications.yaml status_email.recipients (authsdkrelease@, '
           'androididentity@)',
           'template: orchestrator/status_email.py (same as the daily email)'],
 'how': 'Runs via `notification prepare --release <id> --source step --phase finalize --step '
        'final_status_email`. Claim the exact snapshot and acknowledge confirmed delivery with '
        '`notification result`; only then does the step complete and close the channel. Always '
        'run `automation cleanup --release <id> --json`; delete the returned daily status '
        'automation with `m_delete_automation`, and deregister only after deletion succeeds. '
        'RC milestones use rc_report_notify and rc_report_gate. To test, configure isolated '
        'recipients or set the `send_to` mock to your own address.\n',
 'faqs': [{'q': "Won't this double-send with the daily automation?",
           'a': 'The daily automation is idempotent per day (last_status_email_date), and its '
                'window ends at Phase 5. The terminal step is the guaranteed CLOSING send; at '
                'worst you get a morning daily + the closing email on the last day, which is '
                'intended.'},
          {'q': 'Who tears down the daily automation?',
           'a': 'The central cleanup plan makes it eligible after Phase 4 completes. The skill '
                'deletes the live automation first and deregisters it after success; '
                'release-close cleanup is the backstop.'}]}
