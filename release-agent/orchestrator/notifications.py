"""Digest delivery channels — where the daily `tick` digest is sent.

`config/notifications.yaml` declares the channels (email always; Teams optional) and
the Teams target. `tick`/`notify` call this to (1) report which channels are on and
(2) build a ready-to-send Teams `chat` block when a digest is actually due. The
engine still produces exactly ONE digest (render.notification / _html); this module
only fans it out.

Pure + IO-light (reads one yaml) so it's trivially testable and the mailer/poster
side-effects stay in the automation.
"""
from __future__ import annotations

import os

import yaml

from steps.lib.context import SELF_CHAT_ID

# Conservative default when the file is absent: email only (today's behavior),
# Teams off. Adding the file with channels.teams: true opts in.
_DEFAULTS = {"channels": {"email": True, "teams": False}, "teams": {"chat": "self"}}


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


def teams_chat_id(cfg: dict) -> str:
    """Resolve the Teams target. 'self'/'me'/'owner'/None → the owner's own chat
    (48:notes); any other value is treated as an explicit chat id."""
    chat = (cfg.get("teams") or {}).get("chat", "self")
    if chat in (None, "self", "me", "owner"):
        return SELF_CHAT_ID
    return chat


def teams_block(cfg: dict, html: str, message: str) -> dict:
    """The workiq_send_chat_message payload for the digest (prefers HTML, falls back
    to the plain-text message wrapped in <pre>)."""
    content = html or f"<pre>{message}</pre>"
    return {"chatId": teams_chat_id(cfg), "content": content, "contentType": "html"}
