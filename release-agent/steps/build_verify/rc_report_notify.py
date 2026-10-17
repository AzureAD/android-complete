"""Post only a completed publication matching current raw source and rendered HTML.

Preparation/claim/finalization bind source plus the full publication receipt.
Confirmed delivery stores report_lineage and gate_preconditions in this step's
data; quality recommendations never auto-approve or veto the human gate.
"""
from __future__ import annotations

from orchestrator.delivery import fingerprint
from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import StepContext
from steps.build_verify import rc_report as R, _rc_report_rendering as H

ID = "rc_report_notify"
KIND = "scout"
NOTIFICATION = True


def _report_link(context: StepContext):
    published = context.evidence.step("build_verify", "rc_report_publish")
    data = dict(published.data or {})
    if published.status != "done":
        raise ValueError("RC report publication is not complete; publish the current report first.")
    _, html, _, model = R.rc_email(context)
    lineage = R.report_lineage(context, html)
    if any(data.get(key) != value for key, value in lineage.items()):
        raise ValueError("Published report source/content is stale or missing; publish the current report first.")
    if (not all(isinstance(data.get(key), str) and data[key].strip()
                for key in ("drive_id", "item_id", "web_url", "report_link"))
            or type(data.get("size")) is not int
            or data["size"] != len(html.encode("utf-8"))):
        raise ValueError("Published report receipt is incomplete; recover publication before notification.")
    return data["report_link"], data, model, lineage


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
        link, published, model, lineage = _report_link(context)
        auth = R.auth_report_gate(model)
        ecs, local = (H.mrwp_leg_summary(model, provider) for provider in ("ECS", "Local"))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Blocked(f"rc_report_notify: could not summarize the RC report ({exc}).")
    matches = lineage["source_matches"] + [{
        "path": ["steps", "build_verify.rc_report_publish", "data"],
        "hash": fingerprint(published),
    }, {
        "path": ["steps", "build_verify.rc_report_publish", "status"], "value": "done",
    }]
    message = (
        f"**Release {context.release.release_id} - RC verification report**\n\n"
        f"Report: {link}\n\n"
        f"Summary: MRWP ECS: {ecs['headline']}; MRWP Local: {local['headline']}; "
        f"Authenticator ECS: {_auth_summary(model, auth)}.\n\n"
        "Open the report for failure details and next steps."
    )
    completion = {
        "kind": "step",
        "record_as": ID,
        "status": "pass",
        "note": f"RC verification report link posted to Scout bot for {to}: {link}",
        "links": [{"name": "RC verification report", "url": link}],
        "data": {"report_lineage": lineage, "gate_preconditions": matches},
    }
    return NeedsSkill(
        tool="m_send_teams_message",
        payload={"message": message},
        record_as=ID,
        summary=f"Post the RC verification report link to the owner's Scout bot ({to})",
        note=completion["note"],
        outbound=True,
        notification={
            "checkpoint": fingerprint({"lineage": lineage, "published": published}),
            "state_matches": matches,
            "completion": completion,
        },
    )
