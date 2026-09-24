"""Step: post the RC verification report link to the owner's Scout bot."""
from __future__ import annotations

from orchestrator.delivery import fingerprint
from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import StepContext
from steps.build_verify import rc_report as R

ID = "rc_report_notify"
KIND = "scout"
NOTIFICATION = True


def _report_link(context: StepContext):
    published = context.evidence.step("build_verify", "rc_report_publish")
    data = dict(published.data or {})
    return data.get("report_link") or data.get("web_url"), data


def build(context: StepContext):
    to = context.release.owner_email
    if not to:
        return Blocked("rc_report_notify: no release owner email on record.")
    link, published = _report_link(context)
    if not link:
        return Blocked("rc_report_notify: RC report link is missing; publish the report first.")
    try:
        model = R.rc_report_model(context)
        gate, auth = R.rc_ui_gate(model), R.auth_report_gate(model)
        action = R.rc_next_action(model)
    except Exception as exc:  # noqa: BLE001
        return Blocked(f"rc_report_notify: could not summarize the RC report ({exc}).")
    auth_text = "PASS" if auth.get("verdict") == "clean" else "HOLD"
    mrwp_pct = f" {gate.get('pass_pct')}%" if gate.get("pass_pct") is not None else ""
    web_url = published.get("web_url")
    extra = f"\nFallback file URL: {web_url}" if web_url and web_url != link else ""
    message = (
        f"**Release {context.release.release_id} — RC verification report**\n\n"
        f"Report: {link}{extra}\n\n"
        f"Summary: MRWP UI {gate.get('verdict')}{mrwp_pct} · Authenticator ECS {auth_text}\n\n"
        f"Next: {action}"
    )
    completion = {
        "kind": "step",
        "record_as": ID,
        "status": "pass",
        "note": f"RC verification report link posted to Scout bot for {to}: {link}",
        "links": [{"name": "RC verification report", "url": link}],
    }
    return NeedsSkill(
        tool="m_send_teams_message",
        payload={"message": message},
        record_as=ID,
        summary=f"Post the RC verification report link to the owner's Scout bot ({to})",
        note=completion["note"],
        outbound=True,
        notification={
            "checkpoint": published.get("item_id") or published.get("web_url") or link,
            "state_matches": [{
                "path": ["steps", "build_verify.rc_report_publish", "data"],
                "hash": fingerprint(published),
            }],
            "completion": completion,
        },
    )
