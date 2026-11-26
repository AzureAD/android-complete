# Copyright (c) Microsoft Corporation. All rights reserved.
"""Step: `localization_pr` — locate the OneLoc PR and verify its merge status (Phase 1, ccd).

Single-responsibility, idempotent Python `agent` (`read_only`) step (`depends_on: [localization]`):
1. Pipeline still running (< 3h) -> `InProgress` (pending state in checklist while waiting for PR).
2. Pipeline succeeded with NO PR (`/createpr: True` and no PR created) -> `Done` with clear message
   that no localization PR was required/generated.
3. Pipeline succeeded and generated PR `#<id>`:
   - If PR is already merged (`status == "completed"`) -> `Done`.
   - If PR is not yet merged -> `Blocked` with clickable PR link asking the release owner to merge
     the PR and re-run the check (`Fixed — retry`).
"""
from __future__ import annotations

from dataclasses import dataclass

from orchestrator.authority import OwnStepData
from orchestrator.evidence import StepData
from orchestrator.outcomes import Blocked, Done, InProgress
from orchestrator.step_context import StepContext
from steps.ccd import localization as L
from orchestrator.step_context import MISSING

EVIDENCE = (OwnStepData(),)
ID = "localization_pr"
KIND = "agent"
NAME = "Verify localization PR & merge status"
REQUIRED_TOOLS = ("az",)
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True

@dataclass(frozen=True)
class BuildParameters:
    build_id: str | None = None
    pr_id: str | None = None

PARAMETERS = {"build": BuildParameters}

def _check_pr_merge(
    context: StepContext[BuildParameters],
    cfg: dict,
    *,
    bid: str | None,
    run_url: str | None,
    pr_id: str,
    pr_url: str,
):
    effective_pr_url = pr_url
    cfg_pr_status = context.input("pr_status", MISSING)
    if cfg_pr_status is not MISSING:
        pr_status = str(cfg_pr_status or "").strip().lower()
    else:
        pr_inspector = context.services.pipelines.inspect_localization_pr
        if pr_inspector is None:
            fallback_links = [{"name": f"Localization PR #{pr_id}", "url": effective_pr_url}]
            return Blocked(
                f"Localization PR inspector service is not configured (PR #{pr_id}: {effective_pr_url}).",
                links=fallback_links,
            )
        ok, pr_info, pr_detail = pr_inspector(cfg["org"], pr_id)
        if not ok or not isinstance(pr_info, dict):
            fallback_links = [{"name": f"Localization PR #{pr_id}", "url": effective_pr_url}]
            return Blocked(
                (pr_detail or f"Unable to read Localization PR #{pr_id} status") + f" ({effective_pr_url}).",
                links=fallback_links,
            )
        if isinstance(pr_info.get("web_url"), str) and pr_info["web_url"].strip():
            effective_pr_url = pr_info["web_url"].strip()
        pr_status = str(pr_info.get("status") or "").strip().lower()

    # Link directly to the PR that needs to be merged (`ccd.localization` already carries the build link).
    links = [{"name": f"Localization PR #{pr_id}", "url": effective_pr_url}]
    payload = {
        "build_id": bid,
        "run_url": run_url,
        "pr_id": str(pr_id),
        "pr_url": effective_pr_url,
        "pr_status": pr_status or "active",
        "no_strings": False,
    }
    if pr_status == "completed":
        return Done(
            f"Localization PR #{pr_id} is merged: {effective_pr_url}",
            links=links,
            updates=(StepData(payload),),
        )
    status_label = pr_status or "active"
    return Blocked(
        f"Localization PR #{pr_id} is open and not yet merged (ADO status: {status_label}). "
        f"Please merge the PR in Azure DevOps and re-run this check (Fixed — retry): {effective_pr_url}",
        links=links,
        updates=(StepData(payload),),
    )

def build(context: StepContext[BuildParameters]):
    cfg = L.CONFIG
    trigger_step = context.evidence.step("ccd", "localization")
    own_data = context.evidence.step("ccd", ID).data or {}

    explicit_pr = context.parameters.pr_id or context.input("pr_id", None)
    explicit_bid = context.parameters.build_id or context.input("build_id", None)

    if trigger_step.status == "skipped" and not explicit_bid and not explicit_pr:
        return Done(
            "No localization PR required — the localization pipeline trigger was skipped.",
            updates=(StepData({"skipped_upstream": True, "no_strings": True, "pr_id": None}),),
        )

    tdata = trigger_step.data or {}
    bid = str(explicit_bid or tdata.get("build_id") or "").strip() or None
    run_url = (
        tdata.get("run_url")
        or (f"https://dev.azure.com/msazure/{cfg['project']}/_build/results?buildId={bid}" if bid else None)
    )

    if explicit_pr:
        pid = str(explicit_pr).strip()
        return _check_pr_merge(
            context,
            cfg,
            bid=bid,
            run_url=run_url,
            pr_id=pid,
            pr_url=L.pr_url(pid, cfg),
        )

    # Fast path on retry: if we already extracted `pr_id` for this exact `build_id` (and no build/log
    # overrides are configured), skip re-downloading the build log and check the PR directly.
    if (
        bid
        and own_data.get("build_id") == bid
        and own_data.get("pr_id")
        and context.input("status", MISSING) is MISSING
        and context.input("logs", MISSING) is MISSING
    ):
        cached_pr = str(own_data["pr_id"])
        cached_url = str(own_data.get("pr_url") or L.pr_url(cached_pr, cfg))
        return _check_pr_merge(
            context,
            cfg,
            bid=bid,
            run_url=run_url,
            pr_id=cached_pr,
            pr_url=cached_url,
        )

    if not bid:
        return Blocked(
            "No OneLoc `build_id` is recorded on `ccd.localization` — re-run with "
            "`--param build_id=<id>` or `--param pr_id=<id>`, complete manually, or skip."
        )

    run_link = {"name": f"Localization build #{bid}", "url": run_url}
    started_at = tdata.get("started_at")

    cfg_status = context.input("status", MISSING)
    if cfg_status is not MISSING:
        status = str(cfg_status or "").strip()
        result = str(context.input("result", "") or "").strip()
        logs = context.input("logs", None)
        logs_complete = isinstance(logs, str)
        detail = ""
    else:
        inspector = context.services.pipelines.inspect_localization_build
        if inspector is None:
            return Blocked("Localization build inspector service is not configured.", links=[run_link])
        ok, info, detail = inspector(
            cfg["org"],
            cfg["project"],
            bid,
            oneloc_task=cfg.get("oneloc_task", "OneLocBuild@3"),
        )
        if not ok or not isinstance(info, dict):
            return Blocked(detail or f"Unable to inspect OneLoc build #{bid}.", links=[run_link])
        status = str(info.get("status") or "").strip()
        result = str(info.get("result") or "").strip()
        logs = info.get("logs")
        logs_complete = bool(info.get("logs_complete"))

    now = context.clock.utc()
    timeout_hrs = cfg.get("timeout_hours", 3)
    if status.lower() != "completed":
        mins = L.elapsed_minutes(started_at, now)
        if L.poll_status(False, started_at, now, timeout_hrs) == "timeout":
            return Blocked(
                f"OneLoc build #{bid} is still '{status or 'running'}' after {timeout_hrs}h — "
                "inspect the ADO run, re-run `ccd.localization`, complete manually, or skip.",
                links=[run_link],
            )
        elapsed_note = f"{mins}m elapsed" if mins is not None else "in progress"
        return InProgress(
            f"Localization pipeline build #{bid} is still running in ADO ({elapsed_note}) — "
            "PR has not been created yet.",
            links=[run_link],
            poll_in_min=30,
        )

    if result.lower() != "succeeded":
        return Blocked(
            f"OneLoc build #{bid} finished with result '{result or 'unknown'}' — "
            "inspect the build, re-run `ccd.localization`, complete manually, or skip.",
            links=[run_link],
        )

    pr_id, pr_url = L.extract_pr(logs, cfg) if (logs_complete and logs) else (None, None)
    if pr_id:
        return _check_pr_merge(
            context,
            cfg,
            bid=bid,
            run_url=run_url,
            pr_id=pr_id,
            pr_url=pr_url,
        )

    create_pr_enabled = L.extract_create_pr_enabled(logs) if (logs_complete and logs) else None
    if create_pr_enabled is True:
        return Done(
            f"No localization PR required — OneLoc build #{bid} completed with no new strings to localize.",
            links=[run_link],
            updates=(
                StepData(
                    {
                        "build_id": bid,
                        "run_url": run_url,
                        "pr_id": None,
                        "pr_url": None,
                        "no_strings": True,
                    }
                ),
            ),
        )
    if create_pr_enabled is False:
        return Blocked(
            f"OneLoc build #{bid} succeeded with PR creation disabled (`/createpr: False`) — "
            "re-run `ccd.localization` with `isCreatePrSelected=true`.",
            links=[run_link],
        )
    return Blocked(
        f"OneLoc build #{bid} succeeded, but the `{cfg.get('oneloc_task', 'OneLocBuild@3')}` log "
        f"did not confirm a PR or `/createpr: True` no-strings result ({detail or 'inspect task log'}).",
        links=[run_link],
    )

KNOWLEDGE = {
    "summary": "Locate the OneLoc translations PR and verify it is merged (or confirm no PR was required).",
    "what": (
        "Inspects the OneLoc build queued by `ccd.localization` via ADO REST. While the pipeline "
        "is still running, displays a pending (`in_flight`) status. If OneLoc finishes without "
        "generating a PR (`/createpr: True` and no new strings), marks the step done immediately. "
        "If a translations PR was generated: marks the step done if already merged, or marks it "
        "`Blocked` with a clickable PR link prompting the release owner to merge the PR and re-run "
        "the check."
    ),
    "who": (
        "Python (`agent` step) inspects the build and PR automatically; if a translations PR is "
        "open, the release owner merges it in Azure DevOps and clicks 'Fixed — retry'."
    ),
    "where": [
        "OneLoc pipeline (405133): https://dev.azure.com/msazure/One/_build?definitionId=405133",
        "Authenticator PRs: https://msazure.visualstudio.com/DefaultCollection/One/_git/AD-MFA-phonefactor-phoneApp-android/pullrequests",
    ],
    "how": (
        "Idempotent: re-run via 'Fixed — retry' (`task --phase ccd --step localization_pr --retry`) "
        "after merging the PR, or pass `--param pr_id=<id>` / `--param build_id=<id>` on `rerun`."
    ),
}
