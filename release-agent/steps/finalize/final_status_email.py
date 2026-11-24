"""Step: `final_status_email` — send the CLOSING partner status email (Phase 4, finalize).

Scout-assisted: `build()` composes the closing email (the Phase-4-complete snapshot) and returns
NeedsSkill(workiq_send_email); Python's Agency MCP delivery (`deliver-step` / `next --deliver`)
sends it silently and records completion.

Mock knobs (mocks.local.yaml / tests):
  send_to : redirect recipients to these address(es) (owner → you) for a test send.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.mockctx import MISSING

STATUS_EMAIL = True
ID = "final_status_email"
KIND = "scout"
NAME = 'Send the closing partner status email'
REQUIRED_TOOLS = ('workiq',)
REQUIRES_APPROVAL = False
NOTIFICATION = True

MOCKABLE = {
    "send_to": {
        "kind": "input",
        "desc": "Redirect the closing status email to these address(es) (DL → you).",
    },
    "changelogs": {
        "kind": "input",
        "desc": "Inject dict {common:[...], msal:[...], broker:[...]} to skip live changelog reads.",
    },
}


def _sdk_changelogs(context) -> dict[str, list[dict]]:
    injected = context.input("changelogs", MISSING)
    if injected is not MISSING and isinstance(injected, dict):
        return {k: list(v or []) for k, v in injected.items()}
    out: dict[str, list[dict]] = {}
    try:
        from steps.finalize import integ_prs as IP
        from tools import prs as PR
        versions = getattr(context.release, "versions", None) or {}
        reader = context.services.repositories.repo_change_list
        for key in ("common", "msal", "broker"):
            gh = (IP.CONFIG.get(key) or {}).get("gh_repo")
            ver = versions.get(key)
            path = PR.CHANGELOG_PATHS.get(key)
            if not (gh and ver and path):
                continue
            if key == "broker" and reader is None:
                ok, entries, _ = context.services.repositories.broker_change_list(gh, ver)
            elif reader is not None:
                ok, entries, _ = reader(gh, ver, changelog_path=path)
            else:
                ok, entries, _ = PR.repo_change_list(gh, ver, changelog_path=path)
            if ok and entries:
                out[key] = entries
    except Exception:  # noqa: BLE001
        return out
    return out


def build(context: StepContext):
    to = context.input("send_to", MISSING)
    if to is not MISSING and to:
        recipients = (
            [str(x).strip() for x in to if str(x).strip()]
            if isinstance(to, list)
            else [x.strip() for x in str(to).split(",") if x.strip()]
        )
        prefix = "[TEST → me] "
    else:
        recipients = context.services.assets.status_recipients()
        prefix = ""
    if not recipients:
        return Blocked("final_status_email: no status-email recipients configured "
                       "(config/notifications.yaml status_email.recipients).")

    changelogs = _sdk_changelogs(context)
    res = context.services.assets.status_email(
        recipients,
        changes=changelogs.get("broker") or [],
        changelogs=changelogs,
        stage="final",
    )
    subject = f"{prefix}{res['subject']}"
    month_year = res["model"].get("month_year", "")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "subject": subject,
            "body": res["html"],
            "isHtml": True,
        },
        record_as=ID,
        summary=f"Send the CLOSING {month_year} status email to "
                f"{len(recipients)} recipient(s)",
        note="final status email (Phase 4 complete)",
        outbound=True,
        notification={"completion": {"note": "Closing partner status email delivered."}},
    )

KNOWLEDGE = {'summary': 'Send the closing partner status email when Phase 4 completes.',
 'what': 'Terminal Phase-4 step that sends the Phase-4-complete partner status email '
         'summarizing finalized versions, Maven publishing, and rollout readiness.\n',
 'who': 'Scout composes it and delivers it automatically in Python via Agency MCP to the release '
        "DLs (notifications.yaml beside the invocation's phases.yaml, status_email.recipients). An "
        'isolated test configuration is respected; the canonical production recipients are not '
        'a fallback. The `send_to` mock remains an explicit recipient override.\n',
 'where': ['recipients: config/notifications.yaml status_email.recipients (authsdkrelease@, '
           'androididentity@)',
           'template: orchestrator/status_email.py'],
 'how': 'Delivered automatically via `deliver-step --release <id> --phase finalize --step '
        'final_status_email --json` (or `next --deliver`), which claims the exact snapshot, '
        'sends via Agency MCP, and acknowledges confirmed delivery. To test, configure isolated '
        'recipients or set the `send_to` mock to your own address.\n',
 'faqs': [{'q': 'How can I redirect this email during testing?',
           'a': 'Set `finalize.final_status_email.send_to` in `mocks.local.yaml` to your own email address.'}]}
