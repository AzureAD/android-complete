"""Step: publish the RC verification report HTML to the release SharePoint site."""
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


def report_file_name(context: StepContext, model: dict | None = None) -> str:
    rc = (model or {}).get("rc")
    suffix = f"-rc{rc}" if rc else ""
    return f"rc-verification-report-{context.release.release_id}{suffix}.html"


def build(context: StepContext):
    try:
        subject, html, plain, model = R.rc_email(context)
    except Exception as exc:  # noqa: BLE001 - surfaced as a release hold
        return Blocked(f"rc_report_publish: could not build the RC report ({exc}).")
    target = target_config()
    file_name = report_file_name(context, model)
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
