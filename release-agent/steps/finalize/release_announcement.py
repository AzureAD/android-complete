"""Step: `release_announcement` — post the release-complete announcement to the
"General" Teams channel (Phase 4, finalize, F4).

Once the release has published, announce it in the Android DevX <-> partner release-sync
"General" channel: a titled post ("Auth Client Android SDKs <Month Year> Release") with a
small SDK version table (Common / MSAL / Broker). Versions come from `state.versions` — the
single source of truth populated at Phase 2 (build_verify.orchestrator_health) — so this step
never re-discovers them.

Posting a ROOT channel message needs the `microsoft_teams` MCP (WorkIQ only supports channel
REPLIES), which the deterministic engine can't call — so this is a `scout` step: `build()`
composes the message + target and returns NeedsSkill(microsoft_teams-SendMessageToChannel),
which the skill executes, then records the step.

Mock knobs (mocks.local.yaml / tests):
  post_to  : {teamId, channelId} override — post to a TEST channel instead of General
             (keeps the post real, points it elsewhere).
  versions : dict override for the SDK versions shown (else read from state.versions).
  cc_members : list of {name,email} to cc instead of the registry (for tests).
  cc_mode  : 'mention' (default, @-mention every registry member) | 'self' (mention only
             self_email once — pings just you, for a safe test) | 'off' (plain-text names).
  self_email : the email/UPN to self-mention when cc_mode='self'.

The cc groups (CP/Intune, LTW, OneAuth, Native Auth) are Teams TAGS the microsoft_teams tool
cannot @-mention, so their MEMBERS are maintained in config/announcement_cc.yaml and mentioned
as individual users instead.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib import templating as T
from steps.lib.mockctx import mock_input, MISSING
from tools.coordinates import coords

ID = "release_announcement"
KIND = "scout"

# The "General" channel used to sync release plans between Android DevX and partner teams.
_ANNOUNCE = coords.team("general_announce")
CONFIG = {
    "team_id": _ANNOUNCE["team"],
    "channel_id": _ANNOUNCE["channel"],
    "channel_name": _ANNOUNCE["name"],
    # SDKs shown in the announcement table, in order: (state.versions key, display label).
    "sdks": [("common", "Common"), ("msal", "MSAL"), ("broker", "Broker")],
    # cc: the four groups (CP/Intune, LTW, OneAuth, Native Auth) are Teams TAGS the tool can't
    # @-mention, so we @-mention their MEMBERS (maintained in config/announcement_cc.yaml) as
    # individual users. The registry path is resolved relative to this repo's config/ dir.
    "cc_registry": "announcement_cc.yaml",
}

# config/announcement_cc.yaml lives next to config/phases.yaml.
import os as _os
_CC_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                         "config", "announcement_cc.yaml")

MOCKABLE = {
    "post_to": {"kind": "input",
                "desc": "{teamId, channelId} override — post to a TEST channel instead of General."},
    "versions": {"kind": "input",
                 "desc": "dict override for the SDK versions shown (else read from state.versions)."},
    "cc_members": {"kind": "input",
                   "desc": "flat [{name,email}] to cc as one unlabeled group (tests)."},
    "cc_groups": {"kind": "input",
                  "desc": "list of {group, members:[{name,email}]} to override the registry (tests)."},
    "cc_mode": {"kind": "input",
                "desc": "'mention' | 'self' (ping only self_email) | 'off' (plain text). "
                        "Defaults to 'mention'; a post_to redirect defaults to 'off' so a test "
                        "shows the full real cc as plain text (no pings)."},
    "self_email": {"kind": "input",
                   "desc": "the email/UPN to use as the single self-mention when cc_mode='self'."},
}


def _month_year(state) -> str:
    """The release's display month — the ship/target month (CCD month + 1), e.g. 'August 2026'."""
    from orchestrator import schedule
    return schedule.target_month_label(state) or str(state.release_id)


def _sdk_versions(state) -> dict:
    """{key: version} for the announcement — mock override first, else state.versions."""
    v = mock_input("versions", MISSING)
    src = dict(v) if v is not MISSING and v else dict(getattr(state, "versions", {}) or {})
    return {k: src.get(k) for k, _label in CONFIG["sdks"]}


def _target(state):
    """(team_id, channel_id, note) — the live General channel, or a test override via `post_to`."""
    ov = mock_input("post_to", MISSING)
    if ov is not MISSING and isinstance(ov, dict) and ov.get("channelId"):
        return (ov.get("teamId", CONFIG["team_id"]), ov["channelId"], "a test channel")
    return (CONFIG["team_id"], CONFIG["channel_id"], f"the '{CONFIG['channel_name']}' channel")


def _rows_html(versions: dict) -> str:
    rows = []
    for key, label in CONFIG["sdks"]:
        val = versions.get(key)
        rows.append(f"<tr><td>{T.esc(label)}</td><td>{T.esc(val) if val else '—'}</td></tr>")
    return "".join(rows)


def _load_groups():
    """Ordered [(group_name, [{name,email}])] from config/announcement_cc.yaml — empty groups
    skipped, per-group member order preserved. Never raises (missing/broken file -> [])."""
    try:
        import yaml
        with open(_CC_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for group, members in (data.get("groups") or {}).items():
        cleaned = [{"name": (m or {}).get("name") or (m or {}).get("email", "").split("@")[0],
                    "email": (m or {}).get("email")}
                   for m in (members or []) if (m or {}).get("email")]
        if cleaned:
            out.append((group, cleaned))
    return out


def _load_members():
    """Flattened, de-duplicated [{name,email}] across all groups (registry order)."""
    seen, out = set(), []
    for _group, members in _load_groups():
        for m in members:
            if m["email"].lower() not in seen:
                seen.add(m["email"].lower())
                out.append(m)
    return out


def _effective_cc_mode(state):
    """Resolve the cc mode. Explicit `cc_mode` wins. Otherwise a TEST redirect (`post_to`)
    defaults to 'off' — the FULL real cc (all teams + members) rendered as PLAIN TEXT, so a
    test post mirrors reality without @-mentioning anyone. With no redirect, default 'mention'."""
    m = mock_input("cc_mode", MISSING)
    if m is not MISSING and m:
        return str(m).lower()
    if mock_input("post_to", MISSING) is not MISSING:       # test/redirect -> plain text, no pings
        return "off"
    return "mention"


def _groups_for_cc(state):
    """The groups to render — a `cc_groups`/`cc_members` mock overrides the registry (for tests)."""
    g = mock_input("cc_groups", MISSING)
    if g is not MISSING and g:
        return [(x.get("group"), list(x.get("members") or [])) for x in g]
    fm = mock_input("cc_members", MISSING)
    if fm is not MISSING and fm:
        return [(None, list(fm))]                            # single unlabeled group
    return _load_groups()


def _cc(state):
    """(html_block, mentions) — the cc block (one line PER TEAM) + the microsoft_teams
    `mentions` array.

    Modes (see _effective_cc_mode): 'mention' @-mentions each member; 'self' mentions ONLY
    self_email once (safe test — pings just you); 'off' lists names as plain text. Any test
    redirect (post_to) auto-selects self/off so a test post never pings the real members.
    """
    mode = _effective_cc_mode(state)

    if mode == "self":
        se = mock_input("self_email", MISSING)
        if se is MISSING or not se:
            return ("", [])
        name = str(se).split("@")[0]
        return (f"<p>cc: @{T.esc(name)}</p>",
                [{"displayName": name, "id": se, "type": "user"}])

    groups = _groups_for_cc(state)
    if not groups:
        return ("", [])

    lines, mentions = ["<p>cc:<br>"], []
    for group, members in groups:
        label = f"<b>{T.esc(group)}</b>: " if group else ""
        if mode == "off":
            names = ", ".join(T.esc(m["name"]) for m in members)
            lines.append(f"{label}{names}<br>")
        else:  # 'mention'
            parts = []
            for m in members:
                parts.append(f"@{m['name']}")               # server swaps @Name -> mention markup
                mentions.append({"displayName": m["name"], "id": m["email"], "type": "user"})
            lines.append(f"{label}{', '.join(parts)}<br>")
    lines.append("</p>")
    return ("".join(lines), mentions)


def _html(state, versions: dict, cc_line: str) -> str:
    return (
        "<div><p>Hi all,</p>"
        "<p>The latest Android SDK Library release has been completed.</p>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<tr><th align="left">SDK</th><th align="left">Current Build</th></tr>'
        f"{_rows_html(versions)}</table>"
        f"{cc_line}</div>")


def build(state):
    versions = _sdk_versions(state)
    if not any(versions.values()):
        return Blocked(
            "release_announcement: no SDK versions available in state.versions — Phase 2 "
            "(orchestrator_health) should have populated them. Provide the `versions` mock "
            "for testing, or re-run Phase 2 version discovery.")

    subject = f"Auth Client Android SDKs {_month_year(state)} Release"
    cc_line, mentions = _cc(state)
    html = _html(state, versions, cc_line)
    team_id, channel_id, note = _target(state)

    payload = {
        "teamId": team_id,
        "channelId": channel_id,
        "subject": subject,
        "content": html,
        "contentType": "html",
    }
    if mentions:
        # the microsoft_teams tool expects a JSON string for `mentions`.
        import json
        payload["mentions"] = json.dumps(mentions)

    return NeedsSkill(
        tool="microsoft_teams-SendMessageToChannel",
        payload=payload,
        record_as=ID,
        summary=f"Post '{subject}' to {note} (cc {len(mentions)} member(s))",
        note=f"posted the release announcement to {note}",
        outbound=True,
    )
