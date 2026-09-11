"""Resolve the exact chat for an acknowledged calendar event. Read-only Graph calls."""
from datetime import datetime, timezone
import re
from urllib.parse import quote, urlparse

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
