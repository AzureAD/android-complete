"""Read-only, resumable setup planning; never creates a release or edits the host."""
from __future__ import annotations

from . import delivery, m365, m365_capabilities as M, structured_fetch as F


HEALTH_TOOLS = ("icm-get_on_call_schedule_by_team_id", "kusto_-_adx-kusto_query")
ISSUE_KINDS = {"authentication", "session", "access_denied", "unsupported", "connection_failed"}


def _action(kind, title, message, *, status="needs_action", **details):
    return {
        "status": status, "title": title, "message": message,
        "action": {"kind": kind, **details},
        "permission_to_start_release": False, "permission_to_write": False,
        "limitations": (
            "Onboarding checks installation and read connections only. "
            "Release readiness and on-call eligibility remain separate. "
            "Send/upload access and authorization are checked at each owning write step."
        ),
    }


def _support(integration, reason, **details):
    return _action(
        "support", f"{integration} setup is blocked", reason,
        status="blocked", integration=integration, **details,
        steps=[
            "Stop here; no release was started or changed.",
            "Ask your Scout administrator to enable or repair this connection.",
            "After the configuration or access changes, run /release-agent check setup.",
        ])


def _recover(integration, kind, completed):
    if kind == "access_denied":
        return _support(integration, "The read was denied. An administrator must grant the required access.")
    if kind == "unsupported":
        return _support(integration, "This Scout installation does not support the required connection.")
    action_id = f"{integration}:{'sign_in' if kind == 'authentication' else 'reconnect'}"
    if action_id in completed:
        return _support(integration, "The connection still failed after the guided recovery. Do not repeat it.")
    return _action(
        "sign_in" if kind == "authentication" else "reconnect",
        f"{integration} needs {'sign-in' if kind == 'authentication' else 'reconnection'}",
        "Complete this connection step once; Scout will then repeat the failed read.",
        action_id=action_id, integration=integration,
        steps=(
            ["Use Scout's supported sign-in window. Never paste a token into chat or a config file."]
            if kind == "authentication" else
            ["Open Scout's MCP server settings.", f"Reconnect {integration}, then reload its tools."]),
        question="Continue with this connection step?",
        answers=[{"title": "Continue"}, {"title": "Keep setup held"}])


def _owner(profile, *, now=None):
    if not isinstance(profile, dict):
        raise ValueError("Profile observation must be an object")
    raw = profile.get("result")
    if not isinstance(raw, dict):
        raise ValueError("Profile read must return a structured identity")
    owner = next((raw.get(k) for k in ("userPrincipalName", "mail", "email")
                  if isinstance(raw.get(k), str) and "@" in raw[k]), None)
    if not owner:
        raise ValueError("Profile read did not return an account address")
    result = m365.validate_observation(
        profile, tool="workiq_get_my_profile", arguments={}, owner=owner, now=now)
    if not m365.profile_matches(result, owner):
        raise ValueError("Profile read did not identify the signed-in account")
    return owner


def _health_requests(readiness, owner):
    items = {item["id"]: item for item in readiness["items"]}
    team = items["oncall_now"]["team_id"]
    adx = items["adx_access"]
    if type(team) is not int or team < 1 or not adx.get("cluster_uri") or not adx.get("database"):
        raise ValueError("Onboarding requires configured ICM and ADX read targets")
    return {
        "icm": m365.observation_request(HEALTH_TOOLS[0], {"teamIds": [team]}, owner),
        "adx": m365.observation_request(HEALTH_TOOLS[1], {
            "cluster_uri": adx["cluster_uri"], "database": adx["database"], "query": "print 1",
        }, owner),
    }


def _tool_issue(missing, completed, discovery):
    if F.FETCH_TOOL in missing:
        return _support(
            "WorkIQ structured fetch",
            (discovery or {}).get("detail") or
            "No actual MCP fetch or verified native fetch launcher was observed for this Scout. "
            "Inspect its tools and supply --workiq-path for an existing installation; "
            "do not use Ask or claim all Scout versions lack this capability.")
    if any(tool.startswith("microsoft_teams-") for tool in missing):
        return _recover("Microsoft Teams", "session", completed)
    if any(tool.startswith("playwright-") for tool in missing):
        return _recover("Browser", "session", completed)
    if any(tool.startswith("icm-") for tool in missing):
        return _recover("ICM", "session", completed)
    if any(tool.startswith("kusto_") for tool in missing):
        return _recover("Kusto / ADX", "session", completed)
    return _recover("Microsoft 365", "authentication", completed)


def plan(report, evidence, readiness, *, now=None):
    if evidence is not None:
        if (not isinstance(evidence, dict) or type(evidence.get("version")) is not int
                or evidence["version"] != 1):
            raise ValueError("Onboarding requires a version-1 observation")
        m365.validate_freshness(evidence.get("observed_at"), now=now)
    completed = [] if evidence is None else evidence.get("completed_actions", [])
    if (not isinstance(completed, list)
            or any(not isinstance(item, str) or not item for item in completed)):
        raise ValueError("Completed setup actions must be explicit action IDs")
    if report["path_requests"]:
        request = report["path_requests"][0]
        return _action(
            "path", "One installation path is needed", request["question"],
            path_request=request, question=request["question"], input_hint="Existing folder or executable path")
    if not report["infrastructure_ok"]:
        if "installation:repair" in completed:
            return _support("Installation", "The approved installation repair did not resolve the reported prerequisites.")
        problems = ([r for r in report["requirements"] if not r["ok"]]
                    + [r for r in report["mcp_servers"] if r["status"] not in ("present", "registered")])
        if (all(r["ok"] for r in report["requirements"]) and problems
                and all(p.get("status") == "would_register" for p in problems)):
            return _action(
                "register_mcp", "Registering missing release connections",
                "Setup registers missing connections like the other MCP servers. No form or extra setup question is needed.",
                status="checking", command=["infra"], missing_servers=problems,
                steps=["Run infra with registration enabled for this resolved Scout profile.",
                       "Preserve every existing connection and all authentication/permission settings.",
                       "Recheck onboarding after registration; loading tools is not access proof."])
        return _action(
            "repair_installation", "Repair installation prerequisites",
            "Scout will explain the exact local changes before requesting permission to run setup.",
            action_id="installation:repair", problems=problems, question="Review the installation repair?",
            answers=[{"title": "Review repair"}, {"title": "Keep setup held"}])
    if evidence is None:
        return _action(
            "observe_tools", "Checking available connections",
            "Scout will inspect its loaded tools. No files or terminal commands are needed from you.",
            status="checking", required_tools=[*M.REQUIRED_TOOLS, *HEALTH_TOOLS])
    available = evidence.get("available_tools")
    discovery = ((report.get("m365_connectors") or {}).get("structured_fetch")
                 or evidence.get("structured_fetch"))
    missing = M.missing_tools(available, structured_fetch=discovery, now=now)
    missing += [tool for tool in HEALTH_TOOLS if available.get(tool) != tool]
    if missing:
        return _tool_issue(missing, completed, discovery)
    observations = evidence.get("observations", {})
    if not isinstance(observations, dict):
        raise ValueError("Onboarding read observations must be an object")
    issues = evidence.get("issues", {})
    if not isinstance(issues, dict):
        raise ValueError("Onboarding read failures must be an object")
    profile = observations.get("profile")
    if profile is None:
        requests = {"profile": {"tool": "workiq_get_my_profile", "arguments": {}, "read_only": True}}
        owner = None
    else:
        owner = _owner(profile, now=now)
        requests = {**M.read_plan(owner, observations, now=now)["requests"],
                    **_health_requests(readiness, owner)}
    expected_keys = {"profile", "calendar", "roster", "roster_next", "chat", "members",
                     "sharepoint", "report_library", "report_library_next", "icm", "adx"}
    if set(observations) - expected_keys or set(issues) - set(requests):
        raise ValueError("Unexpected onboarding read or failure; recollect the current request")
    for key, issue in issues.items():
        request = requests[key]
        if (not isinstance(issue, dict) or issue.get("kind") not in ISSUE_KINDS
                or issue.get("tool") != request["tool"] or issue.get("arguments") != request["arguments"]
                or owner is not None and issue.get("account") != owner):
            raise ValueError("Read failure must match the exact onboarding tool/account/arguments")
        m365.validate_freshness(issue.get("observed_at"), now=now)
        integration = ("ICM" if key == "icm" else "Kusto / ADX" if key == "adx" else
                       "WorkIQ structured fetch" if request["tool"] == F.FETCH_TOOL else
                       "Microsoft Teams" if request["tool"].startswith("microsoft_teams-") else "Microsoft 365")
        return _recover(integration, issue["kind"], completed)
    pending = {key: request for key, request in requests.items() if key not in observations}
    if pending:
        return _action(
            "read", "Checking connection access", "Scout is checking read-only access to the configured resources.",
            status="checking", requests=pending)
    M.validate_reads(owner, observations, now=now)
    for key, request in _health_requests(readiness, owner).items():
        result = m365.validate_observation(
            observations[key], tool=request["tool"], arguments=request["arguments"], owner=owner, now=now)
        if key == "icm":
            schedules = result if isinstance(result, list) else [result]
            if not schedules or any(not isinstance(row, dict)
                                    or not isinstance(row.get("shiftCurrentOnCalls"), list) for row in schedules):
                raise ValueError("ICM onboarding probe did not return a structured on-call schedule")
        if key == "adx" and not (
                isinstance(result, dict) and isinstance(result.get("data"), dict)
                and result["data"].get("rows") == [[1]]):
            raise ValueError("ADX onboarding probe did not return the requested print 1 result")
    result = _action(
        "complete", "Onboarding complete",
        "Installation, exact directory/group membership and report-library reads are verified. "
        "No release was started or changed.",
        status="ready")
    result.update(
        account=owner, proof_hash=delivery.fingerprint({"tools": available, "observations": observations}),
        routing={
            "version": 1, "account": owner, "targets": M.targets(),
            "structured_fetch": F.route(available, discovery, now=now),
            "canonical_tool": F.FETCH_TOOL,
            "usage": (
                "Use the exposed MCP fetch name, or workiq-read --scout-root <selected-profile> "
                "--account <verified-owner> --entity-url <exact requested path>. "
                "Save fresh canonical workiq.fetch observations; follow all returned pages. "
                "This saved recipe is not a durable access grant; recheck on this user's Scout."),
            "observation_hash": delivery.fingerprint(observations),
        },
        next_action="When you want to start, say: /release-agent start a release")
    return result
