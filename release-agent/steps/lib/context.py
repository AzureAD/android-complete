"""Release context + recipient resolution — shared comms helpers for steps.

Any step that emails or messages people needs the same two things:
  1. a date/owner context to fill templates (`release_ctx`), and
  2. recipient resolution (`resolve_recipients` / `resolve_chat_target`): runs are
     real, so these return the configured distribution list / group chat. To
     redirect for testing, use the step's `send_to` mock knob (never a hardcoded
     recipient).
"""
from __future__ import annotations

from orchestrator import schedule
from steps.lib.templating import ordinal


def release_ctx(state) -> dict:
    """Standard placeholder context from release run-state (month, CCD forms, owner)."""
    ccd = schedule.parse_date(state.ccd)
    owner_email = state.owner_email or ""
    return {
        "month": schedule.target_month_label(state, with_year=False),
        "ccd_long": f"{ccd.strftime('%A, %B')} {ordinal(ccd.day)}, {ccd.year}",
        "ccd_date": ccd.strftime("%m/%d/%Y"),
        "owner": state.owner_name or owner_email or "the release owner",
        "owner_at": (owner_email.split("@")[0] if owner_email else "release-owner"),
        "owner_email": owner_email,
    }


def resolve_recipients(state, live_recipients):
    """Return (recipients, note, prefix) — the configured real recipients.

    Runs are real: recipients default to the configured distribution list. To
    redirect for testing, use the step's `send_to` mock knob (applied at the
    step-action boundary), which keeps the send real but points it at you.
    """
    return list(live_recipients or []), "recipients", ""


# The signed-in user's own Teams chat ("You"), a well-known chat id. Referenced by
# a step's `send_to: me` mock alias to redirect a real post to your own chat.
SELF_CHAT_ID = "48:notes"


def resolve_chat_target(state, live_chat_id, live_chat_name="the group chat"):
    """Return (chat_id, note, prefix) — the configured real group chat. Redirect
    for testing via the step's `send_to` mock knob, not automatically."""
    return live_chat_id, live_chat_name, ""
