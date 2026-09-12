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
from datetime import datetime, time, timedelta

import yaml
from orchestrator import schedule
from tools import bugbash as BB
from tools.coordinates import coords

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


def previous_business_day(day):
    candidate = day - timedelta(days=1)
    while not BB.is_business_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def preflight_escalation(report: dict, now: datetime, emitted: dict) -> dict | None:
    """Return the due Phase-0 risk checkpoint, independent of digest silence rules."""
    ccd = schedule.parse_date(report.get("ccd"))
    phase = report.get("active_phase") or {}
    if not ccd or phase.get("id") != "preflight" or not phase.get("outstanding"):
        return None
    pre_ccd = previous_business_day(ccd)
    today = now.date()
    if now.time() < time(9) or today not in (pre_ccd, ccd):
        return None
    checkpoint = "pre_ccd" if today < ccd else "ccd"
    key = f"preflight:{ccd.isoformat()}:{checkpoint}"
    if key in (emitted or {}):
        return None

    incomplete = [s for s in phase.get("steps", []) if s.get("status") != "done"]
    blocked = [s for s in incomplete if s.get("status") == "blocked"]
    confirmations = [s for s in incomplete if s.get("status") in ("confirm", "action", "approval")]
    other = [s for s in incomplete if s not in blocked and s not in confirmations]
    return {
        "key": key, "checkpoint": checkpoint, "ccd": ccd.isoformat(),
        "pre_ccd": pre_ccd.isoformat(), "today": today.isoformat(),
        "blocked": blocked, "confirmations": confirmations, "other": other,
    }


def core_alert_delivery(report: dict, model: dict, html: str) -> dict:
    """WorkIQ descriptor for the configured Android Core Team group chat."""
    target = coords.team("android_core")
    owner_email = report.get("owner_email") or ""
    owner_name = report.get("owner_name") or owner_email.split("@")[0] or "Release owner"
    mentions = []
    if owner_email:
        mentions.append({
            "id": 0, "mentionText": owner_name,
            "mentioned": {"user": {"id": owner_email, "displayName": owner_name,
                                   "userIdentityType": "aadUser"}},
        })
    return {
        "via": "chat", "chatId": target["chat"], "chatName": target["name"],
        "content": html, "contentType": "html", "mentions": mentions,
        "checkpoint": model["key"],
    }
