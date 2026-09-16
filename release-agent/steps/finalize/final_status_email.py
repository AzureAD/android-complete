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

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.mockctx import MISSING

STATUS_EMAIL = True
ID = "final_status_email"
KIND = "scout"
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
