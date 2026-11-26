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

Step defaults (`step_defaults` / offline scenarios):
  post_to  : {teamId, channelId} override — post to an alternate channel instead of General.
  send_to  : chat override (under `active_profile: test`, `config/environment.yaml` routes to `self`).
  versions : dict override for the SDK versions shown (else read from state.versions).
  cc_members : list of {name,email} to cc instead of the registry.
  cc_mode  : 'mention' (prod default, @-mention every registry member) | 'self' (mention only
             self_email once) | 'off' (test default, plain-text names).
  self_email : the email/UPN to self-mention when cc_mode='self'.

The cc groups (CP/Intune, LTW, OneAuth, Native Auth) are Teams TAGS the microsoft_teams tool
cannot @-mention, so their MEMBERS are maintained in config/announcement_cc.yaml and mentioned
as individual users instead.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib import templating as T
from orchestrator.step_context import MISSING
from tools.coordinates import coords

ID = "release_announcement"
KIND = "scout"
NAME = "Announce the release in the 'General' Teams channel"
REQUIRED_TOOLS = ('microsoft_teams',)
REQUIRES_APPROVAL = False
NOTIFICATION = True

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

def _month_year(context) -> str:
    """The release's display month matching its calendar month, e.g. 'August 2026'."""
    from orchestrator import schedule
    return schedule.target_month_label(context.release) or str(context.release.release_id)

def _sdk_versions(context) -> dict:
    """{key: version} for the announcement — `versions` input override first, else state.versions."""
    v = context.input("versions", MISSING)
    src = dict(v) if v is not MISSING and v else dict(getattr(context.release, "versions", {}) or {})
    return {k: src.get(k) for k, _label in CONFIG["sdks"]}

def _target(context):
    """(team_id, channel_id, note) — the live General channel, or a test override via `post_to`."""
    ov = context.input("post_to", MISSING)
    if ov is not MISSING and isinstance(ov, dict) and ov.get("channelId"):
        return (ov.get("teamId", CONFIG["team_id"]), ov["channelId"], "a test channel")
    return (CONFIG["team_id"], CONFIG["channel_id"], f"the '{CONFIG['channel_name']}' channel")

def _rows_html(versions: dict) -> str:
    rows = []
    for key, label in CONFIG["sdks"]:
        val = versions.get(key)
        rows.append(f"<tr><td>{T.esc(label)}</td><td>{T.esc(val) if val else '—'}</td></tr>")
    return "".join(rows)

def _load_groups(context):
    """Ordered [(group_name, [{name,email}])] from config/announcement_cc.yaml — empty groups
    skipped, per-group member order preserved. Invalid configuration holds the announcement."""
    import yaml
    try:
        data = yaml.safe_load(context.services.assets.text_file(_CC_PATH))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read announcement cc registry: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        raise ValueError("Announcement cc registry requires a groups mapping")
    out = []
    for group, members in data["groups"].items():
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Announcement cc registry has an invalid group name")
        if members is None:
            members = []
        if not isinstance(members, list):
            raise ValueError(f"Announcement cc registry group {group} requires a member list")
        cleaned = []
        for member in members:
            email = member.get("email") if isinstance(member, dict) else None
            if (not isinstance(email, str) or "@" not in email
                    or any(c.isspace() for c in email)):
                raise ValueError(f"Announcement cc registry group {group} has an invalid member email")
            name = member.get("name") or email.split("@")[0]
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"Announcement cc registry group {group} has an invalid member name")
            cleaned.append({"name": name, "email": email})
        if cleaned:
            out.append((group, cleaned))
    return out

def _effective_cc_mode(context):
    """Resolve the cc mode from explicit input or `config/environment.yaml` (`announcement_cc_mode`)."""
    from orchestrator import environment as env

    m = context.input("cc_mode", MISSING)
    ov_chan = context.input("post_to", MISSING)
    ov_chat = context.input("send_to", MISSING)
    target = env.resolve_general_announce(
        override_chat=(None if ov_chat is MISSING else ov_chat),
        override_channel=(None if ov_chan is MISSING else ov_chan),
    )
    return env.announcement_cc_mode(
        explicit_mode=(None if m is MISSING else m),
        is_redirect=bool(target.get("is_redirect")),
    )

def _groups_for_cc(context):
    """The groups to render — a `cc_groups`/`cc_members` input overrides the registry."""
    g = context.input("cc_groups", MISSING)
    if g is not MISSING and g:
        return [(x.get("group"), list(x.get("members") or [])) for x in g]
    fm = context.input("cc_members", MISSING)
    if fm is not MISSING and fm:
        return [(None, list(fm))]                            # single unlabeled group
    return _load_groups(context)

def _cc(context):
    """(html_block, mentions) — the cc block (one line PER TEAM) + the microsoft_teams
    `mentions` array.

    Modes (see _effective_cc_mode): 'mention' @-mentions each member; 'self' mentions ONLY
    self_email once (safe test — pings just you); 'off' lists names as plain text. Any test
    redirect (post_to) auto-selects self/off so a test post never pings the real members.
    """
    mode = _effective_cc_mode(context)

    if mode == "self":
        se = context.input("self_email", MISSING)
        if se is MISSING or not se:
            return ("", [])
        name = str(se).split("@")[0]
        return (f"<p>cc: @{T.esc(name)}</p>",
                [{"displayName": name, "id": se, "type": "user"}])

    groups = _groups_for_cc(context)
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

def _html(context, versions: dict, cc_line: str) -> str:
    return (
        "<div><p>Hi all,</p>"
        "<p>The latest Android SDK Library release has been completed.</p>"
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<tr><th align="left">SDK</th><th align="left">Current Build</th></tr>'
        f"{_rows_html(versions)}</table>"
        f"{cc_line}</div>")

def build(context: StepContext):
    versions = _sdk_versions(context)
    if not any(versions.values()):
        return Blocked(
            "release_announcement: no SDK versions available in state.versions — Phase 2 "
            "(orchestrator_health) should have populated them. Provide `versions` in `step_defaults` "
            "for rehearsals, or re-run Phase 2 version discovery.")

    subject = f"Auth Client Android SDKs {_month_year(context)} Release"
    try:
        cc_line, mentions = _cc(context)
    except ValueError as exc:
        return Blocked(f"release_announcement: {exc}")
    html = _html(context, versions, cc_line)
    from orchestrator import environment as env

    ov_chan = context.input("post_to", MISSING)
    ov_chat = context.input("send_to", MISSING)
    resolved_target = env.resolve_general_announce(
        override_chat=(None if ov_chat is MISSING else ov_chat),
        override_channel=(None if ov_chan is MISSING else ov_chan),
    )
    if resolved_target["mode"] == "chat":
        chat_target = resolved_target["chat_id"]
        redirected_html = (
            f"<div><p><b>[TEST · intended channel: '{T.esc(resolved_target.get('intended', 'General'))}' "
            f"· {T.esc(subject)}]</b></p></div>" + html
        )
        return NeedsSkill(
            tool="workiq_send_chat_message",
            payload={
                "chatId": chat_target,
                "content": redirected_html,
                "contentType": "html",
            },
            record_as=ID,
            summary=f"Post '{subject}' to {resolved_target['label']}",
            note=f"[test-redirect] posted the release announcement to {resolved_target['label']}",
            outbound=True,
        )

    team_id = resolved_target["team_id"]
    channel_id = resolved_target["channel_id"]
    note = resolved_target["label"]

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

KNOWLEDGE = {'summary': "Post the release-complete announcement to the 'General' Teams channel.",
 'what': 'Once the release has published, announce it in the Android DevX <-> partner '
         "release-sync 'General' channel: a titled post ('Auth Client Android SDKs <Month "
         "Year> Release') with a small SDK version table (Common / MSAL / Broker) and a cc "
         'line. A root channel POST (not a reply), so partner teams see the completed build '
         'numbers in one place.\n',
 'who': 'Scout composes and posts it (a scout step); the release owner owns the '
        'content/recipients. Versions come from state.versions (the single source of truth '
        'populated at Phase 2) — the step never re-discovers them.\n',
 'where': ["Team be33b3e7-c501-4225-9413-3b88046f3eb3, channel 'General' "
           '19:715820c336d3454bbd75ef0bad68e460@thread.tacv2'],
 'how': 'Uses notification prepare (source step), claim, then the returned '
        'microsoft_teams-SendMessageToChannel payload and notification result. A ROOT channel '
        'post needs the microsoft_teams MCP; WorkIQ only does channel replies. Under '
        '`active_profile: test` in `config/environment.yaml`, `teams.general_announce: self` '
        'routes to self-chat with `announcement_cc_mode: off`. `versions` in `step_defaults` '
        'overrides the shown SDK versions. Missing, unreadable or malformed '
        'config/announcement_cc.yaml blocks preparation instead of silently dropping the CC members.\n',
 'faqs': [{'q': 'Where do the versions come from?',
           'a': 'state.versions — populated at Phase 2 by build_verify.orchestrator_health '
                'from the orchestrator run tags. The announcement shows Common/MSAL/Broker '
                '(the authenticator branch is stored too but not shown in this SDK table).'},
          {'q': 'Why not WorkIQ like the other Teams messages?',
           'a': 'WorkIQ can only REPLY to an existing channel message; a brand-new channel '
                'post requires the microsoft_teams MCP (SendMessageToChannel). The 1:1/group '
                'chat reminders use WorkIQ; this channel announcement uses microsoft_teams.'},
          {'q': 'Does the cc line @-tag CP/Intune, LTW, OneAuth, Native Auth?',
           'a': "Those are Teams TAGS, and the microsoft_teams tool can't @-mention a tag (it "
                "maps the id to an unknown user). So instead the step @-mentions the tags' "
                'MEMBERS as individual users — maintained by hand in '
                'config/announcement_cc.yaml ({name,email} per group). Controlled by '
                '`teams.announcement_cc_mode` in `config/environment.yaml`: '
                "'mention' (`prod` default), 'self' (ping only self_email), or "
                "'off' (`test` default, plain text); `cc_members` overrides the registry."}]}
