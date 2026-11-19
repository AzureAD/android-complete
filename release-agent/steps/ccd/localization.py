"""Step: `localization` — trigger the OneLoc localization pipeline on CCD, then monitor its PR
through merge or the 6:00 PM PT omission cutoff (Phase 1, P1-2).

Lifecycle (state-driven — runs identically from the 7:00 AM morning drain or any
interactive `/release-agent` session):

  1. TRIGGER  — `build()` routes to `launch-localization`: review the exact source,
                variables and provider target, then reserve/execute that checked plan.
                Only a matching provider receipt records build id + queue time.
                `record-localization-run` is recovery-only, never trigger authorization.
  2. POLL     — whenever reconciled (`check-localization`), reads the run/PR status and
                applies `decide()`:
                  * still running & within `timeout_hours` (3h) → wait, poll again.
                  * still running past 3h → EMAIL the release engineer to check it /
                    do the manual steps (localization doc), and hold the step.
                  * succeeded with a complete OneLocBuild@3 task log → verify PR
                    creation was enabled, then read for
                    `Pull request created with ID '<n>'`:
                      - PR id found → POST that PR to the Code reviews chat, then
                        keep polling its ADO status until it is merged.
                      - no PR      → POST "no strings to localize" to Code reviews,
                        then mark done.
                  * failed/canceled → block with the exact run link for owner review.
                  * unknown result or missing/incomplete/unrecognized logs → wait for
                    evidence, escalating to the owner after the same 3h timeout.
                  * active PR at/after 16:00 Los Angeles time → POST one Code
                    reviews warning that translated strings are at risk.
                  * still unmerged at 18:00 Los Angeles time → mark localization
                    skipped/omitted so the scheduled release proceeds to Phase 2.
                  * merged PR and positively succeeded run → done (with the PR link).

All the decision logic here is PURE (no IO) so it's fully testable; the IO (run the
pipeline, read status/logs, send the email, post the chat) is done by the skill /
poller via the NeedsSkill/decision payloads this module returns.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

import re
from dataclasses import dataclass
from html import escape
from datetime import date, datetime, time, timezone

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.schedule import get_tz
from orchestrator import schedule
from tools.coordinates import coords

ID = "localization"
KIND = "scout"
NAME = 'Trigger localization pipeline (OneLoc)'
REQUIRED_TOOLS = ('az',)
REQUIRES_APPROVAL = False
REPEATABLE = True
POLLABLE = True
AUTOMATION = "ccd-localization-watcher"
WRITE_COMMAND = "launch-localization"

# Coordinates (org/project/pipeline id, repo urls, the Code-reviews chat) come from
# config/coordinates.yaml. The OneLoc pipeline + the auth repo it opens PRs against.
_LOC = coords.pipeline("localization")
_AUTH_REPO = coords.repo("authenticator")
_CODE_REVIEWS = coords.team("code_reviews")

# Step config (co-located).
CONFIG = {
    "org": _LOC["org"],
    "project": _LOC["project"],
    "pipeline_id": _LOC["def"],
    "variables": {"isCreatePrSelected": "true"},
    "poll_interval_min": 60,                  # advisory poll cadence while in-flight
    "timeout_hours": 3,                       # escalate to the engineer if not done by then
    "merge_deadline_local": "16:00",
    "merge_deadline_timezone": "America/Los_Angeles",
    "merge_deadline_label": "4:00 PM Los Angeles time",
    "omission_deadline_local": "18:00",
    "omission_deadline_label": "6:00 PM Los Angeles time",
    "oneloc_task": "OneLocBuild@3",           # the task whose log carries the PR id
    # The OneLoc task logs e.g.
    #   Pull request created with ID '16790317': https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/16790317
    "pr_id_pattern": r"Pull request created with ID '(\d+)'",
    # Capture the id AND (optionally) the full PR URL the log prints after it.
    "pr_line_pattern": r"Pull request created with ID '(\d+)'(?::\s*(https?://\S+))?",
    "pr_url_template": (f"{_AUTH_REPO['org']}/DefaultCollection/{_AUTH_REPO['project']}"
                        f"/_git/{_AUTH_REPO['name']}/pullrequest/{{id}}"),
    # How to READ the run from msazure/One. The ADO MCP is bound to
    # identitydivision/Engineering and canNOT reach msazure/One (TF200016), so the
    # poller uses these az CLI reads (verified working as the signed-in user, no 401).
    # {build_id} / {log_id} are filled in by the runner; {org}/{project}/{oneloc_task}
    # come from this CONFIG.
    "az_read": {
        "status": ("az pipelines build show --id {build_id} --org {org} "
                   "--project {project} --query \"{{status:status,result:result}}\" -o json"),
        "log_id": ("az devops invoke --org {org} --area build --resource timeline "
                   "--route-parameters project={project} buildId={build_id} "
                   "--api-version 7.1 --query \"records[?name=='{oneloc_task}'].log.id | [0]\" -o tsv"),
        "log": ("az devops invoke --org {org} --area build --resource logs "
                "--route-parameters project={project} buildId={build_id} logId={log_id} "
                "--api-version 7.1"),
        "pr_status": ("az repos pr show --id {pr_id} --org {org} "
                      "--query \"{{status:status,mergeStatus:mergeStatus}}\" -o json"),
    },
    # Post the resulting PR and any deadline escalation to the same "Code reviews" chat.
    "code_reviews_chat_id": _CODE_REVIEWS["chat"],
    "code_reviews_chat_name": _CODE_REVIEWS["name"],
    "localization_doc": "https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/combined-release-checklist/localization",
    "links": {
        "pipeline": f"https://dev.azure.com/msazure/{_LOC['project']}/_build?definitionId={_LOC['def']}",
        "repo_prs": f"{_AUTH_REPO['org']}/{_AUTH_REPO['project']}/_git/{_AUTH_REPO['name']}/pullrequests",
    },
}

# Mock knobs (mocks.local.yaml). `send_to` redirects localization follow-up
# posts to your own chat ('me'). `send_to` is applied by check-localization
# (the post happens in the poll decider, not build()).
from steps.lib.context import SELF_CHAT_ID as _SELF_CHAT_ID   # noqa: E402

MOCKABLE = {
    "send_to": {
        "kind": "post", "sets": "chatId", "aliases": {"me": _SELF_CHAT_ID, "self": _SELF_CHAT_ID},
        "desc": "Redirect localization PR posts to this chat ('me' = your own chat). "
                "Applied by check-localization.",
    },
}


# ----------------------------- pure helpers (testable) -----------------------------

def elapsed_minutes(started_iso: str, now) -> "int | None":
    if not isinstance(started_iso, str) or not started_iso:
        return None
    from orchestrator import schedule
    start = schedule.parse_iso_utc(started_iso)
    if start is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return int((now - start).total_seconds() // 60)


def poll_status(is_complete: bool, started_iso: str, now,
                timeout_hours: float = 3) -> str:
    """'complete' | 'timeout' | 'wait' — the poll decision from run state + elapsed."""
    if is_complete:
        return "complete"
    mins = elapsed_minutes(started_iso, now)
    if mins is not None and mins >= timeout_hours * 60:
        return "timeout"
    return "wait"


def extract_pr(logs: str, cfg: dict = None) -> tuple:
    """Return (pr_id, pr_url) from the OneLocBuild@3 log. The log prints the full PR
    URL after the id; prefer it, else build one from the id. (None, None) if no PR."""
    cfg = cfg or CONFIG
    m = re.search(cfg.get("pr_line_pattern", cfg["pr_id_pattern"]), logs or "")
    if not m:
        return None, None
    pr_id = m.group(1)
    url = (m.group(2) if m.lastindex and m.lastindex >= 2 else None) or pr_url(pr_id, cfg)
    return pr_id, url


def extract_create_pr_enabled(logs: str) -> "bool | None":
    """Return the OneLoc task's create-PR flag when the complete task log exposes it."""
    m = re.search(r"/createpr:\s*(true|false)", logs or "", re.IGNORECASE)
    if not m:
        return None
    return m.group(1).lower() == "true"


def pr_url(pr_id: str, cfg: dict = None) -> str:
    cfg = cfg or CONFIG
    return cfg["pr_url_template"].format(id=pr_id)


def merge_deadline(context, cfg: dict = None) -> "datetime | None":
    """The CCD-day localization merge deadline in its configured wall-clock zone."""
    cfg = cfg or CONFIG
    try:
        ccd = date.fromisoformat(context.release.ccd)
        deadline_time = time.fromisoformat(cfg.get("merge_deadline_local", "16:00"))
    except (TypeError, ValueError):
        return None
    zone_name = cfg.get("merge_deadline_timezone", "America/Los_Angeles")
    zone = get_tz(zone_name)
    if zone is None:
        raise ValueError(f"timezone data unavailable for localization deadline: {zone_name}")
    return datetime.combine(ccd, deadline_time, tzinfo=zone)


def merge_deadline_passed(context, now=None, cfg: dict = None) -> bool:
    deadline = merge_deadline(context, cfg)
    if deadline is None:
        return False
    now = now or context.clock.utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= deadline


def omission_deadline_passed(context, now=None, cfg: dict = None) -> bool:
    cfg = cfg or CONFIG
    omission_cfg = dict(cfg)
    omission_cfg["merge_deadline_local"] = cfg.get("omission_deadline_local", "18:00")
    return merge_deadline_passed(context, now, omission_cfg)


# ------------------------------- the poll decider ---------------------------------

def _timeout_email(context, cfg: dict, detail: str) -> dict:
    to = [context.release.owner_email] if context.release.owner_email else []
    subject = f"[Action needed] {context.release.release_id} localization evidence unresolved after {cfg['timeout_hours']}h"
    doc = cfg["localization_doc"]
    pipeline = (_run_link(context, cfg) or {}).get("url", "#")
    body = (
        f"<p>The {context.release.release_id} localization pipeline "
        f"(<a href=\"{escape(pipeline, quote=True)}\">pipeline run</a>) has no verified outcome "
        f"after {cfg['timeout_hours']} hours: {escape(detail)}.</p>"
        f"<p>Please check the run, and if needed follow the manual localization steps: "
        f"<a href=\"{doc}\">Localization instructions</a>.</p>")
    return {"to": to, "subject": subject, "body": body, "isHtml": True}


def _owner_mention(context) -> tuple:
    owner_email = context.release.owner_email or ""
    display = context.release.owner_name or (owner_email.split("@")[0] if owner_email else "") or "release engineer"
    mentions = None
    if owner_email:
        who = f'<at id="0">{escape(display)}</at>'
        mentions = [{
            "id": 0, "mentionText": display,
            "mentioned": {"user": {"id": owner_email, "displayName": display,
                                   "userIdentityType": "aadUser"}},
        }]
    else:
        who = escape(display)
    return who, mentions


def _chat_payload(context, cfg: dict, content: str) -> dict:
    payload = {"chatId": cfg["code_reviews_chat_id"], "contentType": "html",
               "content": content}
    _, mentions = _owner_mention(context)
    if mentions:
        payload["mentions"] = mentions
    return payload


def _review_post(context, cfg: dict, pr_id: str, url: str) -> dict:
    """Initial Code reviews post for a discovered localization PR."""
    who, _ = _owner_mention(context)
    deadline = cfg.get("merge_deadline_label", "4:00 PM Los Angeles time")
    omission = cfg.get("omission_deadline_label", "6:00 PM Los Angeles time")
    content = (
        f"<p><b>Localization PR ready for review — {context.release.release_id}</b></p>"
        f"<p>{who} — the localization pipeline created translations PR "
        f"<a href=\"{url}\">#{pr_id}</a>. Please review and merge it by "
        f"<b>{deadline}</b>. If it is still unmerged at <b>{omission}</b>, "
        f"localization will be omitted and the release will continue without these "
        f"translated strings.</p>")
    return _chat_payload(context, cfg, content)


def _deadline_post(context, cfg: dict, pr_id: str, url: str) -> dict:
    """One Code reviews warning when the localization PR misses the 4 PM target."""
    who, _ = _owner_mention(context)
    content = (
        f"<p><b>Localization PR still unmerged — translated strings at risk</b></p>"
        f"<p><a href=\"{url}\">PR #{pr_id}</a> for {context.release.release_id} was not merged "
        f"by {cfg.get('merge_deadline_label', '4:00 PM Los Angeles time')}. "
        f"The release remains on schedule. {who} — please merge it by "
        f"<b>{cfg.get('omission_deadline_label', '6:00 PM Los Angeles time')}</b>; "
        f"otherwise localization will be omitted and these translated strings will not "
        f"be included in this release.</p>")
    return _chat_payload(context, cfg, content)


def _no_strings_post(context, cfg: dict) -> dict:
    """Code reviews notice for a successful createPR=true run that produced no PR."""
    who, _ = _owner_mention(context)
    run = _run_link(context, cfg) or {}
    run_url = run.get("url", "#")
    content = (
        f"<p><b>No strings to localize — {context.release.release_id}</b></p>"
        f"<p>The OneLoc-1ES localization pipeline completed successfully with PR "
        f"creation enabled, and no localization PR was generated. This means there "
        f"are no strings to localize for this release.</p>"
        f"<p>{who} — no localization PR action is needed. Run evidence: "
        f"<a href=\"{escape(run_url, quote=True)}\">localization pipeline run</a>.</p>")
    return _chat_payload(context, cfg, content)


def _run_link(context, cfg: dict) -> dict | None:
    """A proof link to the triggered pipeline RUN itself — always available once the
    build is recorded, whether or not a PR was created. Prefers the exact run_url
    stored at trigger time; otherwise builds the standard build-results URL from the
    recorded build id + configured org/project."""
    data = context.evidence.step("ccd", "localization").data or {}
    url = data.get("run_url")
    bid = data.get("build_id")
    if not url and bid and cfg.get("org") and cfg.get("project"):
        url = f"{cfg['org'].rstrip('/')}/{cfg['project']}/_build/results?buildId={bid}"
    if not url:
        return None
    label = f"Localization pipeline run{f' (build {bid})' if bid else ''}"
    return {"name": label, "url": url}


@dataclass(frozen=True)
class RunEvidence:
    """Transient poll input, never persisted as new release-state fields.

    Only the documented PR-created line has a verified PR format. A successful
    complete log with /createpr: True and no PR line is the supported no-strings
    path; logs that do not expose the create-PR flag still need owner-reviewed
    no-change proof.
    """
    result: str | None = None
    logs_complete: bool = False
    no_change_confirmation: str | None = None

    def __post_init__(self):
        if self.result is not None and not isinstance(self.result, str):
            raise ValueError("localization run result must be text")
        if type(self.logs_complete) is not bool:
            raise ValueError("localization log completeness must be boolean")
        if self.no_change_confirmation is not None and (
                not isinstance(self.no_change_confirmation, str)
                or not self.no_change_confirmation.strip()):
            raise ValueError("no-change confirmation requires an owner-reviewed explanation")


def _wait_for_pr_status(pr_id, url, links, cfg):
    return {
        "decision": "wait_for_merge", "pr_id": pr_id, "pr_url": url,
        "poll_in_min": cfg.get("poll_interval_min", 60), "links": links,
        "note": f"localization PR #{pr_id}: current merge status is unavailable; "
                "read the exact PR before deciding merge or omission",
    }


def decide(context, is_complete: bool, logs: str = None, now=None, cfg: dict = None,
           pr_status: str = None, *, evidence: RunEvidence = RunEvidence()) -> dict:
    """Pure poll decision. Returns a dict with a `decision` and the payload the poller
    should act on:
      wait          -> {decision, elapsed_min, poll_in_min, note}
      timeout       -> {decision, email:{...}, note}          (hold the step)
      failed        -> {decision, links, note}               (owner-review block)
      announce_pr   -> {decision, pr_id, pr_url, chat:{...}, links, note}
      wait_for_merge -> {decision, pr_id, pr_url, poll_in_min, note}
      warn_unmerged  -> {decision, chat:{...}, note}
      omit_unmerged  -> {decision, pr_id, pr_url, links, note}    (skipped)
      merged        -> {decision, pr_id, pr_url, links, note}      (done)
      announce_none -> {decision, chat:{...}, links, note}    (post no strings, then done)
      complete_none -> {decision, links, note}                (done, owner-reviewed no strings)

    Both terminal branches ALWAYS carry a proof `links` entry so the
    step's Details box has evidence: the PR link when a PR was created, plus the
    pipeline RUN link in every case (the run is the proof it fired even with no PR).
    """
    cfg = cfg or CONFIG
    if type(is_complete) is not bool:
        raise ValueError("localization completion must be boolean")
    if logs is not None and not isinstance(logs, str):
        raise ValueError("localization logs must be text")
    if pr_status is not None and (
            not isinstance(pr_status, str) or pr_status.strip().lower() not in (
                "active", "completed", "abandoned")):
        raise ValueError("localization PR status must be active, completed or abandoned")
    step = context.evidence.step("ccd", "localization")
    data = step.data or {}
    now = now or context.clock.utc()
    run_link = _run_link(context, cfg)
    run_links = [run_link] if run_link else []
    result = str(evidence.result or "").strip().lower()
    if result in ("failed", "canceled", "cancelled", "partiallysucceeded"):
        return {"decision": "failed", "links": run_links,
                "note": f"localization pipeline result: {result} — release owner must inspect "
                        "the recorded run and explicitly reopen localization before rerunning; "
                        "this is not proof of no strings"}
    started = data.get("started_at")
    stored_pr_id = data.get("pr_id")
    stored_pr_url = data.get("pr_url")

    if stored_pr_id and is_complete and result == "succeeded":
        links = [{"name": f"Localization PR #{stored_pr_id}",
                  "url": stored_pr_url or pr_url(stored_pr_id, cfg)}]
        run_link = _run_link(context, cfg)
        if run_link:
            links.append(run_link)
        normalized = str(pr_status or "").strip().lower()
        if normalized == "completed":
            return {"decision": "merged", "pr_id": stored_pr_id,
                    "pr_url": stored_pr_url, "links": links,
                    "note": f"localization PR #{stored_pr_id} merged"}
        if omission_deadline_passed(context, now, cfg):
            if not normalized:
                return _wait_for_pr_status(stored_pr_id, stored_pr_url, links, cfg)
            return {
                "decision": "omit_unmerged", "pr_id": stored_pr_id,
                "pr_url": stored_pr_url, "links": links,
                "note": f"localization omitted — PR #{stored_pr_id} was not merged by "
                        f"{cfg.get('omission_deadline_label', '6:00 PM Los Angeles time')}; "
                        f"release continues without these translated strings",
            }
        if not data.get("pr_announced_at"):
            return {
                "decision": "announce_pr", "pr_id": stored_pr_id,
                "pr_url": stored_pr_url, "chat": _review_post(
                    context, cfg, stored_pr_id, stored_pr_url),
                "links": links,
                "note": f"localization PR #{stored_pr_id} created; initial Code reviews post due",
            }
        if merge_deadline_passed(context, now, cfg) and not data.get("merge_deadline_alert_at"):
            return {
                "decision": "warn_unmerged", "pr_id": stored_pr_id,
                "pr_url": stored_pr_url, "chat": _deadline_post(
                    context, cfg, stored_pr_id, stored_pr_url),
                "links": links,
                "note": f"localization PR #{stored_pr_id} is still unmerged at "
                        f"{cfg.get('merge_deadline_label', '4:00 PM Los Angeles time')}; "
                        f"translations will be omitted at "
                        f"{cfg.get('omission_deadline_label', '6:00 PM Los Angeles time')}",
            }
        poll_in = cfg.get("poll_interval_min", 60)
        status_note = f" (ADO status: {normalized})" if normalized else ""
        return {
            "decision": "wait_for_merge", "pr_id": stored_pr_id,
            "pr_url": stored_pr_url, "poll_in_min": poll_in, "links": links,
            "note": f"localization PR #{stored_pr_id} is not merged{status_note}; "
                    f"re-check in {poll_in}m",
        }

    proven_run = (is_complete and result == "succeeded" and evidence.logs_complete
                  and bool(logs and logs.strip()))
    pr_id, url = extract_pr(logs, cfg) if proven_run else (None, None)
    create_pr_enabled = extract_create_pr_enabled(logs) if proven_run else None
    if proven_run and not pr_id and create_pr_enabled is False:
        return {
            "decision": "failed",
            "links": run_links,
            "note": "localization pipeline succeeded but PR creation was disabled; "
                    "this is not proof of no strings and requires owner-reviewed rerun",
        }
    if not proven_run or (
        not pr_id and create_pr_enabled is None and not evidence.no_change_confirmation
    ):
        detail = (
            "pipeline still running" if not is_complete else
            "successful run result not confirmed" if result != "succeeded" else
            "complete OneLocBuild@3 task log unavailable" if not evidence.logs_complete or not logs else
            "task output does not prove a PR or no changes; owner review required")
        mins = elapsed_minutes(started, now)
        poll_in = cfg.get("poll_interval_min", 60)
        hrs = cfg.get("timeout_hours", 3)
        if poll_status(False, started, now, hrs) == "timeout":
            return {"decision": "timeout", "email": _timeout_email(context, cfg, detail),
                    "links": run_links,
                    "note": f"localization outcome unresolved after {hrs}h: {detail} — "
                            "notify the release engineer to inspect the run / do the manual "
                            "steps; explicitly reopen before rerunning"}
        return {"decision": "wait", "elapsed_min": mins, "poll_in_min": poll_in,
                "links": run_links,
                "note": f"localization {detail} ({mins}m elapsed); re-check in {poll_in}m"}

    # completed — always include the run link as proof; add the PR link when present.
    if pr_id:
        links = [{"name": f"Localization PR #{pr_id}", "url": url}]
        if run_link:
            links.append(run_link)
        if str(pr_status or "").strip().lower() == "completed":
            return {"decision": "merged", "pr_id": pr_id, "pr_url": url, "links": links,
                    "note": f"localization PR #{pr_id} merged"}
        if omission_deadline_passed(context, now, cfg):
            if pr_status is None:
                return _wait_for_pr_status(pr_id, url, links, cfg)
            return {"decision": "omit_unmerged", "pr_id": pr_id, "pr_url": url, "links": links,
                    "note": "localization omitted — PR discovered after the 6 PM omission deadline"}
        return {"decision": "announce_pr", "pr_id": pr_id, "pr_url": url,
                "chat": _review_post(context, cfg, pr_id, url),
                "links": links,
                "note": f"localization pipeline complete — translations PR #{pr_id} "
                        f"created; monitoring until merged"}
    if create_pr_enabled is True:
        return {"decision": "announce_none",
                "chat": _no_strings_post(context, cfg),
                "links": run_links,
                "note": "localization complete — no strings to localize "
                        "(OneLoc-1ES succeeded with PR creation enabled and produced no PR)"}
    return {"decision": "complete_none",
            "links": [run_link] if run_link else [],
            "note": "localization complete — no new strings this release "
                    "(successful run and complete task output reviewed by owner): "
                    + evidence.no_change_confirmation.strip()}


# --------------------------------- stage 1: trigger --------------------------------

def _links(cfg: dict) -> list:
    lk = cfg.get("links", {}) or {}
    out = []
    if lk.get("pipeline"):
        out.append({"name": "Localization pipeline (405133)", "url": lk["pipeline"]})
    if lk.get("repo_prs"):
        out.append({"name": "Auth App PRs (OneLoc PR lands here)", "url": lk["repo_prs"]})
    return out


def _az_read(cfg: dict, build_id: str = "{build_id}", log_id: str = "{log_id}",
             pr_id: str = "{pr_id}") -> dict:
    """The concrete az read commands (templated) for the poller to read msazure/One."""
    r = cfg.get("az_read", {}) or {}
    fill = {"org": cfg["org"], "project": cfg["project"],
            "oneloc_task": cfg.get("oneloc_task", "OneLocBuild@3"),
            "build_id": build_id, "log_id": log_id, "pr_id": pr_id}
    return {k: v.format(**fill) for k, v in r.items()}


def poll_target(context, cfg: dict = None) -> dict:
    """Return the next read-only ADO query with every persisted identifier exposed."""
    cfg = cfg or CONFIG
    data = context.evidence.step("ccd", "localization").data or {}
    if data.get("pr_id"):
        reads = _az_read(cfg, build_id=str(data["build_id"]), pr_id=str(data["pr_id"]))
        return {
            "decision": "poll_pr", "pr_id": str(data["pr_id"]),
            "build_id": str(data["build_id"]), "pr_url": data.get("pr_url"),
            "az": {"status": reads["pr_status"], "run_status": reads["status"]},
        }
    if data.get("build_id"):
        reads = _az_read(cfg, build_id=str(data["build_id"]))
        return {
            "decision": "poll_pipeline", "build_id": str(data["build_id"]),
            "run_url": data.get("run_url"),
            "az": {k: reads[k] for k in ("status", "log_id", "log")},
        }
    return {"decision": "not_started", "note": "localization has not been triggered yet"}


def build(context: StepContext):
    """Preparation is read-only; only the checked command may trigger a pipeline."""
    step = context.evidence.step("ccd", ID)
    if step.status == "in_flight":
        if not step.data.get("build_id"):
            return Blocked(
                "Localization launch is owned but has no verified build receipt. Do not trigger "
                "again; inspect ADO and use record-localization-run for matching receipt recovery "
                "or resolve the execution explicitly with an owner-reviewed reason.")
        return NeedsSkill(
            tool="check-localization",
            payload={"release": context.release.release_id, "execution_id": (step.execution or {}).get("id")},
            record_as=ID, summary="Poll the owned localization run; do not trigger it again.",
        )
    if step.data.get("build_id") and not (step.status == "pending" and step.invalidated_at):
        return Blocked(
            "localization already has a recorded run — inspect its evidence and use "
            "`reopen --phase ccd --step localization --reason \"<owner-reviewed rerun>\"` "
            "before triggering another pipeline.",
            links=[link] if (link := _run_link(context, CONFIG)) else [])
    if not context.release.ccd:
        return Blocked("no CCD set for this release")
    if schedule.parse_date(context.release.ccd) is None:
        return Blocked("invalid CCD for this release")

    cfg = CONFIG
    if not all(cfg.get(k) for k in ("org", "project", "pipeline_id")):
        return Blocked("localization: incomplete pipeline configuration")

    variables = dict(cfg.get("variables", {}) or {})
    var_str = ", ".join(f"{k}={v}" for k, v in variables.items())

    return NeedsSkill(
        tool=WRITE_COMMAND,
        payload={
            "release": context.release.release_id,
            "followup_command": (
                f"launch-localization --release {context.release.release_id} "
                "--execute --auto-approve --executor localization-automation"),
            "after": (
                "Run launch-localization with --execute --auto-approve. The command computes "
                "the current reviewed plan, checkpoints its hash, queues OneLoc once, verifies "
                "its provider receipt, and marks `ccd.localization` done so `ccd.localization_pr` "
                "can locate the PR and verify its merge status."),
            "links": _links(cfg),
        },
        record_as=ID,
        summary=f"Automatically launch localization pipeline {cfg['pipeline_id']} ({var_str})",
        note="Localization launch is automatic; launch-localization records a checked plan before queuing.",
        outbound=True,
    )


KNOWLEDGE = {
    "summary": "Queue the OneLoc localization pipeline (405133) once and record its build receipt.",
    "what": (
        "On Code Complete Day, queues pipeline 405133 (`OneLoc-1ES` in `msazure/One`) with "
        "`isCreatePrSelected=true`, verifies the ADO build receipt (`build_id`, `started_at`, "
        "`run_url`), and immediately marks this trigger step done. Downstream step "
        "`ccd.localization_pr` then inspects the build, locates any generated translations PR, "
        "and verifies that it is merged (or confirms no PR was required)."),
    "who": (
        "Scout runs the whole flow automatically (trigger + poll + notify/post). The "
        "release engineer steps in for failed runs, unrecognized/no-change output, "
        "the 3-hour timeout email, or to "
        "review/merge the posted translations PR. The final inclusion cutoff is "
        "6:00 PM Los Angeles time."),
    "where": [
        "Pipeline run: https://dev.azure.com/msazure/One/_build?definitionId=405133 (open the OneLocBuild@3 task log).",
        "The PR id appears in that log as: Pull request created with ID '<n>'.",
        "Resulting PR: https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
        "If createPR was enabled and no PR appears in the complete log, Scout posts the no-strings result to Code reviews.",
        "Reads use az (the ADO MCP is bound to identitydivision/Engineering and can't reach msazure/One): az pipelines build show for status; az devops invoke --area build --resource timeline to find the OneLocBuild@3 log id; then --resource logs to read it.",
    ],
    "how": (
        "Automatic. If the timeout email arrives, inspect the recorded pipeline and "
        "explicitly reopen with an owner-reviewed reason before any new reserved trigger, "
        "or follow the manual localization steps in the doc below. Old run evidence is "
        "preserved in previous_runs when the new receipt is recorded. Once the "
        "PR is posted to Code reviews, review and merge it into the release branch. "
        "If the no-strings notice is posted, no PR action is needed and Scout completes the step. "
        "Notifications use prepare/claim/result with persisted progress bindings. "
        "Verified recovery cancels a stale timeout; a delayed receipt "
        "is retained without blocking recovered work. A confirmed merge completes the "
        "step even when the initial post is still unacknowledged."),
    "links": [
        {"name": "Localization instructions (manual steps)",
         "url": "https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/combined-release-checklist/localization"},
        {"name": "Localization pipeline (405133)",
         "url": "https://dev.azure.com/msazure/One/_build?definitionId=405133"},
    ],
    "faqs": [
        {"q": "What happens if the pipeline hangs?",
         "a": "After 3 hours Scout emails the release engineer to check the run or do the manual localization steps (see the doc link)."},
        {"q": "How does Scout find the PR to post?",
         "a": "After confirming the run succeeded, it reads the full OneLocBuild@3 task log (via az devops invoke against msazure/One) for the line \"Pull request created with ID '<n>'\" and uses the PR URL printed there. If the same complete log shows /createpr: True and no PR line, Scout posts a no-strings notice to Code reviews and completes the step."},
        {"q": "When does the localization step finish?",
         "a": "Done requires positive run success plus a confirmed merged PR, a successful complete createPR=true run whose log generated no PR, or owner-reviewed no-change proof when the log does not expose createPR. Missing results/logs or unrecognized output escalate after 3 hours; failed/canceled or createPR=false runs block. A known unmerged PR warns Code reviews at 4 PM Los Angeles and is skipped/omitted at 6 PM. Confirmed merge takes precedence over omission."},
        {"q": "Why not the ADO MCP?",
         "a": "The ADO MCP is bound to identitydivision/Engineering; msazure/One returns TF200016 (project not found). The az CLI reaches msazure/One as the signed-in user, so the poller reads via az."},
    ],
}
