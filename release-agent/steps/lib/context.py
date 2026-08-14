"""Release context + recipient resolution — shared comms helpers for steps.

Any step that emails or messages people needs the same two things:
  1. a date/owner context to fill templates (`release_ctx`), and
  2. recipient resolution with the DRY-RUN safety redirect (`resolve_recipients`):
     a dry-run sends only to the release owner (safe rehearsal), a live run uses
     the configured distribution list. Centralized here so every step gets the
     redirect right — never a hardcoded recipient.
"""
from __future__ import annotations

from orchestrator import schedule
from steps.lib.templating import ordinal


def release_ctx(state) -> dict:
    """Standard placeholder context from release run-state (month, CCD forms, owner)."""
    ccd = schedule.parse_date(state.ccd)
    owner_email = state.owner_email or ""
    return {
        "month": ccd.strftime("%B"),
        "ccd_long": f"{ccd.strftime('%A, %B')} {ordinal(ccd.day)}, {ccd.year}",
        "ccd_date": ccd.strftime("%m/%d/%Y"),
        "owner": state.owner_name or owner_email or "the release owner",
        "owner_at": (owner_email.split("@")[0] if owner_email else "release-owner"),
        "owner_email": owner_email,
    }


def resolve_recipients(state, live_recipients):
    """Return (recipients, note, subject_prefix) applying the DRY-RUN redirect.

    dry-run  → the release owner only; subject prefixed '[DRY-RUN → owner] '.
    live     → the configured `live_recipients` list, no prefix.
    Never returns a hardcoded address; the owner comes from run-state.
    """
    if state.dry_run:
        owner_email = state.owner_email or ""
        recipients = [owner_email] if owner_email else []
        return recipients, "dry-run: redirected to release owner", "[DRY-RUN → owner] "
    return list(live_recipients or []), "live recipients", ""


# The signed-in user's own Teams chat ("You"), a well-known chat id. Used as the
# safe DRY-RUN target for any step that posts to Teams — a live release posts to
# the configured group chat instead.
SELF_CHAT_ID = "48:notes"


def resolve_chat_target(state, live_chat_id, live_chat_name="the group chat"):
    """Return (chat_id, note, prefix) applying the DRY-RUN redirect for Teams sends.

    dry-run  → the owner's own Teams self-chat (SELF_CHAT_ID); prefix '[DRY-RUN → owner] '.
    live     → the configured group chat id, no prefix.
    Mirrors `resolve_recipients` so every comms step gets the same redirect.
    """
    if state.dry_run:
        return SELF_CHAT_ID, "owner's own Teams chat (dry-run)", "[DRY-RUN → owner] "
    return live_chat_id, live_chat_name, ""
