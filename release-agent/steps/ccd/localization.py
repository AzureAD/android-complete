"""Step: `localization` — trigger the loc pipeline at noon, then monitor its PR
through merge (Phase 1, P1-2).

Lifecycle (a small state machine — the engine can't wait/poll, so it's driven by a
per-release poller automation + this deterministic decider):

  1. TRIGGER  — `build()` returns a NeedsSkill to run pipeline 405133 with
                isCreatePrSelected=true. The runner records the queued build via
                `record-localization-run` (stores build id + start time), leaving the
                step IN-FLIGHT (not done).
  2. POLL     — every `poll_interval_min` (default 60) a poller calls
                `check-localization`, which reads the run status and applies
                `decide()`:
                  * still running & within `timeout_hours` (3h) → wait, poll again.
                  * still running past 3h → EMAIL the release engineer to check it /
                    do the manual steps (localization doc), and hold the step.
                  * completed → read the OneLocBuild@3 task log for
                    `Pull request created with ID '<n>'`:
                      - PR id found → POST that PR to the Code reviews chat, then
                        keep polling its ADO status until it is merged.
                      - no PR      → no new strings this release; mark done.
                  * active PR at/after 16:00 Los Angeles time → POST one Code
                    reviews warning that translated strings are at risk.
                  * still unmerged at 18:00 Los Angeles time → mark localization
                    skipped/omitted so the scheduled release proceeds to Phase 2.
                  * merged PR → mark the step done (with the PR link).

All the decision logic here is PURE (no IO) so it's fully testable; the IO (run the
pipeline, read status/logs, send the email, post the chat) is done by the skill /
poller via the NeedsSkill/decision payloads this module returns.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.schedule import get_tz
from tools.coordinates import coords

ID = "localization"
KIND = "scout"

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
    "fire_at_local": "12:00",                 # noon on CCD (trigger; automation-driven)
    "poll_interval_min": 60,                  # re-check the run/PR every N minutes
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

# Mock knobs (mocks.local.yaml). `create_pr` overrides the trigger variable
# (set false to run the pipeline WITHOUT creating a PR); `send_to` redirects the
# PR posts to your own chat ('me'). `send_to` is applied by
# check-localization (the post happens in the poll decider, not build()).
from steps.lib.context import SELF_CHAT_ID as _SELF_CHAT_ID   # noqa: E402

MOCKABLE = {
    "create_pr": {
        "kind": "input",
        "desc": "Override isCreatePrSelected on the trigger (true/false). Set false to "
                "run the pipeline without creating a PR.",
    },
    "send_to": {
        "kind": "post", "sets": "chatId", "aliases": {"me": _SELF_CHAT_ID, "self": _SELF_CHAT_ID},
        "desc": "Redirect localization PR posts to this chat ('me' = your own chat). "
                "Applied by check-localization.",
    },
}


# ----------------------------- pure helpers (testable) -----------------------------

def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now_utc():
    return datetime.now(timezone.utc)


def elapsed_minutes(started_iso: str, now=None) -> "int | None":
    start = _parse_iso(started_iso)
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    now = now or _now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return int((now - start).total_seconds() // 60)


def poll_status(is_complete: bool, started_iso: str, now=None,
                timeout_hours: float = 3) -> str:
    """'complete' | 'timeout' | 'wait' — the poll decision from run state + elapsed."""
    if is_complete:
        return "complete"
    mins = elapsed_minutes(started_iso, now)
    if mins is not None and mins >= timeout_hours * 60:
        return "timeout"
    return "wait"


def extract_pr_id(logs: str, pattern: str = None) -> "str | None":
    """Pull the OneLoc PR id out of the OneLocBuild@3 task log, or None if there is
    no 'Pull request created with ID ...' line (i.e. no new strings)."""
    m = re.search(pattern or CONFIG["pr_id_pattern"], logs or "")
    return m.group(1) if m else None


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


def pr_url(pr_id: str, cfg: dict = None) -> str:
    cfg = cfg or CONFIG
    return cfg["pr_url_template"].format(id=pr_id)


def merge_deadline(state, cfg: dict = None) -> "datetime | None":
    """The CCD-day localization merge deadline in its configured wall-clock zone."""
    cfg = cfg or CONFIG
    try:
        ccd = date.fromisoformat(state.ccd)
        deadline_time = time.fromisoformat(cfg.get("merge_deadline_local", "16:00"))
    except (TypeError, ValueError):
        return None
    zone_name = cfg.get("merge_deadline_timezone", "America/Los_Angeles")
    zone = get_tz(zone_name)
    if zone is None:
        raise ValueError(f"timezone data unavailable for localization deadline: {zone_name}")
    return datetime.combine(ccd, deadline_time, tzinfo=zone)


def merge_deadline_passed(state, now=None, cfg: dict = None) -> bool:
    deadline = merge_deadline(state, cfg)
    if deadline is None:
        return False
    now = now or _now_utc()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= deadline


def omission_deadline_passed(state, now=None, cfg: dict = None) -> bool:
    cfg = cfg or CONFIG
    omission_cfg = dict(cfg)
    omission_cfg["merge_deadline_local"] = cfg.get("omission_deadline_local", "18:00")
    return merge_deadline_passed(state, now, omission_cfg)


# ------------------------------- the poll decider ---------------------------------

def _timeout_email(state, cfg: dict) -> dict:
    to = [state.owner_email] if state.owner_email else []
    subject = f"[Action needed] {state.release_id} localization pipeline still running after {cfg['timeout_hours']}h"
    doc = cfg["localization_doc"]
    pipeline = (cfg.get("links", {}) or {}).get("pipeline", "#")
    body = (
        f"<p>The {state.release_id} localization pipeline "
        f"(<a href=\"{pipeline}\">definition {cfg['pipeline_id']}</a>) has not completed "
        f"after {cfg['timeout_hours']} hours.</p>"
        f"<p>Please check the run, and if needed follow the manual localization steps: "
        f"<a href=\"{doc}\">Localization instructions</a>.</p>")
    return {"to": to, "subject": subject, "body": body, "isHtml": True}


def _owner_mention(state) -> tuple:
    owner_email = state.owner_email or ""
    display = state.owner_name or (owner_email.split("@")[0] if owner_email else "") or "release engineer"
    mentions = None
    if owner_email:
        who = f'<at id="0">{display}</at>'
        mentions = [{
            "id": 0, "mentionText": display,
            "mentioned": {"user": {"id": owner_email, "displayName": display,
                                   "userIdentityType": "aadUser"}},
        }]
    else:
        who = display
    return who, mentions


def _chat_payload(state, cfg: dict, content: str) -> dict:
    payload = {"chatId": cfg["code_reviews_chat_id"], "contentType": "html",
               "content": content}
    _, mentions = _owner_mention(state)
    if mentions:
        payload["mentions"] = mentions
    return payload


def _review_post(state, cfg: dict, pr_id: str, url: str) -> dict:
    """Initial Code reviews post for a discovered localization PR."""
    who, _ = _owner_mention(state)
    deadline = cfg.get("merge_deadline_label", "4:00 PM Los Angeles time")
    omission = cfg.get("omission_deadline_label", "6:00 PM Los Angeles time")
    content = (
        f"<p><b>Localization PR ready for review — {state.release_id}</b></p>"
        f"<p>{who} — the localization pipeline created translations PR "
        f"<a href=\"{url}\">#{pr_id}</a>. Please review and merge it by "
        f"<b>{deadline}</b>. If it is still unmerged at <b>{omission}</b>, "
        f"localization will be omitted and the release will continue without these "
        f"translated strings.</p>")
    return _chat_payload(state, cfg, content)


def _deadline_post(state, cfg: dict, pr_id: str, url: str) -> dict:
    """One Code reviews warning when the localization PR misses the 4 PM target."""
    who, _ = _owner_mention(state)
    content = (
        f"<p><b>Localization PR still unmerged — translated strings at risk</b></p>"
        f"<p><a href=\"{url}\">PR #{pr_id}</a> for {state.release_id} was not merged "
        f"by {cfg.get('merge_deadline_label', '4:00 PM Los Angeles time')}. "
        f"The release remains on schedule. {who} — please merge it by "
        f"<b>{cfg.get('omission_deadline_label', '6:00 PM Los Angeles time')}</b>; "
        f"otherwise localization will be omitted and these translated strings will not "
        f"be included in this release.</p>")
    return _chat_payload(state, cfg, content)


def _run_link(state, cfg: dict) -> dict | None:
    """A proof link to the triggered pipeline RUN itself — always available once the
    build is recorded, whether or not a PR was created. Prefers the exact run_url
    stored at trigger time; otherwise builds the standard build-results URL from the
    recorded build id + configured org/project."""
    data = state.get_step("ccd", "localization").data or {}
    url = data.get("run_url")
    bid = data.get("build_id")
    if not url and bid and cfg.get("org") and cfg.get("project"):
        url = f"{cfg['org'].rstrip('/')}/{cfg['project']}/_build/results?buildId={bid}"
    if not url:
        return None
    label = f"Localization pipeline run{f' (build {bid})' if bid else ''}"
    return {"name": label, "url": url}


def decide(state, is_complete: bool, logs: str = None, now=None, cfg: dict = None,
           pr_status: str = None) -> dict:
    """Pure poll decision. Returns a dict with a `decision` and the payload the poller
    should act on:
      wait          -> {decision, elapsed_min, poll_in_min, note}
      timeout       -> {decision, email:{...}, note}          (hold the step)
      announce_pr   -> {decision, pr_id, pr_url, chat:{...}, followup_command, links, note}
      wait_for_merge -> {decision, pr_id, pr_url, poll_in_min, note}
      warn_unmerged  -> {decision, chat:{...}, followup_command, note}
      omit_unmerged  -> {decision, pr_id, pr_url, links, note}    (skipped)
      merged        -> {decision, pr_id, pr_url, links, note}      (done)
      complete_none -> {decision, links, note}                (done, no strings)

    Both terminal branches ALWAYS carry a proof `links` entry so the
    step's Details box has evidence: the PR link when a PR was created, plus the
    pipeline RUN link in every case (the run is the proof it fired even with no PR).
    """
    cfg = cfg or CONFIG
    step = state.get_step("ccd", "localization")
    data = step.data or {}
    started = data.get("started_at")
    stored_pr_id = data.get("pr_id")
    stored_pr_url = data.get("pr_url")

    if stored_pr_id:
        links = [{"name": f"Localization PR #{stored_pr_id}",
                  "url": stored_pr_url or pr_url(stored_pr_id, cfg)}]
        run_link = _run_link(state, cfg)
        if run_link:
            links.append(run_link)
        normalized = str(pr_status or "").strip().lower()
        if normalized != "completed" and omission_deadline_passed(state, now, cfg):
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
                    state, cfg, stored_pr_id, stored_pr_url),
                "followup_command": (
                    f"record-localization-post --release {state.release_id} "
                    f"--kind initial --pr-id {stored_pr_id}"),
                "links": links,
                "note": f"localization PR #{stored_pr_id} created; initial Code reviews post due",
            }
        if normalized == "completed":
            return {"decision": "merged", "pr_id": stored_pr_id,
                    "pr_url": stored_pr_url, "links": links,
                    "note": f"localization PR #{stored_pr_id} merged"}
        if merge_deadline_passed(state, now, cfg) and not data.get("merge_deadline_alert_at"):
            return {
                "decision": "warn_unmerged", "pr_id": stored_pr_id,
                "pr_url": stored_pr_url, "chat": _deadline_post(
                    state, cfg, stored_pr_id, stored_pr_url),
                "followup_command": (
                    f"record-localization-post --release {state.release_id} "
                    f"--kind deadline --pr-id {stored_pr_id}"),
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

    status = poll_status(is_complete, started, now, cfg.get("timeout_hours", 3))

    if status == "wait":
        mins = elapsed_minutes(started, now)
        poll_in = cfg.get("poll_interval_min", 60)
        return {"decision": "wait", "elapsed_min": mins, "poll_in_min": poll_in,
                "note": f"localization pipeline still running ({mins}m elapsed); "
                        f"re-check in {poll_in}m"}

    if status == "timeout":
        hrs = cfg.get("timeout_hours", 3)
        return {"decision": "timeout", "email": _timeout_email(state, cfg),
                "note": f"localization pipeline did not complete within {hrs}h — "
                        f"emailed the release engineer to check it / do the manual steps"}

    # completed — always include the run link as proof; add the PR link when present.
    run_link = _run_link(state, cfg)
    pr_id, url = extract_pr(logs, cfg)
    if pr_id:
        links = [{"name": f"Localization PR #{pr_id}", "url": url}]
        if run_link:
            links.append(run_link)
        return {"decision": "announce_pr", "pr_id": pr_id, "pr_url": url,
                "chat": _review_post(state, cfg, pr_id, url),
                "followup_command": (
                   f"record-localization-post --release {state.release_id} "
                   f"--kind initial --pr-id {pr_id}"),
                "links": links,
                "note": f"localization pipeline complete — translations PR #{pr_id} "
                        f"created; monitoring until merged"}
    return {"decision": "complete_none",
            "links": [run_link] if run_link else [],
            "note": "localization complete — no new strings this release "
                    "(no OneLoc PR was created)"}


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


def poll_target(state, cfg: dict = None) -> dict:
    """Return the next read-only ADO query with every persisted identifier exposed."""
    cfg = cfg or CONFIG
    data = state.get_step("ccd", "localization").data or {}
    if data.get("pr_id"):
        reads = _az_read(cfg, pr_id=str(data["pr_id"]))
        return {
            "decision": "poll_pr", "pr_id": str(data["pr_id"]),
            "pr_url": data.get("pr_url"), "az": {"status": reads["pr_status"]},
        }
    if data.get("build_id"):
        reads = _az_read(cfg, build_id=str(data["build_id"]))
        return {
            "decision": "poll_pipeline", "build_id": str(data["build_id"]),
            "run_url": data.get("run_url"),
            "az": {k: reads[k] for k in ("status", "log_id", "log")},
        }
    return {"decision": "not_started", "note": "localization has not been triggered yet"}


def build(state):
    """Stage 1 (TRIGGER): NeedsSkill that runs pipeline 405133 with
    isCreatePrSelected=true, then records the queued build via
    `record-localization-run` so the poller can drive it to completion. Blocked if
    the release has no CCD / config is incomplete."""
    if not state.ccd:
        return Blocked("no CCD set for this release")

    cfg = CONFIG
    if not all(cfg.get(k) for k in ("org", "project", "pipeline_id")):
        return Blocked("localization: incomplete pipeline configuration")

    variables = dict(cfg.get("variables", {}) or {})
    # mocks.local.yaml `create_pr` overrides isCreatePrSelected (e.g. false = run
    # the pipeline without creating a PR).
    from steps.lib.mockctx import mock_input, MISSING
    cp = mock_input("create_pr", MISSING)
    if cp is not MISSING:
        variables["isCreatePrSelected"] = "true" if str(cp).lower() in ("true", "1", "yes") else "false"
    mcp_vars = {k: {"value": str(v)} for k, v in variables.items()}
    var_str = ", ".join(f"{k}={v}" for k, v in variables.items())

    return NeedsSkill(
        tool="azure_devops-pipelines_run_pipeline",
        payload={
            "project": cfg["project"],
            "pipelineId": cfg["pipeline_id"],
            "variables": mcp_vars,
            "_trigger": {
                "org": cfg["org"],
                "pipeline_id": cfg["pipeline_id"],
                "variables_flat": variables,
                "oneloc_task": cfg.get("oneloc_task"),
                "az_fallback": (
                    f"az pipelines run --id {cfg['pipeline_id']} "
                    f"--org {cfg['org']} --project {cfg['project']} "
                    f"--variables {var_str}"),
                # The ADO MCP can't reach msazure/One — the poller reads via az.
                "az_read": _az_read(cfg),
                "after": (
                    "Note the queued build id, then run "
                    "`record-localization-run --release <id> --build-id <buildId>` "
                    "(this leaves the step IN-FLIGHT). A poller then runs "
                    "`check-localization` every "
                    f"{cfg.get('poll_interval_min', 60)} min until it completes or "
                    f"times out after {cfg.get('timeout_hours', 3)}h."),
            },
            "links": _links(cfg),
        },
        record_as=ID,
        summary=f"Trigger localization pipeline {cfg['pipeline_id']} ({var_str}), then poll to completion",
        note=f"triggered pipeline {cfg['pipeline_id']} with {var_str}; polling every "
             f"{cfg.get('poll_interval_min', 60)}m (timeout {cfg.get('timeout_hours', 3)}h)",
        outbound=True,
    )


def automation_prompt(release: str, spec: dict) -> str:
    """The bespoke automation instruction for THIS step — owned here (single source of
    truth, like `fire_at_local`) so the generic automations planner doesn't special-case
    the step id. Two shapes: the interval POLLER vs the one-shot noon TRIGGER (the planner
    passes the automation `spec`; `interval` set ⇒ poller)."""
    if spec.get("interval"):
        return (
            f"Release {release} — localization poller (hourly).\n"
            f"If localization for {release} is in-flight (triggered at noon and not "
            f"done/blocked), poll it once. The ADO MCP can't reach msazure/One:\n"
            f"1. run `check-localization --release {release}`. It returns `poll_pipeline` "
            f"or `poll_pr` with the persisted build/PR id and exact read-only az command(s).\n"
            f"2. for `poll_pipeline`, run its az status command. If complete, also run "
            f"its log_id and log commands. Then call `check-localization --release "
            f"{release} --complete <true|false> [--logs \"<OneLocBuild@3 log>\"]`.\n"
            f"3. for `poll_pr`, run its az status command and pass the returned status to "
            f"`check-localization --release {release} --pr-status <status>`.\n"
            f"4. act on the printed decision: `timeout` → send the given email; "
            f"`announce_pr` or `warn_unmerged` → post the given chat message, then "
            f"run its followup_command only after delivery succeeds; "
            f"`wait`/`wait_for_merge`/`omit_unmerged`/`merged`/`complete_none`/`not_started`/"
            f"`already_final` → nothing to send. The command marks the step done only "
            f"for `merged` or `complete_none`, or skipped/omitted at the 6 PM cutoff.\n"
            f"5. silently journal: `journal --release {release} --source scout --kind "
            f"automation --text \"localization-poller: <decision>\"`. Stay silent if "
            f"there is nothing to do.")

    # One-shot (noon) trigger — trigger then hand off to the poller.
    return (
        f"Release {release} — trigger localization.\n"
        f"1. run `step-action --release {release} --phase ccd --step localization`;\n"
        f"2. run the returned needs_skill action to start pipeline 405133 "
        f"(isCreatePrSelected=true); note the queued build id;\n"
        f"3. run `record-localization-run --release {release} --build-id <buildId>` "
        f"— this leaves the step IN-FLIGHT (do NOT record-step done; the poller "
        f"finishes it once the run completes or times out);\n"
        f"4. provision its poller with `automation plan --release {release} --on-demand "
        f"ccd-localization-poller --json`; create exactly the returned automation and "
        f"register every field including cleanup_when;\n"
        f"5. silently journal: `journal --release {release} --source scout --kind "
        f"automation --text \"ccd-noon triggered ccd.localization\"`.")


KNOWLEDGE = {
    "summary": "Trigger localization at noon and monitor its PR until merge or the 6 PM omission cutoff.",
    "what": (
        "At noon on CCD, pipeline 405133 (msazure/One) is triggered with "
        "isCreatePrSelected=true. Scout then polls the run every hour. If it "
        "doesn't finish within 3 hours, Scout emails the release engineer to check it "
        "or run the manual localization steps. When it completes, Scout reads the "
        "OneLocBuild@3 task log: if it created a translations PR ('Pull request "
        "created with ID <n>'), there ARE new strings — Scout posts that PR to the "
        "Code reviews chat and @mentions the release engineer. Scout keeps polling the "
        "PR hourly and keeps Phase 1 open until it merges or reaches the omission cutoff. "
        "If it is still unmerged at "
        "4:00 PM Los Angeles time, Scout posts one Code reviews warning that translated "
        "strings are at risk. At 6:00 PM Los Angeles time, Scout marks localization "
        "omitted so the scheduled release continues to Phase 2 without those strings. "
        "If no PR was created, there were no new strings."),
    "who": (
        "Scout runs the whole flow automatically (trigger + poll + notify/post). The "
        "release engineer only steps in if the 3-hour timeout email arrives, or to "
        "review/merge the posted translations PR. The final inclusion cutoff is "
        "6:00 PM Los Angeles time."),
    "where": [
        "Pipeline run: https://dev.azure.com/msazure/One/_build?definitionId=405133 (open the OneLocBuild@3 task log).",
        "The PR id appears in that log as: Pull request created with ID '<n>'.",
        "Resulting PR: https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
        "Reads use az (the ADO MCP is bound to identitydivision/Engineering and can't reach msazure/One): az pipelines build show for status; az devops invoke --area build --resource timeline to find the OneLocBuild@3 log id; then --resource logs to read it.",
    ],
    "how": (
        "Automatic. If the timeout email arrives, open the pipeline and either wait/"
        "re-run it or follow the manual localization steps in the doc below. Once the "
        "PR is posted to Code reviews, review and merge it into the release branch. "
        "The localization step completes only after ADO reports the PR merged."),
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
         "a": "It reads the OneLocBuild@3 task log (via az devops invoke against msazure/One) for the line \"Pull request created with ID '<n>'\" and uses the PR URL printed there. No line = no new strings."},
        {"q": "When does the localization step finish?",
         "a": "With no PR, it finishes when the pipeline completes. With a PR, Scout polls ADO hourly. At 4:00 PM Los Angeles time it warns Code reviews if the PR is unmerged. If it remains unmerged at 6:00 PM, localization is marked skipped/omitted and the release proceeds without those translated strings."},
        {"q": "Why not the ADO MCP?",
         "a": "The ADO MCP is bound to identitydivision/Engineering; msazure/One returns TF200016 (project not found). The az CLI reaches msazure/One as the signed-in user, so the poller reads via az."},
    ],
}
