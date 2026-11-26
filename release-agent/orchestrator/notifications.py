"""Notification channel configuration (`config/notifications.yaml`).

`config/notifications.yaml` declares active owner CTA channels (`email`, `teams`),
the Teams target (`scout` bot DM or explicit chat ID), and partner status email metadata.
"""
from __future__ import annotations

import os

import yaml

# Conservative default when the file is absent: email only (today's behavior),
# Teams off. Adding the file with channels.teams: true opts in. Teams target
# defaults to the Scout bot.
_DEFAULTS = {"channels": {"email": True, "teams": False}, "teams": {"target": "scout"}}

# Aliases that all mean "the Scout Teams bot" (delivered via m_send_teams_message).
_SCOUT_ALIASES = {None, "scout", "scout_bot", "bot", "self", "me", "owner"}


def notifications_path(config_path: str) -> str:
    """config/notifications.yaml sits next to phases.yaml (config_path)."""
    return os.path.join(os.path.dirname(config_path), "notifications.yaml")


def load_config(config_path: str) -> dict:
    """Load + merge notifications.yaml over the defaults. Missing file → defaults."""
    p = notifications_path(config_path)
    doc = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            if doc is None:
                doc = {}
        except yaml.YAMLError as exc:
            raise ValueError(f"Malformed notifications.yaml: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("notifications.yaml must be a mapping")
    for section in ("channels", "teams", "status_email"):
        if section in doc and not isinstance(doc[section], dict):
            raise ValueError(f"notifications.{section} must be a mapping")
    ch = {**_DEFAULTS["channels"], **(doc.get("channels") or {})}
    if ch.keys() - _DEFAULTS["channels"].keys():
        raise ValueError("Unknown notification channel")
    tm = {**_DEFAULTS["teams"], **(doc.get("teams") or {})}
    if any(not isinstance(v, bool) for v in ch.values()):
        raise ValueError("Notification channel flags must be booleans")
    if not isinstance(tm.get("target"), str) or not tm["target"].strip():
        raise ValueError("Notification Teams target must be a non-empty string")
    return {**doc, "channels": {"email": ch.get("email", True),
                         "teams": ch.get("teams", False)},
            "teams": tm}


def channels(cfg: dict) -> dict:
    """{'email': bool, 'teams': bool} — which channels are enabled."""
    return dict(cfg.get("channels", {}))


def partner_recipients(config_path: str | None = None) -> list[str]:
    """Return the consolidated external partner DL (`status_email.recipients`) from `notifications.yaml`.

    If an isolated `config_path` has its own sibling `notifications.yaml`, it is authoritative
    (an empty `recipients` list raises `ValueError`, never falling back to production DLs).
    When a temporary `phases.yaml` has no sibling `notifications.yaml`, falls back to canonical `config/`.
    """
    default_phases = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "phases.yaml",
    )
    effective_phases = (
        config_path
        if (config_path and os.path.exists(notifications_path(config_path)))
        else default_phases
    )
    cfg = load_config(effective_phases) or {}
    recipients = (cfg.get("status_email") or {}).get("recipients")
    if not isinstance(recipients, list) or not recipients:
        raise ValueError("Configure status_email.recipients as a non-empty list")
    return [str(r).strip() for r in recipients if str(r).strip()]


def teams_target(cfg: dict) -> str:
    """The configured Teams target string ('scout' or an explicit chat id)."""
    return (cfg.get("teams") or {}).get("target", "scout")


def _is_scout_bot(target) -> bool:
    return target in _SCOUT_ALIASES


def teams_delivery(cfg: dict, html: str, message: str, markdown: str = None):
    """How to deliver the Teams copy, or None if Teams is off.

    Scout bot (default):
        {"via": "scout_bot", "text": <markdown digest>}
        → the automation calls m_send_teams_message(message=text). The Scout bot
          renders markdown and collapses single newlines, so we send the markdown
          digest (blank-line paragraphs / bullets), NOT the plain-text one.
    Explicit chat id:
        {"via": "chat", "chatId": <id>, "content": <html>, "contentType": "html"}
        → the automation calls workiq_send_chat_message(**block).
    """
    if not channels(cfg).get("teams"):
        return None
    target = teams_target(cfg)
    if _is_scout_bot(target):
        return {"via": "scout_bot", "text": markdown or message}
    return {"via": "chat", "chatId": target,
            "content": html or f"<pre>{message}</pre>", "contentType": "html"}
