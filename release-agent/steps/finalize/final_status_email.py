"""Step: `final_status_email` — send the CLOSING partner status email + close the channel
(Phase 4, finalize; the LAST step).

The daily status email (a skill-provisioned weekday automation running `status-email`) covers
Phase 2 through Phase 4. This terminal step guarantees the FINAL status email goes out when
Phase 4 completes — even if Phase 4 and Phase 5 land on the same day, when the daily automation
might not fire again — and signals the skill to tear the daily automation down so no status
emails leak into Phase 5+.

Scout-assisted: `build()` composes the closing email (the Phase-4-complete snapshot) and returns
NeedsSkill(workiq_send_email); the payload names `record-status-email --final` as the follow-up.
After sending + recording, the skill DEREGISTERS the 'Release status email' automation (see the
finalize phase reference / knowledge).

Mock knobs (mocks.local.yaml / tests):
  send_to : redirect recipients to these address(es) (owner → you) for a test send.
"""
from __future__ import annotations

import os

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator import status_email as SE
from orchestrator import notifications as notif
from steps.lib.mockctx import mock_input, MISSING

ID = "final_status_email"
KIND = "scout"

_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
_PHASES = os.path.join(_CONFIG_DIR, "phases.yaml")

MOCKABLE = {
    "send_to": {"kind": "input",
                "desc": "Redirect the closing status email to these address(es) (owner → you)."},
}


def _phase_order():
    import yaml
    try:
        with open(_PHASES, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return [p["id"] for p in (doc.get("phases") or [])]
    except Exception:  # noqa: BLE001
        return []


def _recipients():
    cfg = notif.load_config(_PHASES) or {}
    return list((cfg.get("status_email") or {}).get("recipients") or [])


def _broker_changes(state):
    try:
        from steps.finalize import integ_prs as IP
        from tools import prs
        gh = (IP.CONFIG.get("broker") or {}).get("gh_repo")
        bver = (getattr(state, "versions", None) or {}).get("broker")
        if not (gh and bver):
            return []
        ok, ch, _d = prs.broker_change_list(gh, bver)
        return ch if ok else []
    except Exception:  # noqa: BLE001
        return []


def build(state):
    to = mock_input("send_to", MISSING)
    recipients = ([x.strip() for x in str(to).split(",") if x.strip()]
                  if to is not MISSING and to else _recipients())
    if not recipients:
        return Blocked("final_status_email: no status-email recipients configured "
                       "(config/notifications.yaml status_email.recipients).")

    res = SE.compose(state, _phase_order(), recipients, changes=_broker_changes(state))
    subject = res["subject"].replace("Daily Status", "Final Status")
    month_year = res["model"].get("month_year", "")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "subject": subject,
            "body": res["html"],
            "isHtml": True,
            # After sending, stamp + close, then the skill deregisters the daily automation.
            "followup_command": f"record-status-email --release {state.release_id} --final",
        },
        record_as=ID,
        summary=f"Send the CLOSING {month_year} status email to "
                f"{len(recipients)} recipient(s) + close the daily status automation",
        note="final status email (Phase 4 complete); deregister the 'Release status email' automation after",
        outbound=True,
    )
