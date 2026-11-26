"""Step: `final_status_email` — send the CLOSING partner status email (Phase 4, finalize).

Scout-assisted: `build()` composes the closing email (the Phase-4-complete snapshot) and returns
NeedsSkill(workiq_send_email); Python's Agency MCP delivery (`deliver-step` / `next --deliver`)
sends it silently and records completion.

Step defaults (`step_defaults` / offline scenarios):
  send_to : explicit recipient override (normally controlled by `emails.partner_status` in `config/environment.yaml`).
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import MISSING

STATUS_EMAIL = True
ID = "final_status_email"
KIND = "scout"
NAME = 'Send the closing partner status email'
REQUIRED_TOOLS = ('workiq',)
REQUIRES_APPROVAL = False
NOTIFICATION = True

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
    from orchestrator import environment as env
    from steps.lib.context import resolve_recipients

    override = context.input("send_to", MISSING)
    if override is not MISSING and override:
        try:
            recipients, _rnote, prefix = resolve_recipients(context, None, group_key="partner_status")
        except ValueError as exc:
            return Blocked(f"final_status_email: {exc}")
    elif env.active_profile_name() != "prod":
        try:
            recipients, _rnote, prefix = resolve_recipients(context, None, group_key="partner_status")
        except ValueError as exc:
            return Blocked(f"final_status_email: {exc}")
    else:
        live_dl = context.services.assets.status_recipients()
        recipients, _rnote, prefix = resolve_recipients(context, live_dl, group_key="partner_status")
    if not recipients:
        return Blocked("final_status_email: no status-email recipients configured "
                       "(config/environment.yaml profiles.<active>.emails.partner_status).")

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
        "DLs (`emails.partner_status` in `config/environment.yaml`). Under `active_profile: test`, "
        'emails route exclusively to `@owner` with `[TEST]` subject prefix; `send_to` remains an '
        'explicit per-step recipient override.\n',
 'where': ['recipients: config/environment.yaml emails.partner_status (@owner in test; '
           'authsdkrelease@, androididentity@ in prod)',
           'template: orchestrator/status_email.py'],
 'how': 'Delivered automatically via `deliver-step --release <id> --phase finalize --step '
        'final_status_email --json` (or `next --deliver`), which claims the exact snapshot, '
        'sends via Agency MCP, and acknowledges confirmed delivery.\n',
 'faqs': [{'q': 'How are recipients controlled between rehearsal and launch?',
           'a': 'Keep `active_profile: test` in `config/environment.yaml` (routes to `@owner`), then switch to `prod` at launch.'}]}
