"""Step: `notice` — the early code-complete notice email (Phase 0, maps to S0).

ONE home for the whole step (was spread across commands/notice.py, templates/,
config/preflight.yaml, and skill/reference/phases/preflight.md). Sending email
needs the WorkIQ MCP the engine can't reach, so this is a `scout` step: `build()`
resolves everything deterministically and returns a NeedsSkill action describing
the exact `workiq_send_email` call for the skill to execute — no per-step skill
instructions required.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Done, Blocked
from orchestrator.phase_config import load_phase_config
from steps.lib import templating as T
from steps.lib.context import release_ctx, resolve_recipients

ID = "notice"
KIND = "scout"

# Fixed external link inside the notice body (see EXTERNAL-REFERENCES.md).
HOTFIX_GUIDE_URL = ("https://eng.ms/docs/microsoft-security/identity/"
                    "entra-developer-application-platform/auth-client/"
                    "microsoft-authenticator/microsoft-authenticator/release/"
                    "cherry-pick-to-hotfix-guidelines")


def _html(variant: str, ctx: dict) -> str:
    """Email-safe HTML notice: clean hotfix-guide anchor + Outlook-friendly table."""
    date_line = ("<strong>Today</strong>" if variant == "update"
                 else f"<strong>{T.esc(ctx['ccd_long'])}.</strong>")
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#101828;line-height:1.5;max-width:680px;">
  <p>Hi everyone,</p>
  <p>This is a reminder that the Microsoft Android Authenticator app and Broker
     libraries code complete date for the {T.esc(ctx['month'])} release is {date_line}</p>
  <p>Any check-ins made after code complete will require following
     <a href="{HOTFIX_GUIDE_URL}" style="color:#0b5cad;">the hotfix cherry-pick guide</a>
     and EM approval.</p>
  <table role="presentation" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;margin:14px 0;border:1px solid #d0d5dd;">
    <tr style="background:#f2f4f7;">
      <th style="text-align:left;padding:8px 14px;border:1px solid #d0d5dd;font-size:13px;">Month</th>
      <th style="text-align:left;padding:8px 14px;border:1px solid #d0d5dd;font-size:13px;">Code Complete Date</th>
      <th style="text-align:left;padding:8px 14px;border:1px solid #d0d5dd;font-size:13px;">Android Release Owner</th>
    </tr>
    <tr>
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">{T.esc(ctx['month'])}</td>
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">{T.esc(ctx['ccd_date'])}</td>
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">
        <strong>Primary (Release Owner &mdash; covers Broker + Auth App):</strong> @{T.esc(ctx['owner_at'])}</td>
    </tr>
  </table>
  <p>Thank you,</p>
  <p>{T.esc(ctx['owner'])}</p>
</div>"""


def build(state, variant: str | None = None):
    """Resolve the notice into a NeedsSkill(workiq_send_email) action, or Blocked
    if the release has no CCD / the template is missing."""
    if not state.ccd:
        return Blocked("no CCD set for this release")

    cfg = load_phase_config("preflight", "notice")
    variant = variant or cfg.get("variant", "initial")
    tpl_rel = cfg.get("template", "templates/early-code-complete-notice.md")
    parsed = T.load_template(tpl_rel, variant)
    if isinstance(parsed, dict) and "error" in parsed:
        return Blocked(parsed["error"])
    subject_tpl, body_tpl = parsed

    ctx = release_ctx(state)
    subject = T.fill(subject_tpl, ctx)
    body = T.fill(body_tpl, ctx)
    html = _html(variant, ctx)

    recipients, rnote, prefix = resolve_recipients(state, cfg.get("recipients", []))
    subject_out = f"{prefix}{subject}"

    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": recipients,
            "subject": subject_out,
            "body": html,          # HTML body (has the rendered table + hotfix link)
            "isHtml": True,
            # plain-text fallback the skill may use if isHtml is dropped
            "_plain_body": body,
        },
        record_as=ID,
        summary=f"Email the code-complete notice to {len(recipients)} recipient(s) ({rnote})",
        dry_run=state.dry_run,
        note=f"sent to {', '.join(recipients) if recipients else '(no recipients)'}",
    )
