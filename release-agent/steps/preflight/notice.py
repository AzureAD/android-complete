"""Step: `notice` — the early code-complete notice email (Phase 0, maps to S0).

ONE self-contained home for the whole step: logic (`build`) and config (`CONFIG`).
Recipients and subject prefixes are resolved from `config/environment.yaml`.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator import notifications as notif, schedule, status_email as SE
from steps.lib import templating as T
from steps.lib.context import release_ctx, resolve_recipients

ID = "notice"
KIND = "scout"
NAME = 'Send early release notice'
REQUIRED_TOOLS = ('workiq',)
REQUIRES_APPROVAL = False
NOTIFICATION = True

@dataclass(frozen=True)
class BuildParameters:
    variant: str | None = None

PARAMETERS = {"build": BuildParameters}

CONFIG = {
    "template": "templates/early-code-complete-notice.md",
    "variant": "initial",                    # initial (CCD-7) | update (CCD-day)
}

def _resolve_dl(context) -> list[str]:
    try:
        return context.services.assets.status_recipients()
    except ValueError as exc:
        if "was not supplied" not in str(exc):
            raise
        return notif.partner_recipients()

def build(context: StepContext[BuildParameters]):
    """Resolve the Phase-0 notice into a NeedsSkill(workiq_send_email) using the unified card."""
    variant = context.parameters.variant
    if not context.release.ccd:
        return Blocked("no CCD set for this release")
    ccd = schedule.parse_date(context.release.ccd)
    if ccd is None:
        return Blocked("invalid CCD for this release")

    cfg = CONFIG
    variant = variant or context.input("variant") or cfg.get("variant", "initial")
    if not isinstance(variant, str):
        return Blocked("notice variant must be text")
    tpl_rel = cfg.get("template", "templates/early-code-complete-notice.md")
    parsed = T.select_template(context.services.assets.template(tpl_rel), variant)
    if isinstance(parsed, dict) and "error" in parsed:
        return Blocked(parsed["error"])
    subject_tpl, body_tpl = parsed

    ctx = release_ctx(context)
    is_update = variant.casefold() == "update"
    today = is_update and context.clock.now().date() == ccd
    subject = T.fill(subject_tpl, ctx)
    body = T.fill(body_tpl, ctx)
    if is_update and not today:
        subject = subject.replace(" (Today)", f" ({ctx['ccd_date']})")
        body = body.replace("**Today**", f"**{ctx['ccd_long']}**")

    live_dl = _resolve_dl(context)
    recipients, rnote, prefix = resolve_recipients(context, live_dl, group_key="partner_status")
    stage_mode = "ccd" if is_update else "preflight"
    composed = SE.compose(
        context.release,
        [],
        recipients,
        stage=stage_mode,
        ccd_today=today,
        ccd_long=ctx["ccd_long"],
    )
    subject_out = f"{prefix}{subject}" if prefix and not subject.startswith("[TEST") else subject
    note_prefix = "[test-redirect] " if prefix else ""

    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "subject": subject_out,
            "body": composed["html"],
            "isHtml": True,
            "_plain_body": body,
        },
        record_as=ID,
        summary=f"Email the code-complete notice to {len(recipients)} recipient(s) ({rnote})",
        note=f"{note_prefix}sent to {', '.join(recipients) if recipients else '(no recipients)'}",
        outbound=True,
    )

KNOWLEDGE = {'summary': 'Email the early code-complete notice using the unified partner status template.',
 'what': "Sends a heads-up to the consolidated partner DL that code complete for the month's "
         'SDK release is approaching (showing the 4-milestone card with Code Complete ETA and '
         'the hotfix cherry-pick policy, without versions or changelogs).\n',
 'how': 'Delivered automatically in Python via Agency MCP (`deliver-step` / `next --deliver`) '
        'to `emails.partner_status` from `config/environment.yaml` (`@owner` in `test` profile, '
        'full DLs in `prod` profile).\n',
 'faqs': [{'q': 'Who receives it?',
           'a': 'The consolidated distribution list in config/notifications.yaml '
                '(status_email.recipients). Redirect for testing with status_email.send_to.'},
          {'q': "What's the difference between the 'initial' and 'update' variant?",
           'a': "'initial' is the CCD-7 heads-up (Scheduled); 'update' is the CCD-day reminder "
                "('Code Complete is Today' / In Progress). Set via --param variant=update."}]}
