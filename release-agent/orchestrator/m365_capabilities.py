"""Read-only entry probes and explicit limits of Microsoft 365 capability evidence."""
from __future__ import annotations

from urllib.parse import quote, urlsplit

from . import delivery, m365, structured_fetch as F


REQUIRED_TOOLS = (
    "workiq_get_my_profile", "workiq_list_events", "workiq_get_event",
    "workiq_create_event", "workiq_send_email", "workiq_send_chat_message",
    "microsoft_teams-GetChat", "microsoft_teams-ListChatMembers",
    "microsoft_teams-SendMessageToUser", "microsoft_teams-SendMessageToChannel",
    "workiq_resolve_m365_link", "workiq_download_file",
    "playwright-browser_navigate", "playwright-browser_snapshot",
    "playwright-browser_file_upload",
    F.FETCH_TOOL,
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


def _rows(result, label):
    if isinstance(result, list):
        return result
    if (not isinstance(result, dict) or result.get("@odata.nextLink")
            or result.get("nextLink") or result.get("hasMoreResults")
            or not isinstance(result.get("value"), list)):
        raise ValueError(f"{label} requires a complete structured collection")
    return result["value"]


def site_path(site_url):
    parsed = urlsplit(site_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Capability probe requires an exact HTTPS SharePoint site")
    return f"/sites/{parsed.hostname}:{parsed.path.rstrip('/')}?$select=id,webUrl"


def _site(observation, owner, *, now=None):
    target = targets()
    site = m365.validate_observation(
        observation, tool=F.FETCH_TOOL, arguments={"entityUrls": [site_path(target["report_site"])]},
        owner=owner, now=now)
    if (not isinstance(site, dict) or not isinstance(site.get("id"), str) or not site["id"]
            or str(site.get("webUrl", "")).rstrip("/").casefold()
            != target["report_site"].rstrip("/").casefold()):
        raise ValueError("SharePoint probe must read the exact configured site")
    return site


def _libraries(site, observations, owner, *, now=None):
    from tools.distribution import ConnectorObservationRequired
    envelopes = observations if isinstance(observations, list) else (
        [] if observations is None else [observations])
    root = f"/sites/{quote(site['id'], safe=',-')}/drives"
    path, seen, rows, count = root, set(), [], None
    index = 0
    while path:
        if path in seen or len(seen) >= 1000:
            raise ValueError("Cyclic/excessive report-library pagination")
        seen.add(path)
        if index == len(envelopes):
            raise ConnectorObservationRequired(
                "Read the exact report-library page using the discovered structured fetch route",
                tool=F.FETCH_TOOL, arguments={"entityUrls": [path]}, owner=owner)
        result = m365.validate_observation(
            envelopes[index], tool=F.FETCH_TOOL, arguments={"entityUrls": [path]}, owner=owner, now=now)
        if (not isinstance(result, dict) or not isinstance(result.get("value"), list)
                or any(result.get(key) for key in
                       ("hasMoreResults", "nextLink", "nextSkipToken", "isPartial", "truncated"))
                or result.get("complete") is False):
            raise ValueError("Report libraries require complete structured collection pages")
        rows.extend(result["value"])
        if "@odata.count" in result:
            observed_count = result["@odata.count"]
            if (type(observed_count) is not int or observed_count < 0
                    or count is not None and observed_count != count):
                raise ValueError("Invalid/changing report-library collection count")
            count = observed_count
        index += 1
        link = result.get("@odata.nextLink")
        if not link:
            path = None
        else:
            parsed = urlsplit(link)
            if parsed.scheme:
                if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com":
                    raise ValueError("Invalid report-library pagination URL")
                path = parsed.path.removeprefix("/v1.0") + ("?" + parsed.query if parsed.query else "")
            else:
                path = link
            if path.split("?", 1)[0] != root or "#" in path:
                raise ValueError("Report-library pagination changed site/entity")
    if index != len(envelopes):
        raise ValueError("Unexpected/unconsumed report-library observations")
    if count is not None and len(rows) != count:
        raise ValueError("Incomplete report-library collection count")
    return rows


def read_plan(owner, observations=None, *, now=None):
    from tools.distribution import roster_read_request, roster_observed, ConnectorObservationRequired
    target = targets()
    requests = {
        "profile": m365.observation_request("workiq_get_my_profile", {}, owner),
        "calendar": m365.observation_request(
            "workiq_list_events", {"limit": 1, "query": {"select": [
                "id", "organizer", "attendees", "onlineMeeting", "start", "end"]}}, owner),
        "chat": m365.observation_request(
            "microsoft_teams-GetChat", {"chatId": target["representative_chat"]}, owner),
        "members": m365.observation_request(
            "microsoft_teams-ListChatMembers", {"chatId": target["representative_chat"]}, owner),
        "roster": roster_read_request(target["roster_group"], owner),
        "sharepoint": F.request([site_path(target["report_site"])], owner),
    }
    if observations is not None and not isinstance(observations, dict):
        raise ValueError("Capability observations must be an object")
    if observations is not None:
        if "roster" in observations:
            try:
                roster_observed(target["roster_group"], {"observations": observations["roster"]}, owner, now=now)
            except ConnectorObservationRequired as exc:
                requests["roster_next"] = exc.requests[0]
            else:
                requests.pop("roster")
        if "sharepoint" in observations:
            site = _site(observations["sharepoint"], owner, now=now)
            try:
                _libraries(site, observations.get("report_library"), owner, now=now)
            except ConnectorObservationRequired as exc:
                key = "report_library_next" if "report_library" in observations else "report_library"
                requests[key] = exc.requests[0]
    return {
        "required_tools": list(REQUIRED_TOOLS),
        "requests": requests,
        "structured_fetch_required": True,
        "routing": (
            "Inspect actual MCP fetch tools, then this user's native WorkIQ fetch --help. "
            "A missing custom remote registration is not a missing structured-read capability."),
        "permission_to_send": False, "permission_to_upload": False,
        "limitations": (
            "Read probes and discovered write tools do not prove permission to send/upload. "
            "Check access and authorization at each owning write step, not through a blanket "
            "entry attestation. A future Bug Bash event/chat must be verified "
            "when created. Exact group/member and site/library reads must succeed through "
            "the current user's verified structured fetch route, not WorkIQ Ask or people search. "
            "Immutable report creation uses a separately owned, approved SharePoint browser upload."
        ),
    }


def plan(state, evidence=None):
    observations = None
    if evidence is not None:
        if not isinstance(evidence, dict) or evidence.get("binding") != binding(state):
            raise ValueError("Capability follow-up evidence belongs to another release/owner/configuration")
        observations = evidence.get("observations") or {}
    return {"binding": binding(state), **read_plan(state.owner_email, observations)}


def missing_tools(available, *, structured_fetch=None, now=None):
    if (not isinstance(available, dict)
            or any(not isinstance(key, str) or not key.strip()
                   or not isinstance(value, str) or not value.strip()
                   for key, value in available.items())):
        raise ValueError("Record the actual loaded tool inventory, not server registration alone")
    missing = [tool for tool in REQUIRED_TOOLS if tool != F.FETCH_TOOL and available.get(tool) != tool]
    if F.route(available, structured_fetch, now=now) is None:
        missing.append(F.FETCH_TOOL)
    return missing


def validate(state, evidence, *, now=None):
    expected = plan(state)
    if not isinstance(evidence, dict) or evidence.get("binding") != expected["binding"]:
        raise ValueError("M365 capability evidence does not match this release/owner/CCD/target configuration")
    missing = missing_tools(
        evidence.get("available_tools"), structured_fetch=evidence.get("structured_fetch"), now=now)
    if missing:
        raise ValueError("Missing loaded connector capabilities: " + ", ".join(missing))
    return {"capability_binding": expected["binding"],
            "structured_fetch": F.route(evidence["available_tools"], evidence.get("structured_fetch"), now=now),
            **validate_reads(state.owner_email, evidence.get("observations"), now=now)}


def validate_reads(owner, observations, *, now=None):
    expected = read_plan(owner)
    if not isinstance(observations, dict):
        raise ValueError("Successful read probes are required; tool names alone are not capability proof")

    def read(key, tool=None, arguments=None):
        request = expected["requests"].get(key)
        return m365.validate_observation(
            observations.get(key), tool=tool or request["tool"],
            arguments=arguments if arguments is not None else request["arguments"],
            owner=owner, now=now)

    profile = read("profile")
    if not m365.profile_matches(profile, owner):
        raise ValueError("M365 profile does not identify the release owner")
    calendar = read("calendar")
    if not (isinstance(calendar, list) or isinstance(calendar, dict)
            and isinstance(calendar.get("events", calendar.get("value")), list)):
        raise ValueError("Calendar probe returned no structured event collection")
    target = targets()
    from tools.distribution import roster_observed
    roster, _ = roster_observed(
        target["roster_group"], {"observations": observations.get("roster")}, owner, now=now)
    if not roster:
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
    site = _site(observations.get("sharepoint"), owner, now=now)
    libraries = _libraries(site, observations.get("report_library"), owner, now=now)
    matches = [row for row in libraries if isinstance(row, dict)
               and row.get("name") == target["report_library"] and row.get("id")]
    if len(matches) != 1:
        raise ValueError("Report library did not resolve uniquely in the configured site")
    return {
        "checks": [
            {"name": "owner/calendar", "ok": True, "detail": "owner identity and event read verified"},
            {"name": "distribution roster", "ok": True, "detail": "exact group and complete member read"},
            {"name": "Teams chat/members", "ok": True, "detail": "exact representative chat and owner membership"},
            {"name": "SharePoint library", "ok": True, "detail": "exact report site and library read"},
        ],
        "limitations": expected["limitations"],
        "observation_hash": delivery.fingerprint(observations),
    }
