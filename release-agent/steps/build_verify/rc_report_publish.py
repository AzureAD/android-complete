"""Publish captured RC evidence as immutable, content-addressed SharePoint HTML.

The checked command binds raw source matches/digest and HTML SHA-256 in its plan,
then persists those with the validated provider receipt in this step's data.
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import StepContext
from steps.build_verify import rc_report as R
from tools.coordinates import coords

ID = "rc_report_publish"
KIND = "scout"
WRITE_COMMAND = "publish-rc-report"


def target_config() -> dict:
    return coords.sharepoint("rc_reports")


def report_file_name(context: StepContext, model: dict | None = None, *,
                     content_sha256: str | None = None) -> str:
    if content_sha256 is None:
        _, html, _, model = R.rc_email(context)
        content_sha256 = R.report_lineage(context, html)["content_sha256"]
    rc = (model or {}).get("rc")
    suffix = f"-rc{rc}" if rc else ""
    return f"rc-verification-report-{context.release.release_id}{suffix}-{content_sha256}.html"


def publication_data(receipt: dict, lineage: dict, file_name: str) -> dict:
    """Bind gate approval to current source and confirmed delivery of this report."""
    return {
        **receipt, **lineage, "file_name": file_name,
        "gate_preconditions": lineage["source_matches"] + [
            {"path": ["steps", "build_verify.rc_report_notify", "status"], "value": "done"},
            {"path": ["steps", "build_verify.rc_report_notify", "data", "report_lineage"],
             "value": lineage},
        ],
    }


def build(context: StepContext):
    try:
        subject, html, plain, model = R.rc_email(context)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return Blocked(f"rc_report_publish: could not build the RC report ({exc}).")
    target = target_config()
    lineage = R.report_lineage(context, html)
    file_name = report_file_name(context, model, content_sha256=lineage["content_sha256"])
    summary = (
        f"Publish RC verification report '{file_name}' to "
        f"{target['site_url'].rstrip('/')}/{target['folder'].strip('/')}."
    )
    return NeedsSkill(
        tool=WRITE_COMMAND,
        payload={
            "release": context.release.release_id,
            "file_name": file_name,
            "site_url": target["site_url"],
            "library": target["library"],
            "folder": target["folder"],
            "subject": subject,
            "html": html,
            "source_digest": lineage["source_digest"],
            "content_sha256": lineage["content_sha256"],
            "_plain_body": plain,
            "followup_command": (
                f"{WRITE_COMMAND} --release {context.release.release_id} "
                "--execute --auto-approve --executor rc-report-publish-automation"
            ),
        },
        record_as=ID,
        summary=summary,
        note=summary,
        outbound=True,
    )
