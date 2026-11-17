"""Post the RC verification summary and pipeline links directly from captured evidence.

Composes the RC summary directly from captured Phase-2 pipeline evidence without any
SharePoint upload step. Confirmed delivery stores report_lineage and gate_preconditions
in this step's data; quality recommendations never auto-approve or veto the human gate.
"""
from __future__ import annotations

from orchestrator.delivery import fingerprint
from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import StepContext
from steps.build_verify import rc_report as R, _rc_report_rendering as H

ID = "rc_report_notify"
KIND = "scout"
NOTIFICATION = True


def _auth_summary(model: dict, auth: dict) -> str:
    summary = H.auth_leg_summary(model, auth)
    label = "pass" if auth.get("verdict") == "clean" else "hold"
    worst = summary["worst"]
    detail = f"{worst[1]:.1f}% lowest-suite pass" if worst and worst[1] is not None else summary["headline"]
    return f"{label} ({detail})"


def build(context: StepContext):
    to = context.release.owner_email
    if not to:
        return Blocked("rc_report_notify: no release owner email on record.")
    try:
        _, html, _, model = R.rc_email(context)
        lineage = R.report_lineage(context, html)
        auth = R.auth_report_gate(model)
        ecs, local = (H.mrwp_leg_summary(model, provider) for provider in ("ECS", "Local"))
        links = R.rc_run_links(model)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Blocked(f"rc_report_notify: could not summarize the RC report ({exc}).")
    matches = list(lineage["source_matches"])
    runs_text = "\n".join(f"- [{item['name']}]({item['url']})" for item in links)
    message = (
        f"**Release {context.release.release_id} - RC verification summary**\n\n"
        f"Summary: MRWP ECS: {ecs['headline']}; MRWP Local: {local['headline']}; "
        f"Authenticator ECS: {_auth_summary(model, auth)}.\n\n"
        + (f"Pipeline runs:\n{runs_text}\n\n" if runs_text else "")
        + "Review the results and decide `rc_report_gate` (`approve` or `deny`)."
    )
    completion = {
        "kind": "step",
        "record_as": ID,
        "status": "pass",
        "note": f"RC verification summary posted to Scout bot for {to}",
        "links": links,
        "data": {"report_lineage": lineage, "gate_preconditions": matches},
    }
    return NeedsSkill(
        tool="m_send_teams_message",
        payload={"message": message},
        record_as=ID,
        summary=f"Post the RC verification report summary to the owner's Scout bot ({to})",
        note=completion["note"],
        outbound=True,
        notification={
            "checkpoint": fingerprint({"lineage": lineage}),
            "state_matches": matches,
            "completion": completion,
        },
    )
