"""Verify the acknowledged event's meeting chat using MCP evidence or Graph."""
from datetime import datetime, timezone
import json
import re
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import UUID

from orchestrator import schedule
from tools import distribution as G

GRAPH = "https://graph.microsoft.com/v1.0"


def meeting_chat_id(value):
    return isinstance(value, str) and re.fullmatch(r"19:meeting_[A-Za-z0-9_-]+@thread\.v2", value) is not None


def _instant(value, zone):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        tz = schedule.get_tz(zone)
        if tz is None:
            raise ValueError(f"Timezone unavailable: {zone}")
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def mcp_request(invite):
    """Gather only exact-event evidence; transcript content is never requested."""
    return {
        "decision": "needs_mcp_evidence",
        "event_id": invite["event_id"],
        "owner": invite["owner"],
        "evidence_schema": {
            "version": 1,
            "observed_at": "<UTC timestamp of these fresh reads>",
            "profile": "<workiq_get_my_profile response>",
            "event": "<workiq_get_event response>",
            "calendar": "<workiq_list_events response>",
            "online_meeting": "<workiq_list_meeting_transcripts response, metadata only>",
            "chat": "<workiq_get_chat response>",
        },
        "reads": [
            {"tool": "workiq_get_my_profile", "payload": {}},
            {"tool": "workiq_get_event", "payload": {
                "eventId": invite["event_id"],
                "select": ["id", "subject", "organizer", "start", "end", "isOnlineMeeting", "isCancelled"],
            }},
            {"tool": "workiq_list_events", "payload": {
                "startDate": _instant(invite["start"], invite["timeZone"]).isoformat(),
                "endDate": _instant(invite["end"], invite["timeZone"]).isoformat(),
                "limit": 50,
                "query": {
                    "filter": "isCancelled eq false and subject eq '"
                              + invite["subject"].replace("'", "''") + "'",
                    "select": ["id", "subject", "start", "end", "isCancelled"],
                },
            }},
            {"tool": "workiq_list_meeting_transcripts", "payload": {
                "calendarEventId": invite["event_id"], "limit": 1,
            }},
        ],
        "instructions": (
            "Read all responses freshly as the organizer. The transcript-list tool is used "
            "only to resolve canonical meeting metadata; do not read transcript content. "
            "Derive the meeting thread from that response's meeting.joinWebUrl, then call "
            "workiq_get_chat with that exact thread. Keep each response intact in its "
            "evidence_schema field, without external_content wrappers. Do not invent missing "
            "fields. Save the envelope outside release state and supply --mcp-evidence-file. "
            "The engine independently checks owner, event, cancellation, times, canonical "
            "online meeting, join URL and actual chat. No title searches or bare chat IDs."
        ),
    }


def _observed_time(event, key):
    actual = event.get(key)
    if isinstance(actual, dict):
        value, zone = actual.get("dateTime"), actual.get("timeZone")
    else:
        value, zone = actual, event.get("timeZone")
    if not isinstance(value, str) or not value or not isinstance(zone, str) or not zone:
        raise ValueError(f"Missing observed invitation {key}/timezone")
    return _instant(value, zone)


def resolve_mcp(invite, observation):
    """Validate fresh, complete MCP responses without a separate Graph credential."""
    fields = {"version", "observed_at", "profile", "event", "calendar", "online_meeting", "chat"}
    if (not isinstance(observation, dict) or set(observation) != fields
            or type(observation["version"]) is not int or observation["version"] != 1):
        raise ValueError("Expected version-1 complete meeting MCP observation")
    stamp = observation["observed_at"]
    if not isinstance(stamp, str):
        raise ValueError("Meeting MCP observation requires a UTC timestamp")
    try:
        observed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid meeting MCP observation timestamp") from exc
    if (observed_at.tzinfo is None
            or not 0 <= (datetime.now(timezone.utc) - observed_at).total_seconds() <= 300):
        raise ValueError("Meeting MCP observation must be fresh (at most five minutes old)")
    if any(not isinstance(observation[key], dict) for key in fields - {"version", "observed_at"}):
        raise ValueError("Meeting MCP responses must be JSON objects")
    profile, event = observation["profile"], observation["event"]
    owner = invite["owner"].casefold()
    if str(profile.get("userPrincipalName") or "").casefold() != owner:
        raise ValueError("Run chat resolution as the invitation's organizer")
    try:
        owner_id = str(UUID(profile.get("id", "")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Missing organizer Entra identity in MCP profile") from exc
    organizer = event.get("organizer")
    organizer_mail = organizer.get("address") if isinstance(organizer, dict) else None
    if (event.get("id") != invite["event_id"] or event.get("subject") != invite["subject"]
            or event.get("isOnlineMeeting") is not True or event.get("isCancelled") is True
            or str(organizer_mail or "").casefold() != owner):
        raise ValueError("MCP calendar event is not the acknowledged release invitation")
    for key in ("start", "end"):
        if _observed_time(event, key) != _instant(invite[key], invite["timeZone"]):
            raise ValueError("Invitation time changed; owner must reconcile the event before binding its chat")
    calendar = observation["calendar"]
    events = calendar.get("events")
    if (not isinstance(events, list) or calendar.get("@odata.nextLink")
            or calendar.get("nextLink") or calendar.get("hasMoreResults")):
        raise ValueError("Expected complete calendar evidence for the invitation")
    matches = [row for row in events if isinstance(row, dict) and row.get("id") == invite["event_id"]]
    if (len(matches) != 1 or matches[0].get("isCancelled") is not False
            or matches[0].get("subject") != invite["subject"]):
        raise ValueError("The exact invitation must be present and explicitly not cancelled")
    for key in ("start", "end"):
        if _observed_time(matches[0], key) != _observed_time(event, key):
            raise ValueError("Calendar observations disagree about invitation times")
    metadata = observation["online_meeting"]
    meeting = metadata.get("meeting")
    if (metadata.get("error") or not isinstance(meeting, dict)
            or meeting.get("calendarEventId") != invite["event_id"]
            or meeting.get("subject") != invite["subject"]
            or not isinstance(meeting.get("onlineMeetingId"), str)
            or not meeting["onlineMeetingId"].strip()):
        raise ValueError("Missing canonical online-meeting metadata for the exact invitation")
    join = meeting.get("joinWebUrl")
    if not isinstance(join, str):
        raise ValueError("Canonical meeting has no Teams join URL")
    parsed = urlparse(join)
    parts = parsed.path.split("/")
    if (parsed.scheme != "https" or parsed.netloc != "teams.microsoft.com" or parsed.fragment
            or len(parts) != 5 or parts[:3] != ["", "l", "meetup-join"] or parts[4] != "0"):
        raise ValueError("Unsupported canonical Teams meeting join URL")
    thread = unquote(parts[3])
    if not meeting_chat_id(thread):
        raise ValueError("Canonical Teams join URL has no verifiable meeting thread")
    contexts = parse_qs(parsed.query).get("context", [])
    try:
        context = json.loads(contexts[0]) if len(contexts) == 1 else None
        organizer_id = str(UUID(context.get("Oid", ""))) if isinstance(context, dict) else None
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid organizer context in canonical Teams join URL") from exc
    if organizer_id != owner_id:
        raise ValueError("Canonical Teams meeting organizer does not match the signed-in owner")
    chat = observation["chat"]
    if chat.get("id") != thread or chat.get("chatType") != "meeting" or chat.get("topic") != invite["subject"]:
        raise ValueError("Observed chat is not the exact invitation's meeting thread")
    return {
        "chat_id": thread, "event_id": invite["event_id"], "join_url": join,
        "online_meeting_id": meeting["onlineMeetingId"], "subject": invite["subject"],
        "verified_via": "workiq_mcp", "observed_at": stamp,
    }


def resolve(invite, timeout=90):
    """Event ID -> join URL -> onlineMeeting.chatInfo.threadId; no title lookup."""
    def get(path):
        ok, data, detail = G._graph_get(GRAPH + path, timeout)
        if not ok:
            raise ValueError(f"Cannot verify the invitation's meeting chat: {detail}")
        if not isinstance(data, dict):
            raise ValueError("Malformed Graph meeting response")
        return data

    me = get("/me?$select=userPrincipalName")
    if me.get("userPrincipalName", "").casefold() != invite["owner"].casefold():
        raise ValueError("Run chat resolution as the invitation's organizer; do not use another mailbox.")
    event = get(f"/me/events/{quote(invite['event_id'], safe='')}?"
                "$select=id,subject,organizer,start,end,isCancelled,isOnlineMeeting,onlineMeeting")
    if (event.get("id") != invite["event_id"] or event.get("subject") != invite["subject"]
            or event.get("isCancelled") is not False or event.get("isOnlineMeeting") is not True
            or event.get("organizer", {}).get("emailAddress", {}).get("address", "").casefold()
            != invite["owner"].casefold()):
        raise ValueError("Calendar event is not the acknowledged release invitation")
    for key in ("start", "end"):
        actual = event.get(key) or {}
        if _instant(actual["dateTime"], actual["timeZone"]) != _instant(invite[key], invite["timeZone"]):
            raise ValueError("Invitation time changed; owner must reconcile the event before binding its chat")
    join = (event.get("onlineMeeting") or {}).get("joinUrl")
    if not isinstance(join, str) or urlparse(join).scheme != "https":
        raise ValueError("The acknowledged event has no Teams join URL")
    query = quote("JoinWebUrl eq '" + join.replace("'", "''") + "'", safe="")
    collection = get(f"/me/onlineMeetings?$filter={query}")
    meetings = collection.get("value")
    if (not isinstance(meetings, list) or len(meetings) != 1 or collection.get("@odata.nextLink")):
        raise ValueError("The event's join URL did not resolve to exactly one online meeting")
    meeting = meetings[0]
    if (meeting.get("joinWebUrl") != join or not isinstance(meeting.get("id"), str)
            or not meeting["id"]):
        raise ValueError("Online meeting does not match the event's join URL")
    thread = (meeting.get("chatInfo") or {}).get("threadId")
    if not meeting_chat_id(thread):
        raise ValueError("Meeting has no verifiable thread ID; open this exact event's Chat pane and retry")
    chat = get(f"/chats/{quote(thread, safe='')}?$select=id,topic,chatType")
    if chat.get("id") != thread or chat.get("chatType") != "meeting" or chat.get("topic") != invite["subject"]:
        raise ValueError("Resolved thread is not the release meeting chat")
    return {"chat_id": thread, "event_id": invite["event_id"], "join_url": join,
            "online_meeting_id": meeting["id"], "subject": invite["subject"]}
