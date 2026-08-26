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
             (keeps the post real, points it elsewhere). Alias "self" is not valid for a
             channel (channels have no self); provide explicit ids.
  versions : dict override for the SDK versions shown (e.g. {common:'24.6.0', msal:'8.4.2',
             broker:'16.5.0'}) — otherwise read from state.versions.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib import templating as T
from steps.lib.mockctx import mock_input, MISSING

ID = "release_announcement"
KIND = "scout"

# The "General" channel used to sync release plans between Android DevX and partner teams.
CONFIG = {
    "team_id": "be33b3e7-c501-4225-9413-3b88046f3eb3",
    "channel_id": "19:715820c336d3454bbd75ef0bad68e460@thread.tacv2",
    "channel_name": "General",
    # SDKs shown in the announcement table, in order: (state.versions key, display label).
    "sdks": [("common", "Common"), ("msal", "MSAL"), ("broker", "Broker")],
    # cc @-mentions. Each: {displayName, id, type}. These four are Teams TAGS (a tag pings a
    # curated subset of the team). Real tag mentions need the tag's id (Graph
    # GET /teams/{team-id}/tags) AND the microsoft_teams tool passing type:"tag" through to
    # Graph — NOT yet confirmed (the tool documents user|team|channel|app). Until an id is
    # filled, each entry renders as PLAIN TEXT (safe, never mis-tags). Fill id to activate.
    "cc_mentions": [
        {"displayName": "CP/Intune", "id": None, "type": "tag"},
        {"displayName": "LTW", "id": None, "type": "tag"},
        {"displayName": "OneAuth", "id": None, "type": "tag"},
        {"displayName": "Native Auth", "id": None, "type": "tag"},
    ],
}

MOCKABLE = {
    "post_to": {"kind": "input",
                "desc": "{teamId, channelId} override — post to a TEST channel instead of General."},
    "versions": {"kind": "input",
                 "desc": "dict override for the SDK versions shown (else read from state.versions)."},
    "cc_mentions": {"kind": "input",
                    "desc": "list of {displayName,id,type} cc @-mentions (id=None -> plain text)."},
}


def _month_year(state) -> str:
    """'August 2026' — from the CCD if set, else the release_id (YYYY-MM)."""
    from orchestrator import schedule
    if state.ccd:
        return schedule.parse_date(state.ccd).strftime("%B %Y")
    try:
        y, m = str(state.release_id).split("-")[:2]
        import datetime
        return datetime.date(int(y), int(m), 1).strftime("%B %Y")
    except Exception:  # noqa: BLE001
        return str(state.release_id)


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


def _cc(state):
    """(html_line, mentions) — the cc line and the microsoft_teams `mentions` array.
    Entries WITH an id become real @mentions (content carries '@DisplayName' + a mentions
    entry); entries without an id fall back to plain text so we never mis-tag."""
    ov = mock_input("cc_mentions", MISSING)
    entries = list(ov) if ov is not MISSING and ov else CONFIG.get("cc_mentions", [])
    if not entries:
        return ("", [])
    parts, mentions = [], []
    for e in entries:
        name = e.get("displayName", "")
        if e.get("id"):
            parts.append(f"@{name}")          # server swaps @Name -> Teams mention markup
            mentions.append({"displayName": name, "id": e["id"],
                             "type": e.get("type", "tag")})
        else:
            parts.append(T.esc(name))          # no id -> plain text, not a tag
    return (f"<p>cc: {', '.join(parts)}</p>", mentions)


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
        summary=f"Post '{subject}' to {note}",
        note=f"posted the release announcement to {note}",
        outbound=True,
    )
