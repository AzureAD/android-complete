"""Send the Authenticator rollout-start notice from exact release evidence.

The model is source-only: final app version/commit, recorded RC build, Authenticator
test suite, payload page, reachable Authenticator/DID commits and EcsFlight code
defaults. It never invents Major/Minor classifications, rollout intent or progression
dates.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from html import escape as _html_escape
import re

from orchestrator import delivery, schedule
from orchestrator.outcomes import Blocked, NeedsSkill
from orchestrator.step_context import StepContext, thaw
from steps.lib import templating as T
from steps.lib.mockctx import MISSING
from tools import testplans as TP
from tools.coordinates import coords
from tools.pipelines.auth_app import auth_branch_url, auth_build_url

ID = "notice"
KIND = "scout"
NOTIFICATION = True

CONFIG = {
    "to": ["MAuthenticatorRel@microsoft.com"],
    "cc": ["windevxeng@microsoft.com"],
}

MOCKABLE = {
    "send_to": {
        "kind": "input",
        "desc": "Redirect all delivery to these addresses and clear the real CC list.",
    },
    "build": {
        "kind": "input",
        "desc": "Inject final build {build_id,version,commit,build_number}; skip live build lookup.",
    },
    "manifest": {
        "kind": "input",
        "desc": "Inject the exact release source manifest; skip live Authenticator git reads.",
    },
}

_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.I)
_PROGRESSION = (
    ("Initial Release Notification Email", "Done", "Intent to begin staged rollout"),
    ("Publishing to BETA 100%", "Not Started", "Rollout owner schedules this stage"),
    ("Publishing to PROD 5%", "Not Started", "Rollout owner schedules this stage"),
    ("Publishing to PROD 25%", "Not Started", "Rollout owner schedules this stage"),
    ("Publishing to PROD 50%", "Not Started", "Rollout owner schedules this stage"),
    ("Publishing to PROD 100%", "Not Started", "Rollout owner schedules this stage"),
    ("Publishing to Partner Stores: Samsung, China, Intune AOSP",
     "Not Started", "Partner-store dates are tracked during rollout"),
    ("Final release completion mail notification",
     "Not Started", "Sent after rollout and partner-store completion"),
)


def _positive_id(value):
    return (not isinstance(value, bool) and str(value).isdigit() and int(value) > 0)


def _build_info(context):
    injected = context.input("build", MISSING)
    if injected is not MISSING:
        info = thaw(injected)
        detail = ""
    else:
        branch = context.release.versions.get("authenticator")
        if not branch:
            return (None, "no Authenticator release branch on state.versions")
        ok, info, detail = context.services.pipelines.find_auth_release_build(branch)
        if not ok or not info:
            return (None, detail or "no successful final Authenticator release build")
    if (not isinstance(info, dict) or not _positive_id(info.get("build_id"))
            or not _VERSION.fullmatch(str(info.get("version") or ""))
            or not _COMMIT.fullmatch(str(info.get("commit") or ""))):
        return (None, "final Authenticator build identity/version/commit is incomplete")
    return (info, "")


def _manifest(context, branch, commit):
    injected = context.input("manifest", MISSING)
    if injected is not MISSING:
        manifest = thaw(injected)
        detail = ""
    else:
        ok, manifest, detail = context.services.pipelines.release_change_manifest(
            branch, commit)
        if not ok:
            return (None, detail)
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("branch") != branch
            or str(manifest.get("target_commit") or "").lower() != commit.lower()
            or not isinstance(manifest.get("general"), list)
            or not isinstance(manifest.get("did"), list)
            or not isinstance(manifest.get("flight_changes"), dict)):
        return (None, "release source manifest does not match the final build")
    for section in ("general", "did"):
        for item in manifest[section]:
            if (not isinstance(item, dict)
                    or not _COMMIT.fullmatch(str(item.get("commit") or ""))
                    or not isinstance(item.get("title"), str)
                    or not isinstance(item.get("url"), str)
                    or type(item.get("mixed")) is not bool
                    or (item.get("id") is not None and not _positive_id(item["id"]))):
                return (None, f"release source manifest has invalid {section} entry")
    flights = manifest["flight_changes"]
    if (not isinstance(flights.get("added"), list)
            or not isinstance(flights.get("default_changed"), list)):
        return (None, "release source manifest has invalid flight changes")
    for item in flights["added"]:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(key), str) or not item[key]
                       for key in ("key", "default"))):
            return (None, "release source manifest has invalid flight entry")
    for item in flights["default_changed"]:
        if (not isinstance(item, dict)
                or any(not isinstance(item.get(key), str) or not item[key]
                       for key in ("key", "default", "previous_default"))):
            return (None, "release source manifest has invalid changed-flight entry")
    return (manifest, "")


def _plans(context):
    auth = context.evidence.step("bug_bash", "clone_plans_auth")
    suite_id = auth.data.get("suite_id")
    auth_plan = coords.testplan("authenticator")["plan"]
    if auth.status != "done" or not _positive_id(suite_id):
        return (None, "completed Authenticator release test suite is missing")
    return ({
        "authenticator": {
            "name": auth.data.get("suite_name") or f"Authenticator suite {suite_id}",
            "url": TP.plan_web_url(auth_plan, suite_id),
        },
    }, "")


def _recorded_release_build(context):
    runs = thaw(context.evidence.pipeline_runs).get("rcs") or []
    build = (
        (((runs[-1].get("auth") or {}).get("build") or {}))
        if isinstance(runs, list) and runs and isinstance(runs[-1], dict)
        else {}
    )
    run_id = build.get("run_id")
    if not _positive_id(run_id):
        return (None, "latest RC has no recorded Authenticator build from pipeline 475778")
    return ({
        "run_id": str(run_id),
        "url": auth_build_url(run_id),
    }, "")


def _payload_url(context):
    step = context.evidence.step("finalize", "wiki_payload")
    if step.status != "done":
        return None
    return next(
        (link.get("url") for link in step.links
         if link.get("url") and "payload" in str(link.get("name") or "").lower()),
        None,
    )


def _source_entry_html(entry):
    label = f"PR {entry['id']}" if entry.get("id") else f"Commit {entry['commit'][:8]}"
    mixed = " <strong>[Authenticator + DID]</strong>" if entry.get("mixed") else ""
    return (
        f'<li><a href="{_attr(entry.get("url"))}" style="color:#0b5cad;">'
        f'{T.esc(label)}</a>: {T.esc(entry.get("title"))}{mixed}</li>'
    )


def _source_list_html(entries, empty):
    return (
        '<ul style="margin:0;padding-left:20px;color:#344054;">'
        + "".join(_source_entry_html(item) for item in entries)
        + "</ul>"
        if entries else f'<p style="margin:0;color:#667085;"><em>{T.esc(empty)}</em></p>'
    )


def _attr(value):
    return _html_escape(str(value or ""), quote=True)


def _link_html(label, url):
    return (f'<a href="{_attr(url)}">{T.esc(label)}</a>' if url else T.esc(label))


def _flight_html(flights):
    rows = []
    for item in flights.get("added", []):
        rows.append(
            f"<li><strong>{T.esc(item['key'])}</strong> — added with code default "
            f"<code>{T.esc(item['default'])}</code></li>"
        )
    for item in flights.get("default_changed", []):
        rows.append(
            f"<li><strong>{T.esc(item['key'])}</strong> — code default changed from "
            f"<code>{T.esc(item['previous_default'])}</code> to "
            f"<code>{T.esc(item['default'])}</code></li>"
        )
    return (
        '<ul style="margin:8px 0 0;padding-left:20px;color:#344054;">'
        + "".join(rows) + "</ul>"
        if rows else
        '<p style="margin:8px 0 0;color:#667085;"><em>'
        'No EcsFlight additions or code-default changes detected.</em></p>'
    )


def _status_pill(status):
    if status == "Done":
        bg, fg = "#d1fadf", "#027a48"
    else:
        bg, fg = "#f2f4f7", "#475467"
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;'
        f'background:{bg};color:{fg};font-size:12px;font-weight:600;white-space:nowrap;">'
        f'{T.esc(status)}</span>'
    )


def _progression_html(sent_date):
    rows = []
    for index, (name, status, note) in enumerate(_PROGRESSION):
        date = sent_date if index == 0 else "To be scheduled"
        rows.append(
            "<tr>"
            f'<td style="padding:11px 14px;border-top:1px solid #eaecf0;font-weight:600;">{T.esc(name)}</td>'
            f'<td style="padding:11px 14px;border-top:1px solid #eaecf0;">{_status_pill(status)}</td>'
            f'<td style="padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;white-space:nowrap;">{T.esc(date)}</td>'
            f'<td style="padding:11px 14px;border-top:1px solid #eaecf0;color:#475467;font-size:13px;">{T.esc(note)}</td>'
            "</tr>"
        )
    return "".join(rows)


def render_html(model):
    sdk = "".join(
        f'<tr><td style="padding:8px 14px;border-top:1px solid #eaecf0;font-weight:600;">'
        f'{T.esc(label)}</td><td style="padding:8px 14px;border-top:1px solid #eaecf0;'
        f'font-family:Consolas,monospace;">{T.esc(model["versions"][key])}</td></tr>'
        for key, label in (("broker", "Broker"), ("common", "Common"), ("msal", "MSAL"))
    )
    omitted = len(model["manifest"].get("generated_omitted") or [])
    omitted_note = (
        f'<p style="margin:10px 0 0;color:#667085;font-size:12px;"><em>'
        f'{omitted} generated localization-only commit(s) omitted.</em></p>'
        if omitted else ""
    )
    validation = (
        '<div style="background:#fff4ce;border:1px solid #e6b800;border-radius:4px;'
        f'padding:12px;margin-bottom:15px;"><strong>Validation copy:</strong> '
        f'{T.esc(model["validation_note"])}</div>'
        if model.get("validation_note") else ""
    )
    resources = (
        f'<tr><td style="padding:8px 14px;border-top:1px solid #eaecf0;font-weight:600;">'
        f'Authenticator test suite</td><td style="padding:8px 14px;border-top:1px solid #eaecf0;">'
        f'{_link_html(model["plans"]["authenticator"]["name"], model["plans"]["authenticator"]["url"])}</td></tr>'
        f'<tr><td style="padding:8px 14px;border-top:1px solid #eaecf0;font-weight:600;">'
        f'Release payload</td><td style="padding:8px 14px;border-top:1px solid #eaecf0;">'
        f'<a href="{_attr(model["payload_url"])}">{T.esc(model["month_year"])} payload page</a></td></tr>'
    )
    return f"""\
<div style="font-family:'Segoe UI',-apple-system,Arial,sans-serif;color:#101828;max-width:760px;margin:0 auto;font-size:14px;line-height:1.5;">
  <div style="padding:20px 22px;background:#0b3a6f;border-radius:12px 12px 0 0;color:#fff;">
    <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.8;">Android Authenticator — Release Intent</div>
    <div style="font-size:22px;font-weight:700;margin-top:4px;">{T.esc(model['month_year'])} Release</div>
  </div>
  <div style="border:1px solid #eaecf0;border-top:none;border-radius:0 0 12px 12px;padding:22px;">
    {validation}
    <p style="margin-top:0;">Hello everyone,</p>
    <p>This is a notice of intent to start the Android Authenticator
       <strong>{T.esc(model['month_year'])}</strong> release.</p>

    <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:18px 0 24px;border-collapse:separate;border-spacing:8px 0;">
      <tr>
        <td style="width:33%;padding:14px;background:#f2f7ff;border:1px solid #d1e9ff;border-radius:8px;">
          <div style="font-size:11px;color:#475467;text-transform:uppercase;letter-spacing:.04em;">App version</div>
          <div style="font-size:18px;font-weight:700;margin-top:4px;">{T.esc(model['build']['version'])}</div>
        </td>
        <td style="width:33%;padding:14px;background:#f9fafb;border:1px solid #eaecf0;border-radius:8px;">
          <div style="font-size:11px;color:#475467;text-transform:uppercase;letter-spacing:.04em;">Release build</div>
          <div style="font-size:14px;font-weight:600;margin-top:4px;"><a href="{_attr(model['release_build']['url'])}" style="color:#175cd3;text-decoration:none;">Run {T.esc(model['release_build']['run_id'])}</a></div>
        </td>
        <td style="width:34%;padding:14px;background:#f9fafb;border:1px solid #eaecf0;border-radius:8px;">
          <div style="font-size:11px;color:#475467;text-transform:uppercase;letter-spacing:.04em;">Release branch</div>
          <div style="font-size:13px;font-weight:600;margin-top:4px;"><a href="{_attr(model['branch_url'])}" style="color:#175cd3;text-decoration:none;">{T.esc(model['branch'])}</a></div>
        </td>
      </tr>
    </table>

    <h3 style="margin:0 0 8px;font-size:15px;">Release resources</h3>
    <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #eaecf0;border-radius:8px;overflow:hidden;">
      <tr style="background:#f9fafb;"><th align="left" style="padding:8px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Resource</th><th align="left" style="padding:8px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Link</th></tr>
      {resources}
    </table>

    <h3 style="margin:26px 0 8px;font-size:15px;">Authenticator</h3>
    <div style="border:1px solid #eaecf0;border-radius:8px;padding:14px;">
      {_source_list_html(model['manifest']['general'], 'No non-generated Authenticator changes found.')}
      {omitted_note}
    </div>

    <h3 style="margin:26px 0 8px;font-size:15px;">DID</h3>
    <div style="border:1px solid #eaecf0;border-radius:8px;padding:14px;">
      {_source_list_html(model['manifest']['did'], 'No DID-path changes found in the exact release commit range.')}
    </div>

    <h3 style="margin:26px 0 8px;font-size:15px;">Auth Client Android SDKs</h3>
    <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;min-width:280px;border:1px solid #eaecf0;border-radius:8px;overflow:hidden;">
      <tr style="background:#f9fafb;"><th align="left" style="padding:8px 14px;font-size:12px;color:#475467;text-transform:uppercase;">SDK</th><th align="left" style="padding:8px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Version</th></tr>
      {sdk}
    </table>

    <h3 style="margin:26px 0 8px;font-size:15px;">Feature flags introduced or changed</h3>
    <div style="background:#fffaeb;border:1px solid #fedf89;border-radius:8px;padding:12px 14px;">
      <strong style="color:#b54708;">Source defaults, not rollout intent</strong>
      <div style="color:#475467;font-size:13px;">Default-true additions require owner review before rollout.</div>
      {_flight_html(model['manifest']['flight_changes'])}
    </div>

    <h3 style="margin:26px 0 8px;font-size:15px;">Safe Fly Request</h3>
    <div style="background:#f9fafb;border:1px solid #eaecf0;border-radius:8px;padding:12px 14px;color:#475467;">
      Release: <strong>{T.esc(model['month_year'])}</strong>. No separate structured Safe Fly request is recorded by release-agent.
    </div>

    <h3 style="margin:26px 0 8px;font-size:15px;">Release Progression</h3>
    <table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #eaecf0;border-radius:8px;overflow:hidden;">
      <tr style="background:#f9fafb;">
        <th align="left" style="padding:10px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Step</th>
        <th align="left" style="padding:10px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Status</th>
        <th align="left" style="padding:10px 14px;font-size:12px;color:#475467;text-transform:uppercase;">ETA / Date</th>
        <th align="left" style="padding:10px 14px;font-size:12px;color:#475467;text-transform:uppercase;">Notes</th>
      </tr>
      {_progression_html(model['sent_date'])}
    </table>
    <p style="margin:26px 0 0;color:#344054;">Thank you,<br>{T.esc(model['owner'])}</p>
    <p style="margin:16px 0 0;color:#98a2b3;font-size:11px;">Generated from exact release build, branch, Authenticator test-suite, payload and source evidence.</p>
  </div>
</div>"""


def render_plain(model):
    def entries(rows, empty):
        return "\n".join(
            f"- {'PR ' + str(item['id']) if item.get('id') else 'Commit ' + item['commit'][:8]}: "
            f"{item['title']}{' [Authenticator + DID]' if item.get('mixed') else ''} "
            f"({item['url']})"
            for item in rows
        ) or f"- {empty}"

    flights = [
        f"- {item['key']}: added; code default {item['default']}"
        for item in model["manifest"]["flight_changes"].get("added", [])
    ] + [
        f"- {item['key']}: code default {item['previous_default']} -> {item['default']}"
        for item in model["manifest"]["flight_changes"].get("default_changed", [])
    ]
    validation = (f"VALIDATION COPY: {model['validation_note']}\n\n"
                  if model.get("validation_note") else "")
    return (
        f"{validation}Hello everyone,\n\nThis is a notice of intent to start the Android Authenticator "
        f"{model['month_year']} release.\n\nApp Version\n{model['build']['version']}\n\n"
        f"Monthly release\n- Authenticator test suite: {model['plans']['authenticator']['url']}\n"
        f"- Release build: {model['release_build']['url']}\n"
        f"- Release branch: {model['branch_url']}\n"
        f"- Release payload: {model['payload_url']}\n\nAuthenticator\n"
        f"{entries(model['manifest']['general'], 'No general changes found.')}\n\nDID\n"
        f"{entries(model['manifest']['did'], 'No DID-path changes found.')}\n\n"
        f"Auth Client Android SDKs\n- Broker: {model['versions']['broker']}\n"
        f"- Common: {model['versions']['common']}\n- MSAL: {model['versions']['msal']}\n\n"
        f"Feature flags (code defaults; not rollout intent)\n"
        f"{chr(10).join(flights) or '- No additions/default changes detected.'}\n\n"
        f"Safe Fly Request\nRelease: {model['month_year']}; no structured request recorded.\n\n"
        f"Thank you,\n{model['owner']}"
    )


def _state_matches(context):
    matches = [(["versions"], context.release.versions)]
    for key in (
        "bug_bash.clone_plans_auth",
        "finalize.wiki_payload",
    ):
        matches.append((["steps", key], thaw(asdict(context.evidence.steps[key]))))
    runs = thaw(context.evidence.pipeline_runs).get("rcs") or []
    matches.append((
        ["pipeline_runs", "rcs", -1, "auth", "build"],
        runs[-1]["auth"]["build"],
    ))
    matches.extend((
        (["target_month"], context.release.target_month),
        (["owner_email"], context.release.owner_email),
    ))
    return [
        {"path": path, "hash": delivery.fingerprint(value)}
        for path, value in matches
    ]


def build(context: StepContext):
    branch = str(context.release.versions.get("authenticator") or "")
    if not re.fullmatch(r"release/\d{4}/\d{2}/\d{2}", branch):
        return Blocked("rollout_start.notice: canonical Authenticator release/YYYY/MM/DD "
                       "branch is missing")
    versions = thaw(context.release.versions)
    missing_versions = [key for key in ("broker", "common", "msal") if not versions.get(key)]
    if missing_versions:
        return Blocked("rollout_start.notice: missing final SDK versions: "
                       + ", ".join(missing_versions))
    build_injected = context.input("build", MISSING) is not MISSING
    manifest_injected = context.input("manifest", MISSING) is not MISSING
    redirected = context.input("send_to", MISSING)
    if (build_injected or manifest_injected) and redirected is MISSING:
        return Blocked(
            "rollout_start.notice: injected build/manifest evidence requires send_to; "
            "test evidence can never target production recipients")
    build_info, build_detail = _build_info(context)
    if not build_info:
        return Blocked(f"rollout_start.notice: {build_detail}")
    manifest, manifest_detail = _manifest(context, branch, build_info["commit"])
    if not manifest:
        return Blocked(f"rollout_start.notice: could not derive exact Authenticator/DID payload "
                       f"({manifest_detail})")
    plans, plan_detail = _plans(context)
    if not plans:
        return Blocked(f"rollout_start.notice: {plan_detail}")
    release_build, release_build_detail = _recorded_release_build(context)
    if not release_build:
        return Blocked(f"rollout_start.notice: {release_build_detail}")
    payload_url = _payload_url(context)
    if not payload_url:
        return Blocked("rollout_start.notice: completed release payload page link is missing")
    if redirected is MISSING:
        recipients, cc, prefix = list(CONFIG["to"]), list(CONFIG["cc"]), ""
    else:
        recipients = ([str(item).strip() for item in redirected]
                      if isinstance(redirected, (list, tuple))
                      else [item.strip() for item in str(redirected).split(",")])
        recipients, cc, prefix = [item for item in recipients if item], [], "[TEST → me] "
    if not recipients or any("@" not in item for item in recipients):
        return Blocked("rollout_start.notice: invalid or empty recipient list")
    month_year = schedule.target_month_label(context.release) or context.release.release_id
    model = {
        "month_year": month_year,
        "owner": context.release.owner_name or context.release.owner_email or "Release owner",
        "branch": branch,
        "branch_url": auth_branch_url(branch),
        "build": {**build_info, "url": auth_build_url(build_info["build_id"])},
        "release_build": release_build,
        "plans": plans,
        "payload_url": payload_url,
        "versions": versions,
        "manifest": manifest,
        "sent_date": context.clock.now().strftime("%A, %B %d, %Y"),
    }
    subject = (
        f"{prefix}Android Authenticator {month_year} release intent — "
        f"{build_info['version']}"
    )
    html, plain = render_html(model), render_plain(model)
    checkpoint = (
        f"auth-rollout:{release_build['run_id']}:{build_info['build_id']}:"
        f"{build_info['commit']}:"
        f"{delivery.fingerprint(manifest)}"
    )
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "cc": cc,
            "subject": subject,
            "body": html,
            "isHtml": True,
            "_plain_body": plain,
        },
        record_as=ID,
        summary=(
            f"Send {month_year} Authenticator rollout intent to {', '.join(recipients)}"
            + (f"; CC {', '.join(cc)}" if cc else " (test redirect; real CC cleared)")
        ),
        note=(
            f"Authenticator {build_info['version']} rollout-start notice; "
            f"{len(manifest['general'])} general and {len(manifest['did'])} DID source entries"
        ),
        outbound=True,
        notification={
            "checkpoint": checkpoint,
            "state_matches": _state_matches(context),
            "completion": {
                "status": "pass",
                "note": f"Authenticator {build_info['version']} rollout-start notice delivered.",
                "links": [
                    {"name": "Authenticator release build", "url": release_build["url"]},
                    {"name": "Release payload page", "url": payload_url},
                ],
            },
        },
    )
