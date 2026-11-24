"""Step: `final_reminder` — the CCD-day final code-complete reminder EMAIL (Phase 1, P1-1).

The morning-of Code Complete Day reminder to the Android distribution list: "code
complete is TODAY; anything after this needs the hotfix cherry-pick process + EM
approval." It's the `update` variant of the same notice template Phase 0's `notice`
uses (which sends the CCD-7 `initial` variant a week earlier).

Sending email needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` resolves the email deterministically and returns a
NeedsSkill(workiq_send_email) for the skill to send. Redirect for tests with the
`send_to` mock knob (keeps the send real, points it at you).
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator import notifications as notif, schedule, status_email as SE
from steps.lib import templating as T
from steps.lib.context import release_ctx, resolve_recipients

ID = "final_reminder"
KIND = "scout"
NAME = 'CCD final code-complete reminder (email)'
REQUIRED_TOOLS = ('workiq',)
REQUIRES_APPROVAL = False
NOTIFICATION = True


@dataclass(frozen=True)
class BuildParameters:
    variant: str | None = None


PARAMETERS = {"build": BuildParameters}

CONFIG = {
    "template": "templates/early-code-complete-notice.md",
    "variant": "update",                     # CCD-day reminder ("Today")
}

MOCKABLE = {
    "send_to": {
        "kind": "payload", "sets": "to", "as": "list", "tag_subject": True,
        "desc": "Send the email for real, but only to these address(es) (DL → you).",
    },
    "variant": {
        "kind": "input",
        "desc": "Force the notice variant: update (CCD-day) | initial (CCD-7).",
    },
}


def _resolve_dl(context) -> list[str]:
    try:
        return context.services.assets.status_recipients()
    except ValueError as exc:
        if "was not supplied" not in str(exc):
            raise
        return notif.partner_recipients()


def build(context: StepContext[BuildParameters]):
    """Resolve the CCD-day reminder into a NeedsSkill(workiq_send_email) using the unified card."""
    variant = context.parameters.variant
    if not context.release.ccd:
        return Blocked("no CCD set for this release")
    ccd = schedule.parse_date(context.release.ccd)
    if ccd is None:
        return Blocked("invalid CCD for this release")

    cfg = CONFIG
    variant = variant or context.input("variant") or cfg.get("variant", "update")
    if not isinstance(variant, str):
        return Blocked("final reminder variant must be text")
    parsed = T.select_template(context.services.assets.template(cfg.get("template")), variant)
    if isinstance(parsed, dict) and "error" in parsed:
        return Blocked(parsed["error"])
    subject_tpl, body_tpl = parsed

    ctx = release_ctx(context)
    is_update = variant.casefold() == "update"
    today = is_update and context.clock.now().date() == ccd
    subject = T.fill(subject_tpl, ctx)
    body = T.fill(body_tpl, ctx)
    if not today:
        subject = subject.replace(" (Today)", f" ({ctx['ccd_date']})" if is_update else "")
        body = body.replace("**Today**", f"**{ctx['ccd_long']}**")

    live_dl = _resolve_dl(context)
    recipients, rnote, prefix = resolve_recipients(context, live_dl)
    stage_mode = "ccd" if is_update else "preflight"
    composed = SE.compose(
        context.release,
        [],
        recipients,
        stage=stage_mode,
        ccd_today=today,
        ccd_long=ctx["ccd_long"],
    )
    subject_out = f"{prefix}{subject}"

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
        summary=f"Email the CCD-day code-complete reminder to {len(recipients)} recipient(s) ({rnote})",
        note=f"sent to {', '.join(recipients) if recipients else '(no recipients)'}",
        outbound=True,
    )

KNOWLEDGE = {'summary': 'Email the Android DL that code complete is TODAY (Code Complete Day).',
 'what': "The CCD-day final code-complete reminder email — the 'update' variant of the same "
         'notice template Phase 0 sends a week earlier at CCD-7. Tells everyone code complete '
         'is today and that any check-ins after it need the hotfix cherry-pick process + EM '
         'approval. Catch-up sends show the actual CCD instead of incorrectly calling a past '
         'date Today; the initial variant uses the date too.\n',
 'who': 'Scout sends it automatically (via the optional 7:00 AM morning drain or the next '
        'interactive session once CCD arrives). The release owner owns the content/recipients.\n',
 'how': 'Delivered automatically in Python via Agency MCP (`deliver-step` / `next --deliver`) '
        'using the exact same template and durable claim/result protocol as Phase 0 `notice`. '
        'To test, redirect with the send_to mock knob (keeps the send real, points it at you).\n',
 'faqs': [{'q': 'How is this different from the Phase 0 notice?',
           'a': "Same template and delivery path, different variant/timing: Phase 0 'notice' "
                "sends the 'initial' variant at CCD-7; this sends the 'update' variant ('Today') on CCD."}]}
