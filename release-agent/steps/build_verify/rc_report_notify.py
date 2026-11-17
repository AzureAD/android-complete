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
NAME = 'Post RC verification report summary to Scout bot'
REQUIRED_TOOLS = ('scout_teams_relay',)
REQUIRES_APPROVAL = False
AUTOMATION = 'build-verify-rc-poller'
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

KNOWLEDGE = {'summary': 'Send the RC verification summary and pipeline links to the release owner through '
            'the Scout bot.',
 'what': 'This step sends a concise Scout-bot message to the release owner with the '
         'consolidated RC verification summary (MRWP ECS, MRWP Local, and Authenticator ECS) '
         'plus direct pipeline run links, directly after auth_ecs completes.\n',
 'who': 'Scout sends it to the release owner recorded in state.owner_email using the Scout bot '
        'transport. It is a personal owner notification, not a broad team post.\n',
 'where': ["The release owner's Scout bot chat."],
 'how': 'Run step-action --phase build_verify --step rc_report_notify, send the returned '
        'm_send_teams_message payload, then record completion with record-step --status '
        'pass.\n',
 'faqs': [{'q': 'Why is the Scout-bot message short?',
           'a': 'The bot message gives the high-level gate status and direct pipeline run '
                'links so the chat stays readable.'},
          {'q': 'Who receives it?', 'a': 'The release owner on record, via the Scout bot.'},
          {'q': 'Does posting the summary approve the RC quality gate?',
           'a': 'No. It only confirms delivery of the RC summary. The next step, '
                'build_verify.rc_report_gate, remains a separate owner decision.'}]}
