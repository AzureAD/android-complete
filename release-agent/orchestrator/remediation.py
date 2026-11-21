# Copyright (c) Microsoft Corporation. All rights reserved.
"""Human-actionable external permission/connector diagnostics and 1-click escape hatches."""
from __future__ import annotations

from typing import Optional

ONEAUTH_ACCESS_PACKAGE_URL = (
    "https://myaccess.microsoft.com/@microsoft.onmicrosoft.com#/access-packages/"
    "e9958ef5-ccfa-4041-8622-0f76c0429bc4"
)
ADX_DASHBOARD_URL = (
    "https://dataexplorer.azure.com/dashboards/ab7abd7e-c36f-4ff0-88a7-aaa3ec014bd7"
)
PLAY_CONSOLE_URL = (
    "https://play.google.com/console/u/0/developers/6720847872553662727/"
    "app/4972501392087484764/app-dashboard"
)
ONCALL_URL = "https://aka.ms/androidoncall"


CCOA_URL = "https://prod.change-manager.msidentity.com/ccoa-periods"


def _is_connector_error(low: str, tool: Optional[str]) -> bool:
    if tool:
        return True
    markers = (
        "required tool/connector",
        "az login",
        "unauthorized",
        "forbidden",
        "access denied",
        "accessdenied",
        "401",
        "403",
        "aadsts",
        "session not found",
        "mcp",
        "playwright",
        "workiq",
        "executable not found",
        "launcher not found",
        "not logged in",
    )
    return any(m in low for m in markers)


def step_remediation(
    release_id: str,
    phase_id: str,
    step_id: str,
    reason: str = "",
    *,
    tool: Optional[str] = None,
) -> dict:
    """Build a structured, human-actionable fix + 1-command escape hatch for a blocked step."""
    rid = release_id or "<YYYY-MM>"
    low = f"{step_id} {tool or ''} {reason}".lower()
    system, fix = _classify_external_fix(
        low, step_id=step_id, tool=tool, reason=reason, release_id=rid
    )
    return {
        "system": system,
        "how_to_fix": fix,
        "complete_manually_command": (
            f"complete-manually --release {rid} --phase {phase_id} --step {step_id} "
            "--note \"Completed manually outside Scout\""
        ),
        "skip_command": (
            f"skip --release {rid} --phase {phase_id} --step {step_id} "
            "--reason \"Handled outside orchestrator\""
        ),
    }


def readiness_remediation(release_id: str, item_id: str, reason: str = "") -> dict:
    """Build a structured, human-actionable fix + 1-command escape hatch for a readiness item."""
    rid = release_id or "<YYYY-MM>"
    low = f"{item_id} {reason}".lower()
    system, fix = _classify_external_fix(
        low, step_id=item_id, tool=None, reason=reason, release_id=rid
    )
    return {
        "system": system,
        "how_to_fix": fix,
        "complete_manually_command": (
            f"complete-readiness-manually --release {rid} --item {item_id} "
            "--note \"Verified manually\""
        ),
        "skip_command": (
            f"skip-readiness --release {rid} --item {item_id} "
            "--reason \"Waived by release owner\""
        ),
    }


def _classify_external_fix(
    low: str,
    *,
    step_id: str = "",
    tool: Optional[str] = None,
    reason: str = "",
    release_id: str = "<YYYY-MM>",
) -> tuple[str, str]:
    clean_reason = " ".join((reason or "").split())
    connector_err = _is_connector_error(low, tool)

    # 1. Domain / policy / check findings (connector succeeded, check found a real blocker)
    if not connector_err:
        if step_id == "lockdown" or ("ccoa" in low and "overlap" in low):
            return (
                f"CCOA Change-Freeze / Lockdown Schedule ({CCOA_URL})",
                f"{clean_reason} Action required: inspect the CCOA schedule ({CCOA_URL}) first — "
                f"either shift CCD outside the freeze with "
                f"`set-ccd --release {release_id} --date <YYYY-MM-DD> --reason \"Shift past CCOA lockdown\"` "
                "and re-run, confirm your rollout dates avoid shipping during the freeze (`complete-manually`), "
                "or waive (`skip`) ONLY if you have an approved CCOA lockdown exception.",
            )
        if step_id == "cg":
            return (
                "Component Governance (Security Alerts)",
                f"{clean_reason} Remediate or dismiss High/Critical alerts in Azure DevOps and retry, "
                "or waive with `skip` if approved.",
            )
        if step_id == "telemetry_verify":
            return (
                f"Release Telemetry Verification ({ADX_DASHBOARD_URL})",
                f"{clean_reason} Verify telemetry ingestion for the current RC build and re-run, "
                "or waive with `skip` if approved.",
            )
        if step_id == "localization_pr":
            return (
                "Azure DevOps — Localization Pull Request (OneLoc)",
                f"{clean_reason} Open the linked Localization PR in Azure DevOps, review and merge it, "
                f"then click 'Fixed — retry' (`task --release {release_id} --phase ccd --step localization_pr --retry`) "
                "— or override with `complete-manually` / `skip`.",
            )
        if step_id == "activate_chat":
            return (
                "Microsoft Teams — Bug Bash Meeting Chat",
                f"{clean_reason} Manual fallback (no Playwright needed): 1) Open the Bug Bash invite in "
                "Outlook/Teams Calendar and click 'Chat with participants' (send a quick message if needed "
                "to create the thread). 2) Click '...' -> 'Copy link' on the chat/message. 3) Share that "
                f"Teams link with Scout (`record-bugbash-chat --release {release_id} --chat-link <url>`) "
                "to store the `19:meeting_...@thread.v2` chat ID for `bugbash_updates`.",
            )
        if step_id in (
            "localization", "cron", "checker_fired", "orchestrator_health", "mrwp_ecs", "mrwp_local",
            "auth_ecs", "verify_pub", "verify_release_notes", "identify_auth_build",
        ):
            return (
                "Azure Pipelines / Release Verification Check",
                f"{clean_reason} Investigate or re-run the pipeline/stage in Azure DevOps and retry "
                "(`task --retry`), or override with `skip` / `complete-manually`.",
            )

    # 2. External permission / tool / connector failures
    if step_id == "activate_chat":
        return (
            "Microsoft Teams — Bug Bash Meeting Chat",
            "Manual fallback (no Playwright needed): 1) Open the Bug Bash invite in Outlook/Teams Calendar "
            "and click 'Chat with participants' (send a quick message if needed so Teams creates the thread). "
            "2) Click '...' -> 'Copy link' on the chat/message. 3) Share that Teams URL with Scout "
            f"(`record-bugbash-chat --release {release_id} --chat-link <url>`) to bind the meeting chat ID.",
        )
    if step_id == "oneauth_access" or "tf401027" in low or "createbranch" in low:
        return (
            "Azure DevOps — OneAuth Git Repository",
            f"Request OneAuth R/W access in MyAccess ({ONEAUTH_ACCESS_PACKAGE_URL}), "
            "verify `az account show`, then retry — or mark completed manually / skip.",
        )
    if step_id == "build_access" or ("ado" in low and "build" in low) or "az login" in low or tool == "az":
        return (
            "Azure DevOps / Azure CLI (`az`)",
            "Run `az login` in a terminal with your Microsoft corporate account, verify "
            "`az extension show --name azure-devops`, then retry.",
        )
    if step_id in ("adx_access", "telemetry_verify") or "kusto" in low or "adx" in low or tool == "kusto":
        return (
            "Azure Data Explorer (Kusto)",
            f"Verify access to the ADX release dashboard ({ADX_DASHBOARD_URL}) and ensure "
            "the `kusto` MCP server is connected in Scout Settings → MCP Servers.",
        )
    if step_id == "oncall_now" or "icm" in low or tool == "icm":
        return (
            "IcM On-Call Schedule (`icm` MCP)",
            f"Check the Android on-call schedule ({ONCALL_URL}) and verify the `icm` MCP "
            "server is connected in Scout Settings → MCP Servers.",
        )
    if tool == "agency_mcp" or "agency mcp" in low or "agency cli" in low:
        return (
            "Agency CLI MCP (`agency.exe mcp mail/teams/calendar`)",
            "Verify `%APPDATA%\\agency\\CurrentVersion\\agency.exe --version` works and Entra WAM SSO is signed in, then retry.",
        )
    if tool == "microsoft_teams" or "microsoft_teams" in low or "session not found" in low:
        return (
            "Microsoft Teams MCP Connector",
            "Open Scout Settings → MCP Servers → `microsoft_teams` and click Reconnect "
            "(or restart Scout), then retry.",
        )
    if tool == "workiq" or "workiq" in low or "m365" in low:
        return (
            "Microsoft 365 / WorkIQ Connector",
            "Ensure Microsoft 365 (WorkIQ) is signed in and enabled in Scout Settings → "
            "Permissions / Connectors, then retry.",
        )
    if tool == "playwright" or "playwright" in low or step_id == "lockdown":
        return (
            "Playwright Browser Connector (Edge SSO)",
            "Ensure Playwright browser tools are enabled in Scout Settings → Permissions "
            "(or inspect the target page manually in Edge and record the result).",
        )
    if tool == "gh" or "github" in low:
        return (
            "GitHub CLI (`gh`)",
            "Run `gh auth status` / `gh auth login` in a terminal and ensure SSO is "
            "authorized for the `AzureAD` organization, then retry.",
        )
    if "play_console" in low or "vitals" in low:
        return (
            "Google Play Console",
            f"Sign in to Google Play Console ({PLAY_CONSOLE_URL}) with your authorized "
            "Google account, or request Play Console access from the Android release team.",
        )
    return (
        f"External dependency ({tool or step_id or 'step'})",
        "Resolve the external access or connector issue shown in the error details and retry, "
        "or use `complete-manually` / `skip` to proceed manually.",
    )
