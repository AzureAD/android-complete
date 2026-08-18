"""Digest delivery channels — where the daily `tick` digest is sent.

`config/notifications.yaml` declares the channels (email always; Teams optional) and
the Teams target. `tick`/`notify` call this to (1) report which channels are on and
(2) build a Teams delivery descriptor when a digest is actually due. The engine still
produces exactly ONE digest (render.notification / _html); this module only fans it
out.

Teams has TWO possible destinations:
  * 'scout' (default) → the Scout Teams bot DM (m_send_teams_message). This is the
    release owner's Scout notification channel — plain-text digest.
  * an explicit chat id → workiq_send_chat_message to that chat (rich HTML).

Pure + IO-light (reads one yaml) so it's trivially testable; the actual send
side-effects stay in the automation.
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
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            doc = {}
    ch = {**_DEFAULTS["channels"], **(doc.get("channels") or {})}
    tm = {**_DEFAULTS["teams"], **(doc.get("teams") or {})}
    return {"channels": {"email": bool(ch.get("email", True)),
                         "teams": bool(ch.get("teams", False))},
            "teams": tm}


def channels(cfg: dict) -> dict:
    """{'email': bool, 'teams': bool} — which channels are enabled."""
    return dict(cfg.get("channels", {}))


def teams_target(cfg: dict) -> str:
    """The configured Teams target string ('scout' or an explicit chat id)."""
    return (cfg.get("teams") or {}).get("target", "scout")


def _is_scout_bot(target) -> bool:
    return target in _SCOUT_ALIASES


def teams_delivery(cfg: dict, html: str, message: str):
    """How to deliver the Teams copy, or None if Teams is off.

    Scout bot (default):
        {"via": "scout_bot", "text": <plain digest>}
        → the automation calls m_send_teams_message(message=text).
    Explicit chat id:
        {"via": "chat", "chatId": <id>, "content": <html>, "contentType": "html"}
        → the automation calls workiq_send_chat_message(**block).
    """
    if not channels(cfg).get("teams"):
        return None
    target = teams_target(cfg)
    if _is_scout_bot(target):
        return {"via": "scout_bot", "text": message}
    return {"via": "chat", "chatId": target,
            "content": html or f"<pre>{message}</pre>", "contentType": "html"}
