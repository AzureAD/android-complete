"""Exact invitation-to-chat binding from native connector observations or owner attestation."""
from datetime import datetime, timezone
from collections import Counter
from os import PathLike
import re
from urllib.parse import unquote, urlparse

from orchestrator import delivery, schedule
from orchestrator.m365 import observation_request, profile_matches, read_observation, validate_observation

PROFILE_TOOL = "workiq_get_my_profile"
EVENT_TOOL = "workiq_get_event"
CHAT_TOOL = "microsoft_teams-GetChat"
EVENT_FIELDS = ["id", "subject", "organizer", "start", "end", "isCancelled",
                "isOnlineMeeting", "onlineMeeting", "attendees"]


class ConnectorEvidenceRequired(ValueError):
    """Read-only handoff; neither observations nor a request authorize a send."""

    def __init__(self, requests, *, missing_capability=None):
        super().__init__(missing_capability or "Native connector observations are required")
        self.requests = requests
        self.missing_capability = missing_capability


def event_arguments(invite):
    return {"eventId": invite["event_id"], "select": list(EVENT_FIELDS)}


def _request(tool, arguments, owner, flag):
    return {**observation_request(tool, arguments, owner), "observation_flag": flag}


def _observation(value, *, tool, arguments, owner, now):
    kwargs = {"tool": tool, "arguments": arguments, "owner": owner, "now": now}
    result = (read_observation(value, **kwargs) if isinstance(value, (str, PathLike))
              else validate_observation(value, **kwargs))
    if not isinstance(result, dict):
        raise ValueError(f"{tool} must return one complete structured object")
    return result


def meeting_chat_id(value):
    return isinstance(value, str) and re.fullmatch(r"19:meeting_[A-Za-z0-9_-]+@thread\.v2", value) is not None


def chat_id_from_link(link):
    """Accept only explicit Teams meeting-chat, message or meetup deep links."""
    if not isinstance(link, str) or link != link.strip():
        raise ValueError("Supply the Teams chat link copied from the exact invitation's Chat pane")
    parsed = urlparse(link)
    if (parsed.scheme != "https" or parsed.hostname not in (
            "teams.microsoft.com", "teams.cloud.microsoft", "teams.live.com")
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Expected an HTTPS Teams meeting-chat link, without credentials")
    parts = parsed.path.split("/")
    thread = unquote(parts[3]) if len(parts) >= 5 and parts[1:3] in (
        ["l", "chat"], ["l", "message"], ["l", "meetup-join"]) else None
    if not meeting_chat_id(thread):
        raise ValueError("Link must contain a meeting thread ID; copy the meeting chat/message link")
    return thread


def manual_candidate(invite, link):
    """Build an owner-review candidate, never a fabricated Graph verification."""
    thread = chat_id_from_link(link)
    join = invite.get("join_url")
    if join:
        parts = urlparse(join).path.split("/")
        if len(parts) >= 5 and parts[1:3] == ["l", "meetup-join"]:
            receipted_thread = unquote(parts[3])
            if meeting_chat_id(receipted_thread) and receipted_thread != thread:
                raise ValueError("Chat link conflicts with the acknowledged invitation's meeting thread")
    candidate = {
        "verification": "owner_attested", "event_id": invite["event_id"],
        "subject": invite["subject"], "chat_id": thread, "chat_link": link,
        "invite_hash": delivery.fingerprint(invite),
        **({"join_url": join} if join else {}),
    }
    return {**candidate, "review_hash": delivery.fingerprint(candidate)}


def manual_matches(invite, meeting, chat_id):
    candidate = manual_candidate(invite, meeting.get("chat_link"))
    return (all(meeting.get(key) == value for key, value in candidate.items())
            and chat_id == candidate["chat_id"]
            and isinstance(meeting.get("approved_by"), str)
            and meeting["approved_by"].casefold() == invite["owner"].casefold()
            and isinstance(meeting.get("reason"), str) and bool(meeting["reason"].strip())
            and meeting.get("activated") is True)


def _instant(value, zone):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        tz = schedule.get_tz(zone)
        if tz is None:
            raise ValueError(f"Timezone unavailable: {zone}")
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _address(value):
    if (not isinstance(value, str)
            or re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value) is None):
        raise ValueError("Missing or malformed invitation attendee address")
    return value.casefold()


def _verify_attendees(invite, event):
    if (not isinstance(event.get("attendees"), list)
            or "attendees@odata.nextLink" in event or "@odata.nextLink" in event):
        raise ValueError("Missing or partial invitation attendees; owner recovery required")
    actual = Counter()
    for attendee in event["attendees"]:
        if (not isinstance(attendee, dict) or attendee.get("type") not in ("required", "optional", "resource")
                or not isinstance(attendee.get("emailAddress"), dict)):
            raise ValueError("Malformed invitation attendee identity/type")
        actual[(attendee["type"], _address(attendee["emailAddress"].get("address")))] += 1
    if "attendees@odata.count" in event:
        count = event["attendees@odata.count"]
        if type(count) is not int or count != len(event["attendees"]):
            raise ValueError("Partial invitation attendee list")
    expected = Counter()
    for key, kind in (("attendees", "required"), ("optionalAttendees", "optional"),
                      ("resourceAttendees", "resource")):
        values = invite.get(key, [] if key != "attendees" else None)
        if not isinstance(values, list) or (key == "attendees" and not values):
            raise ValueError("Missing or malformed approved invitation recipient list")
        expected.update((kind, _address(address)) for address in values)
    if actual != expected:
        raise ValueError("Calendar event attendees changed from the acknowledged invitation")


def resolve(invite, *, profile_observation=None, event_observation=None,
            chat_observation=None, now=None):
    """Validate supplied read observations only; never obtain a token or call a provider."""
    try:
        owner = invite["owner"]
        requests = []
        for value, tool, arguments, flag in (
            (profile_observation, PROFILE_TOOL, {}, "--profile-observation"),
            (event_observation, EVENT_TOOL, event_arguments(invite), "--event-observation"),
        ):
            if value is None:
                requests.append(_request(tool, arguments, owner, flag))
        profile = (_observation(profile_observation, tool=PROFILE_TOOL, arguments={},
                                owner=owner, now=now) if profile_observation is not None else None)
        event = (_observation(event_observation, tool=EVENT_TOOL, arguments=event_arguments(invite),
                              owner=owner, now=now) if event_observation is not None else None)
        if profile is not None:
            _verify_profile(invite, profile)
        thread = _verify_event(invite, event) if event is not None else None
        if thread is not None and chat_observation is None:
            requests.append(_request(CHAT_TOOL, {"chatId": thread}, owner, "--chat-observation"))
        if requests:
            raise ConnectorEvidenceRequired(requests)
        chat = _observation(chat_observation, tool=CHAT_TOOL, arguments={"chatId": thread},
                            owner=owner, now=now)
        return _native_binding(invite, profile, event, chat)
    except (TypeError, KeyError, AttributeError, IndexError):
        raise ValueError("Malformed invitation or native connector evidence; owner recovery required") from None


def _verify_profile(invite, profile):
    if not profile_matches(profile, invite["owner"]):
        raise ValueError("Run chat resolution as the invitation's organizer; do not use another mailbox.")


def _verify_event(invite, event):
    if (event.get("id") != invite["event_id"] or event.get("subject") != invite["subject"]
            or event.get("isCancelled") is not False or event.get("isOnlineMeeting") is not True
            or event.get("organizer", {}).get("emailAddress", {}).get("address", "").casefold()
            != invite["owner"].casefold()):
        raise ValueError("Calendar event is not the acknowledged release invitation")
    _verify_attendees(invite, event)
    for key in ("start", "end"):
        actual = event.get(key) or {}
        if _instant(actual["dateTime"], actual["timeZone"]) != _instant(invite[key], invite["timeZone"]):
            raise ValueError("Invitation time changed; owner must reconcile the event before binding its chat")
    join = (event.get("onlineMeeting") or {}).get("joinUrl")
    parsed = urlparse(join) if isinstance(join, str) else None
    if (parsed is None or join != join.strip() or parsed.scheme != "https"
            or parsed.hostname not in ("teams.microsoft.com", "teams.cloud.microsoft", "teams.live.com")
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("The acknowledged event has no Teams join URL")
    if "join_url" in invite and invite["join_url"] != join:
        raise ValueError("Calendar event join URL differs from the acknowledged invitation")
    parts = parsed.path.split("/")
    thread = unquote(parts[3]) if len(parts) >= 5 and parts[1:3] == ["l", "meetup-join"] else None
    if not meeting_chat_id(thread):
        raise ConnectorEvidenceRequired([], missing_capability=(
            "The exact current event has an opaque Teams join URL with no meeting thread. "
            "No supported native onlineMeeting-to-thread resolver is available. Ask the owner "
            "to open THIS invitation's Chat pane and provide its meeting chat/message link; "
            "preview --chat-link and record only its explicitly approved --confirm-activated "
            "--review-hash. Alternatively keep held or use owner-reviewed manual posting. "
            "Do not guess a thread, search by topic, use raw Graph or create another invitation."))
    return thread


def _native_binding(invite, profile, event, chat):
    _verify_profile(invite, profile)
    thread = _verify_event(invite, event)
    if chat.get("id") != thread or chat.get("chatType") != "meeting" or chat.get("topic") != invite["subject"]:
        raise ValueError("Resolved thread is not the release meeting chat")
    profile = {key: profile[key] for key in ("id", "mail", "email", "userPrincipalName")
               if key in profile}
    event = {
        **{key: event[key] for key in ("id", "subject", "isCancelled", "isOnlineMeeting")},
        "organizer": {"emailAddress": {"address": event["organizer"]["emailAddress"]["address"]}},
        **{key: {field: event[key][field] for field in ("dateTime", "timeZone")}
           for key in ("start", "end")},
        "onlineMeeting": {"joinUrl": event["onlineMeeting"]["joinUrl"]},
        "attendees": [
            {"type": attendee["type"], "emailAddress": {"address": attendee["emailAddress"]["address"]}}
            for attendee in event["attendees"]],
    }
    chat = {key: chat[key] for key in ("id", "topic", "chatType")}
    return {"verification": "native_connectors", "chat_id": thread, "event_id": invite["event_id"],
            "join_url": event["onlineMeeting"]["joinUrl"], "subject": invite["subject"],
            "invite_hash": delivery.fingerprint(invite),
            "thread_source": "event.onlineMeeting.joinUrl",
            "connector_evidence": {
                "profile": {"tool": PROFILE_TOOL, "arguments": {}, "result": profile},
                "event": {"tool": EVENT_TOOL, "arguments": event_arguments(invite), "result": event},
                "chat": {"tool": CHAT_TOOL, "arguments": {"chatId": thread}, "result": chat},
            }}


def native_matches(invite, meeting, chat_id):
    """Recheck durable identity proof, without expiring an already-recorded observation."""
    try:
        evidence = meeting["connector_evidence"]
        expected = _native_binding(invite, evidence["profile"]["result"],
                                   evidence["event"]["result"], evidence["chat"]["result"])
        return meeting == expected and chat_id == expected["chat_id"]
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return False
