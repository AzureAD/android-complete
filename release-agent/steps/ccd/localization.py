"""Step: `localization` — trigger the loc pipeline at noon, then poll it to completion
(Phase 1, P1-2).

Lifecycle (a small state machine — the engine can't wait/poll, so it's driven by a
per-release poller automation + this deterministic decider):

  1. TRIGGER  — `build()` returns a NeedsSkill to run pipeline 405133 with
                isCreatePrSelected=true. The runner records the queued build via
                `record-localization-run` (stores build id + start time), leaving the
                step IN-FLIGHT (not done).
  2. POLL     — every `poll_interval_min` (default 10) a poller calls
                `check-localization`, which reads the run status and applies
                `decide()`:
                  * still running & within `timeout_hours` (3h) → wait, poll again.
                  * still running past 3h → EMAIL the release engineer to check it /
                    do the manual steps (localization doc), and hold the step.
                  * completed → read the OneLocBuild@3 task log for
                    `Pull request created with ID '<n>'`:
                      - PR id found → POST that PR to the Code reviews chat for review
                        and mark the step done (with the PR link).
                      - no PR      → no new strings this release; mark done.

All the decision logic here is PURE (no IO) so it's fully testable; the IO (run the
pipeline, read status/logs, send the email, post the chat) is done by the skill /
poller via the NeedsSkill/decision payloads this module returns.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from orchestrator.outcomes import NeedsSkill, Blocked

ID = "localization"
KIND = "scout"

# Step config (co-located).
CONFIG = {
    "org": "https://msazure.visualstudio.com",
    "project": "One",
    "pipeline_id": 405133,
    "variables": {"isCreatePrSelected": "true"},
    "fire_at_local": "12:00",                 # noon on CCD (trigger; automation-driven)
    "poll_interval_min": 10,                  # re-check the run every N minutes
    "timeout_hours": 3,                       # escalate to the engineer if not done by then
    "oneloc_task": "OneLocBuild@3",           # the task whose log carries the PR id
    # The OneLoc task logs e.g.  Pull request created with ID '16790317'
    "pr_id_pattern": r"Pull request created with ID '(\d+)'",
    "pr_url_template": "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequest/{id}",
    # Post the resulting PR to the same "Code reviews" chat pr_reminder uses.
    "code_reviews_chat_id": "19:meeting_Y2Y3OGRjZGMtZGVkYi00MTkzLThhZjktNDAxYWVkMjZlMmE3@thread.v2",
    "code_reviews_chat_name": "Code reviews",
    "localization_doc": "https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/combined-release-checklist/localization",
    "links": {
        "pipeline": "https://dev.azure.com/msazure/One/_build?definitionId=405133",
        "repo_prs": "https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
    },
}

# No per-step mock knobs on the trigger; engine-level `outcome: done|blocked` still
# lets you clear/hold it offline (see `mock-spec`). The poll decider is tested via
# decide() directly.
MOCKABLE = {}


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


def pr_url(pr_id: str, cfg: dict = None) -> str:
    cfg = cfg or CONFIG
    return cfg["pr_url_template"].format(id=pr_id)


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


def _review_post(state, cfg: dict, pr_id: str, url: str) -> dict:
    content = (
        f"<p><b>Localization PR ready for review — {state.release_id}</b></p>"
        f"<p>The localization pipeline created translations PR "
        f"<a href=\"{url}\">#{pr_id}</a>. Please review &amp; merge it into the release "
        f"branch.</p>")
    return {"chatId": cfg["code_reviews_chat_id"], "content": content, "contentType": "html"}


def decide(state, is_complete: bool, logs: str = None, now=None, cfg: dict = None) -> dict:
    """Pure poll decision. Returns a dict with a `decision` and the payload the poller
    should act on:
      wait          -> {decision, elapsed_min, poll_in_min, note}
      timeout       -> {decision, email:{...}, note}          (hold the step)
      complete_pr   -> {decision, pr_id, pr_url, chat:{...}, links, note}  (done)
      complete_none -> {decision, note}                        (done, no strings)
    """
    cfg = cfg or CONFIG
    started = state.get_step("ccd", "localization").data.get("started_at")
    status = poll_status(is_complete, started, now, cfg.get("timeout_hours", 3))

    if status == "wait":
        mins = elapsed_minutes(started, now)
        poll_in = cfg.get("poll_interval_min", 10)
        return {"decision": "wait", "elapsed_min": mins, "poll_in_min": poll_in,
                "note": f"localization pipeline still running ({mins}m elapsed); "
                        f"re-check in {poll_in}m"}

    if status == "timeout":
        hrs = cfg.get("timeout_hours", 3)
        return {"decision": "timeout", "email": _timeout_email(state, cfg),
                "note": f"localization pipeline did not complete within {hrs}h — "
                        f"emailed the release engineer to check it / do the manual steps"}

    # completed
    pr_id = extract_pr_id(logs, cfg.get("pr_id_pattern"))
    if pr_id:
        url = pr_url(pr_id, cfg)
        return {"decision": "complete_pr", "pr_id": pr_id, "pr_url": url,
                "chat": _review_post(state, cfg, pr_id, url),
                "links": [{"name": f"Localization PR #{pr_id}", "url": url}],
                "note": f"localization complete — translations PR #{pr_id} created; "
                        f"posted to {cfg.get('code_reviews_chat_name', 'Code reviews')} for review"}
    return {"decision": "complete_none",
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

    variables = cfg.get("variables", {}) or {}
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
                "after": (
                    "Note the queued build id, then run "
                    "`record-localization-run --release <id> --build-id <buildId>` "
                    "(this leaves the step IN-FLIGHT). A poller then runs "
                    "`check-localization` every "
                    f"{cfg.get('poll_interval_min', 10)} min until it completes or "
                    f"times out after {cfg.get('timeout_hours', 3)}h."),
            },
            "links": _links(cfg),
        },
        record_as=ID,
        summary=f"Trigger localization pipeline {cfg['pipeline_id']} ({var_str}), then poll to completion",
        note=f"triggered pipeline {cfg['pipeline_id']} with {var_str}; polling every "
             f"{cfg.get('poll_interval_min', 10)}m (timeout {cfg.get('timeout_hours', 3)}h)",
    )


KNOWLEDGE = {
    "summary": "Trigger the loc pipeline at noon, poll it to completion, then post the translations PR for review.",
    "what": (
        "At noon on CCD, pipeline 405133 (msazure/One) is triggered with "
        "isCreatePrSelected=true. Scout then polls the run every 10 minutes. If it "
        "doesn't finish within 3 hours, Scout emails the release engineer to check it "
        "or run the manual localization steps. When it completes, Scout reads the "
        "OneLocBuild@3 task log: if it created a translations PR ('Pull request "
        "created with ID <n>'), there ARE new strings — Scout posts that PR to the "
        "Code reviews chat for review; if no PR, there were no new strings."),
    "who": (
        "Scout runs the whole flow automatically (trigger + poll + notify/post). The "
        "release engineer only steps in if the 3-hour timeout email arrives, or to "
        "review/merge the posted translations PR."),
    "where": [
        "Pipeline run: https://dev.azure.com/msazure/One/_build?definitionId=405133 (open the OneLocBuild@3 task log).",
        "The PR id appears in that log as: Pull request created with ID '<n>'.",
        "Resulting PR: https://msazure.visualstudio.com/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
    ],
    "how": (
        "Automatic. If the timeout email arrives, open the pipeline and either wait/"
        "re-run it or follow the manual localization steps in the doc below. Once the "
        "PR is posted to Code reviews, review and merge it into the release branch."),
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
         "a": "It reads the OneLocBuild@3 task log for the line \"Pull request created with ID '<n>'\" and builds the PR link from that id. No line = no new strings."},
    ],
}
