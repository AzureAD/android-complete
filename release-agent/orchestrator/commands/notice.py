"""Early code-complete notice (Phase-0 step `notice`, S0) + generic step recorder.

Sending email needs the WorkIQ MCP (a skill-layer capability the deterministic
engine can't reach), so `notice` is a scout-assisted step:

  1. `prepare-notice` (here, deterministic) fills the local template with the
     release's CCD/owner and resolves recipients — DRY-RUN redirects every mail to
     the release owner; a LIVE release uses the real recipients from preflight.yaml.
     It prints {subject, body, recipients, dry_run, ...} for the skill to send.
  2. the skill sends it via workiq_send_email, then calls `record-step` to mark
     the step done (or attention on failure).

`record-step` is a generic recorder for ANY scout-assisted phase step.
"""
from __future__ import annotations
import json as _json
import os

from orchestrator import cli_common as C
from orchestrator import schedule

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Fixed external link used inside the notice body (see EXTERNAL-REFERENCES.md).
HOTFIX_GUIDE_URL = ("https://eng.ms/docs/microsoft-security/identity/"
                    "entra-developer-application-platform/auth-client/"
                    "microsoft-authenticator/microsoft-authenticator/release/"
                    "cherry-pick-to-hotfix-guidelines")


def _load_notice_cfg() -> dict:
    from orchestrator.phase_config import load_phase_config
    return load_phase_config("preflight", "notice")


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _parse_template(text: str, variant: str):
    """Pull the (subject, body) for a variant from the delimited template file.
    Sections are marked '===INITIAL:SUBJECT===' / '===INITIAL:BODY===' etc."""
    key = variant.upper()
    marks = {"subject": f"==={key}:SUBJECT===", "body": f"==={key}:BODY==="}
    out = {}
    for field, mark in marks.items():
        if mark not in text:
            return None
        after = text.split(mark, 1)[1]
        # body runs until the next '===...===' marker or EOF
        end = after.find("\n===")
        out[field] = (after[:end] if end != -1 else after).strip("\n")
    return out["subject"].strip(), out["body"]


def _fill(s: str, ctx: dict) -> str:
    for k, v in ctx.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _notice_html(variant: str, ctx: dict) -> str:
    """Email-safe HTML notice: clean anchor for the hotfix guide + a real table
    (inline styles, Outlook-friendly). Same content as the markdown body."""
    date_line = ("**Today**" if variant == "update"
                 else f"<strong>{_esc(ctx['ccd_long'])}.</strong>")
    if variant == "update":
        date_line = "<strong>Today</strong>"
    owner = _esc(ctx["owner"])
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#101828;line-height:1.5;max-width:680px;">
  <p>Hi everyone,</p>
  <p>This is a reminder that the Microsoft Android Authenticator app and Broker
     libraries code complete date for the {_esc(ctx['month'])} release is {date_line}</p>
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
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">{_esc(ctx['month'])}</td>
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">{_esc(ctx['ccd_date'])}</td>
      <td style="padding:8px 14px;border:1px solid #d0d5dd;">
        <strong>Primary (Release Owner &mdash; covers Broker + Auth App):</strong> @{_esc(ctx['owner_at'])}</td>
    </tr>
  </table>
  <p>Thank you,</p>
  <p>{owner}</p>
</div>"""


def cmd_prepare_notice(args):
    st = C.load_state(args.runs_root, args.release)
    if not st.ccd:
        print(_json.dumps({"error": "no CCD set for this release"}))
        return 1
    cfg = _load_notice_cfg()
    variant = getattr(args, "variant", None) or cfg.get("variant", "initial")
    tpl_path = os.path.join(_ROOT, cfg.get("template", "templates/early-code-complete-notice.md"))
    try:
        with open(tpl_path, "r", encoding="utf-8") as fh:
            parsed = _parse_template(fh.read(), variant)
    except OSError:
        print(_json.dumps({"error": f"template not found: {tpl_path}"}))
        return 1
    if not parsed:
        print(_json.dumps({"error": f"variant '{variant}' not in template"}))
        return 1
    subject_tpl, body_tpl = parsed

    ccd = schedule.parse_date(st.ccd)
    owner_email = st.owner_email or ""
    ctx = {
        "month": ccd.strftime("%B"),
        "ccd_long": f"{ccd.strftime('%A, %B')} {_ordinal(ccd.day)}, {ccd.year}",
        "ccd_date": ccd.strftime("%m/%d/%Y"),
        "owner": st.owner_name or owner_email or "the release owner",
        "owner_at": (owner_email.split("@")[0] if owner_email else "release-owner"),
    }
    subject = _fill(subject_tpl, ctx)
    body = _fill(body_tpl, ctx)
    html = _notice_html(variant, ctx)

    # RECIPIENTS: dry-run → owner only (safe); live → configured real list.
    if st.dry_run:
        recipients = [owner_email] if owner_email else []
        note = "dry-run: redirected to release owner"
    else:
        recipients = list(cfg.get("recipients", []))
        note = "live recipients"
    subject_out = (f"[DRY-RUN → owner] {subject}" if st.dry_run else subject)

    print(_json.dumps({
        "step": "notice", "release": args.release, "dry_run": st.dry_run,
        "subject": subject_out, "body": body, "html": html, "recipients": recipients,
        "recipients_note": note,
    }))
    return 0


def _reminder_ctx(st):
    """Shared date/owner context for the Teams reminders."""
    ccd = schedule.parse_date(st.ccd)
    owner_email = st.owner_email or ""
    ccd7 = schedule.anchor_date(ccd, "CCD-7")
    return {
        "month": ccd.strftime("%B"),
        "ccd_long": f"{ccd.strftime('%A, %B')} {_ordinal(ccd.day)}, {ccd.year}",
        "ccd_date": ccd.strftime("%m/%d/%Y"),
        "ccd7_date": ccd7.strftime("%m/%d/%Y"),
        "owner": st.owner_name or owner_email or "the release owner",
    }, owner_email


def _reminder_cfg(section: str) -> dict:
    from orchestrator.phase_config import load_phase_config
    return load_phase_config("preflight", section)


def _reminder_payload(step, args, st, cfg, html, owner_email):
    """Resolve the Teams target: DRY-RUN → owner's own chat; LIVE → group chat."""
    if st.dry_run:
        html = ("<p><i>[DRY-RUN → owner] this would go to the "
                f"{_esc(cfg.get('live_chat_name', 'Android Core Team'))} group chat.</i></p>" + html)
        return {"step": step, "release": args.release, "dry_run": True,
                "content": html, "content_type": "html",
                "send_to": "owner", "owner_email": owner_email, "chat_id": None,
                "target_note": "dry-run: send to the release owner's own Teams chat"}
    return {"step": step, "release": args.release, "dry_run": False,
            "content": html, "content_type": "html",
            "send_to": "group", "owner_email": owner_email,
            "chat_id": cfg.get("live_chat_id"),
            "target_note": cfg.get("live_chat_name", "Android Core Team")}


def _flight_reminder_html(ctx: dict, links: dict) -> str:
    """Teams-friendly HTML for the combined feature-owner reminders."""
    vg = links.get("variable_group", "#")
    pm = links.get("premortem_example", "#")
    loc = links.get("localization", "#")
    ecs = links.get("ecs_flight_history", "#")
    return f"""\
<p><b>Flight &amp; String Reminders — {_esc(ctx['month'])} Release</b></p>
<p>Hi Android Core Team, four reminders as we approach code complete (<b>{_esc(ctx['ccd_long'])}</b>):</p>
<ol>
  <li><b>[Broker] Update local flights.</b> Feature owners — please update your local flights in the <code>release</code> variable group. The release engineer will <b>not</b> update these directly; verify your values are current before we proceed. (<a href="{vg}">Variable group</a>)</li>
  <li><b>[Broker &amp; Auth App] Flight pre-mortem docs.</b> It is each Feature Owner's <b>sole responsibility</b> to ensure every flight shipping with a default value of <code>true</code> has a flight pre-mortem doc — explaining the change's purpose and how we plan to monitor it after the release hits PROD. (<a href="{pm}">Example pre-mortem doc</a>)</li>
  <li><b>[Auth App] Merge user-facing strings by today (CCD−7).</b> It is each Feature Owner's <b>sole responsibility</b> to ensure every Authenticator PR with a new or modified user-facing string is <b>merged by today ({_esc(ctx['ccd7_date'])})</b>. Strings landing inside the 1-week window are <b>not guaranteed</b> to be localized — call out any late strings and escalate to the team lead if not resolved by end of day. (<a href="{loc}">Localization Instructions</a>)</li>
  <li><b>[Auth App] Feature-flag freeze &amp; default-OFF review.</b> Review all features added since the last release and ensure they are <b>default OFF</b>; any feature rolling out <b>default ON</b> requires explicit <b>Team Lead / Engineering Manager approval documented in the release wiki</b>, and the release should be <b>blocked</b> if such approval is missing. <b>No feature work is allowed after Code Complete (CCD)</b> — all feature-flag changes must land before CCD, and any flag changes after CCD require the same cherry-pick approval process as code changes. Review <code>EcsFlight.kt</code> history since the last Code Complete to verify compliance. (<a href="{ecs}">EcsFlight.kt history</a>)</li>
</ol>
<p>Thanks,<br>{_esc(ctx['owner'])}</p>"""


def cmd_prepare_flight_reminder(args):
    """Build the combined 3-in-1 flight & string reminder Teams message and resolve
    its target. DRY-RUN → the release owner's own Teams chat; LIVE → the Android
    Core Team group chat. Prints JSON for the skill to send via workiq_send_chat_message."""
    st = C.load_state(args.runs_root, args.release)
    if not st.ccd:
        print(_json.dumps({"error": "no CCD set for this release"}))
        return 1
    cfg = _reminder_cfg("flight_reminder")
    ctx, owner_email = _reminder_ctx(st)
    html = _flight_reminder_html(ctx, cfg.get("links", {}) or {})
    print(_json.dumps(_reminder_payload("flight_reminder", args, st, cfg, html, owner_email)))
    return 0


def cmd_record_step(args):
    """Generic recorder for a scout-assisted phase step (skill calls this after
    doing the out-of-engine work, e.g. sending the notice email)."""
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    act = orch.record_scout_step(args.phase, args.step, args.status, args.detail or "")
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[{'ok' if args.status == 'pass' else 'attention'}] {args.step}: {act.message}",
           kind="step")
    return 0


def register(sub):
    pn = sub.add_parser("prepare-notice",
                        help="Fill the early code-complete notice template and resolve recipients (JSON)")
    pn.add_argument("--release", required=True)
    pn.add_argument("--variant", default=None, help="initial (default) | update")
    pn.set_defaults(func=cmd_prepare_notice)

    pf = sub.add_parser("prepare-flight-reminder",
                        help="Build the combined flight & string reminder Teams message + target (JSON)")
    pf.add_argument("--release", required=True)
    pf.set_defaults(func=cmd_prepare_flight_reminder)

    rs = sub.add_parser("record-step",
                        help="Record a scout-assisted phase step result (pass|attention)")
    rs.add_argument("--release", required=True)
    rs.add_argument("--phase", default="preflight")
    rs.add_argument("--step", required=True)
    rs.add_argument("--status", required=True, choices=["pass", "attention"])
    rs.add_argument("--detail", default="")
    rs.add_argument("--as-of", default=None)
    rs.set_defaults(func=cmd_record_step)
