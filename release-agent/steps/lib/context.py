"""Release context + target resolution — shared comms helpers for steps.

Resolves outbound email recipients and Teams chat destinations directly from
`config/environment.yaml` (`orchestrator/environment.py`).
"""
from __future__ import annotations

from orchestrator import environment as env, schedule
from orchestrator.step_context import MISSING
from steps.lib.templating import ordinal

SELF_CHAT_ID = env.SELF_CHAT_ID


def release_ctx(context) -> dict:
    """Standard placeholder context from release run-state (month, CCD forms, owner)."""
    ccd = schedule.parse_date(context.release.ccd)
    owner_email = context.release.owner_email or ""
    return {
        "month": schedule.target_month_label(context.release, with_year=False),
        "ccd_long": f"{ccd.strftime('%A, %B')} {ordinal(ccd.day)}, {ccd.year}",
        "ccd_date": ccd.strftime("%m/%d/%Y"),
        "owner": context.release.owner_name or owner_email or "the release owner",
        "owner_at": (owner_email.split("@")[0] if owner_email else "release-owner"),
        "owner_email": owner_email,
    }


def resolve_recipients(context, live_recipients=None, *, group_key: str = "partner_status"):
    """Return `(recipients, note, subject_prefix)` from `config/environment.yaml` (or step override)."""
    override = context.input("send_to", MISSING)
    if override is not MISSING and override:
        recipients, prefix, label = env.resolve_email_targets(
            group_key, owner_email=context.release.owner_email, override=override
        )
        return recipients, label, prefix

    prof_name = env.active_profile_name()
    if prof_name != "prod" or not live_recipients:
        recipients, prefix, label = env.resolve_email_targets(
            group_key, owner_email=context.release.owner_email
        )
        return recipients, label, prefix

    return list(live_recipients), "recipients", env.email_subject_prefix()


def resolve_chat_target(context, live_chat_id=None, live_chat_name="the group chat", *, team_key: str | None = None):
    """Return `(chat_id, target_note, banner_html)` from `config/environment.yaml` (or step override)."""
    override = context.input("send_to", MISSING)
    if override is not MISSING and override:
        cid = env.normalize_chat_target(override)
        is_self = cid == SELF_CHAT_ID
        banner = f"<p><b>[TEST · intended: {live_chat_name}]</b></p>" if is_self else ""
        return cid, live_chat_name, banner

    if team_key is not None:
        cid, label, is_self = env.resolve_teams_chat(team_key)
        if is_self:
            return cid, label, f"<p><b>[TEST · intended: {live_chat_name}]</b></p>"
        return (live_chat_id or cid), live_chat_name, ""

    if env.active_profile_name() != "prod":
        return SELF_CHAT_ID, f"self-chat (intended: {live_chat_name})", f"<p><b>[TEST · intended: {live_chat_name}]</b></p>"

    return live_chat_id, live_chat_name, ""
