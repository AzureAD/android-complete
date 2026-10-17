"""Read-only entry probes and explicit limits of Microsoft 365 capability evidence."""
from __future__ import annotations

from urllib.parse import quote, urlparse

from . import delivery, m365


REQUIRED_TOOLS = (
    "workiq_get_my_profile", "workiq_list_events", "workiq_get_event",
    "workiq_create_event", "workiq_send_email", "workiq_send_chat_message",
    "microsoft_teams-GetChat", "microsoft_teams-ListChatMembers",
    "microsoft_teams-SendMessageToUser", "microsoft_teams-SendMessageToChannel",
    "workiq.fetch", "workiq_resolve_m365_link", "workiq_download_file",
    "playwright-browser_navigate", "playwright-browser_snapshot",
    "playwright-browser_file_upload",
)


def targets():
    from tools.coordinates import coords
    from tools.distribution import load_config

    report = coords.sharepoint("rc_reports")
    return {
        "roster_group": load_config()["roster_group"],
        "representative_chat": coords.team("code_reviews")["chat"],
        "report_site": report["site_url"], "report_library": report["library"],
        "report_folder": report["folder"],
    }


def binding(state):
    return delivery.fingerprint({
        "release": state.release_id, "owner": state.owner_email, "ccd": state.ccd,
        "tools": REQUIRED_TOOLS, "targets": targets(),
    })


def site_path(site_url):
    parsed = urlparse(site_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Capability probe requires an HTTPS SharePoint site")
    return f"/sites/{parsed.hostname}:{parsed.path.rstrip('/')}?$select=id,webUrl"


def _rows(result, label):
    if isinstance(result, list):
        return result
    if (not isinstance(result, dict) or result.get("@odata.nextLink")
            or result.get("nextLink") or result.get("hasMoreResults")
            or not isinstance(result.get("value"), list)):
        raise ValueError(f"{label} requires a complete structured collection")
    return result["value"]


def plan(state, evidence=None):
    from tools.distribution import roster_read_request, roster_observed, ConnectorObservationRequired
    target = targets()
    owner = state.owner_email
    requests = {
        "profile": m365.observation_request("workiq_get_my_profile", {}, owner),
        "calendar": m365.observation_request(
            "workiq_list_events", {"limit": 1, "query": {"select": [
                "id", "organizer", "attendees", "onlineMeeting", "start", "end"]}}, owner),
        "roster": roster_read_request(target["roster_group"], owner),
        "chat": m365.observation_request(
            "microsoft_teams-GetChat", {"chatId": target["representative_chat"]}, owner),
        "members": m365.observation_request(
            "microsoft_teams-ListChatMembers", {"chatId": target["representative_chat"]}, owner),
        "sharepoint": m365.observation_request(
            "workiq.fetch", {"entityUrls": [site_path(target["report_site"])]}, owner),
    }
    if evidence is not None:
        if not isinstance(evidence, dict) or evidence.get("binding") != binding(state):
            raise ValueError("Capability follow-up evidence belongs to another release/owner/configuration")
        observations = evidence.get("observations") or {}
        if not isinstance(observations, dict):
            raise ValueError("Capability observations must be an object")
        if "roster" in observations:
            try:
                roster_observed(target["roster_group"], {"observations": observations["roster"]}, owner)
            except ConnectorObservationRequired as exc:
                requests["roster_next"] = exc.requests[0]
            else:
                requests.pop("roster")
        if "sharepoint" in observations:
            site = m365.validate_observation(
                observations["sharepoint"], tool=requests["sharepoint"]["tool"],
                arguments=requests["sharepoint"]["arguments"], owner=owner)
            if (not isinstance(site, dict) or not isinstance(site.get("id"), str) or not site["id"]
                    or str(site.get("webUrl", "")).rstrip("/").casefold()
                    != target["report_site"].rstrip("/").casefold()):
                raise ValueError("Capability follow-up must use the exact configured SharePoint site")
            requests["report_library"] = m365.observation_request(
                "workiq.fetch", {"entityUrls": [f"/sites/{quote(site['id'], safe=',-')}/drives"]}, owner)
    return {
        "binding": binding(state), "required_tools": list(REQUIRED_TOOLS),
        "requests": requests,
        "followup_reads": {
            "roster_pages": "Pass partial --evidence-file to request the exact next group/member page",
            "report_library": "/sites/<exact returned site id>/drives",
        },
        "permission_to_send": False, "permission_to_upload": False,
        "limitations": (
            "Read probes and discovered write tools do not prove permission to send/upload. "
            "Confirm write access separately. A future Bug Bash event/chat must be verified "
            "when created. Hosted WorkIQ new-file upload is unavailable; immutable report "
            "creation uses a separately owned, approved SharePoint browser upload."
        ),
    }


def _tool_available(canonical, resolved):
    if canonical != "workiq.fetch":
        return resolved == canonical
    return resolved in ("workiq-fetch", "workiq.fetch", "workiq:fetch", "mcp__workiq__fetch")


def validate(state, evidence, *, now=None):
    expected = plan(state)
    if not isinstance(evidence, dict) or evidence.get("binding") != expected["binding"]:
        raise ValueError("M365 capability evidence does not match this release/owner/CCD/target configuration")
    available = evidence.get("available_tools")
    if not isinstance(available, dict):
        raise ValueError("Record the actual loaded tool inventory, not server registration alone")
    missing = [tool for tool in REQUIRED_TOOLS if not _tool_available(tool, available.get(tool))]
    if missing:
        raise ValueError("Missing loaded connector capabilities: " + ", ".join(missing))
    observations = evidence.get("observations")
    if not isinstance(observations, dict):
        raise ValueError("Successful read probes are required; tool names alone are not capability proof")

    def read(key, tool=None, arguments=None):
        request = expected["requests"].get(key)
        return m365.validate_observation(
            observations.get(key), tool=tool or request["tool"],
            arguments=arguments if arguments is not None else request["arguments"],
            owner=state.owner_email, now=now)

    profile = read("profile")
    if not m365.profile_matches(profile, state.owner_email):
        raise ValueError("M365 profile does not identify the release owner")
    calendar = read("calendar")
    if not (isinstance(calendar, list) or isinstance(calendar, dict)
            and isinstance(calendar.get("events", calendar.get("value")), list)):
        raise ValueError("Calendar probe returned no structured event collection")
    target = targets()
    from tools.distribution import roster_observed
    roster_pages = observations.get("roster")
    members, _ = roster_observed(
        target["roster_group"], {"observations": roster_pages}, state.owner_email, now=now)
    if not members:
        raise ValueError("Roster member probe returned no eligible directory users")
    chat = read("chat")
    if (not isinstance(chat, dict) or chat.get("id") != target["representative_chat"]
            or chat.get("chatType") not in ("meeting", "group")):
        raise ValueError("Teams probe did not read the exact configured chat")
    member_result = read("members")
    if isinstance(member_result, dict) and isinstance(member_result.get("members"), list):
        if (member_result.get("hasMoreResults") or member_result.get("nextLink")
                or member_result.get("@odata.nextLink")):
            raise ValueError("Teams member probe is incomplete")
        member_result = member_result["members"]
    chat_members = _rows(member_result, "Teams members")
    if not chat_members or not any(isinstance(row, dict) and row.get("userId") == profile["id"]
                                   for row in chat_members):
        raise ValueError("Teams membership does not include the verified release owner")
    site = read("sharepoint")
    if (not isinstance(site, dict) or not isinstance(site.get("id"), str) or not site["id"]
            or str(site.get("webUrl", "")).rstrip("/").casefold()
            != target["report_site"].rstrip("/").casefold()):
        raise ValueError("SharePoint probe must read the exact configured site")
    drives = _rows(read("report_library", "workiq.fetch", {
        "entityUrls": [f"/sites/{quote(site['id'], safe=',-')}/drives"]}), "Report libraries")
    libraries = [row for row in drives if isinstance(row, dict)
                 and row.get("name") == target["report_library"] and row.get("id")]
    if len(libraries) != 1:
        raise ValueError("Report library did not resolve uniquely in the configured site")
    return {
        "capability_binding": expected["binding"],
        "checks": [
            {"name": "owner/calendar", "ok": True, "detail": "owner identity and event read verified"},
            {"name": "distribution roster", "ok": True, "detail": "exact group and complete member read"},
            {"name": "Teams chat/members", "ok": True, "detail": "exact representative chat and owner membership"},
            {"name": "SharePoint library", "ok": True, "detail": "exact report site and library read"},
        ],
        "limitations": expected["limitations"],
        "observation_hash": delivery.fingerprint(observations),
    }
