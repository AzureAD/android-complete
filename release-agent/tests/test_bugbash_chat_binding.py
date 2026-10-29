"""Exact event-to-thread binding; no real Graph reads, sends or release state."""
from tests._context import context as _context, invoke as _invoke
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import quote

import pytest

from orchestrator import cli, cli_common as C, delivery as D
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import activate_chat as A, bugbash_updates as BU, send_invite as S
from tests._meeting import seed_invite, meeting
from tests._harness import _active_step
from tools import bugbash_meeting as M, distribution as G


@pytest.fixture
def state():
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09",
                      owner_email="owner@example.test", timezone="America/Los_Angeles")
    _active_step(st, "bug_bash", "activate_chat")
    seed_invite(st)
    return st


@pytest.fixture
def graph(state, monkeypatch):
    invite = S.delivered_invite(_context(state))
    url = "https://teams.microsoft.com/l/meetup-join/test?context=a&b=c"
    data = {
        "me": {"userPrincipalName": state.owner_email},
        "event": {"id": "event-1", "subject": invite["subject"], "isCancelled": False,
                  "isOnlineMeeting": True, "organizer": {"emailAddress": {"address": state.owner_email}},
                  "start": {"dateTime": "2026-09-11T16:00:00Z", "timeZone": "UTC"},
                  "end": {"dateTime": "2026-09-11T18:00:00Z", "timeZone": "UTC"},
                  "onlineMeeting": {"joinUrl": url}},
        "meetings": {"value": [{"id": "online-1", "joinWebUrl": url,
                               "chatInfo": {"threadId": "19:meeting_CURRENT@thread.v2"}}]},
        "chat": {"id": "19:meeting_CURRENT@thread.v2", "topic": invite["subject"], "chatType": "meeting"},
    }
    calls = []
    def get(url, timeout):
        calls.append(url)
        key = "event" if "/events/" in url else "meetings" if "/onlineMeetings?" in url else (
            "chat" if "/chats/" in url else "me")
        return True, deepcopy(data[key]), ""
    monkeypatch.setattr(G, "_graph_get", get)
    return data, calls


def test_resolve_event_to_join_url_to_thread_without_title_search(state, graph):
    out = M.resolve(S.delivered_invite(_context(state)))
    assert out["chat_id"] == graph[0]["chat"]["id"]
    assert len(graph[1]) == 4
    assert "/me/events/event-1?" in graph[1][1]
    assert "JoinWebUrl%20eq%20" in graph[1][2]


def test_graph_access_failure_does_not_fall_back_to_pasted_chat(state, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_graph_get", lambda *args: (False, None, "HTTP 403"))
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
                     "--transport", "graph",
                     "--chat-id", "19:meeting_CURRENT@thread.v2"]) == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", ["wrong_user", "wrong_event", "wrong_subject", "wrong_organizer",
                                 "cancelled", "time_changed", "no_join", "ambiguous",
                                 "wrong_join", "no_thread", "old_chat", "nonmeeting"])
def test_resolver_rejects_wrong_or_ambiguous_meeting(state, graph, bad):
    data, _ = graph
    if bad == "wrong_user":
        data["me"]["userPrincipalName"] = "other@example.test"
    elif bad == "wrong_event":
        data["event"]["id"] = "other"
    elif bad == "wrong_subject":
        data["event"]["subject"] = "September 2026 Release Bug Bash"
    elif bad == "wrong_organizer":
        data["event"]["organizer"]["emailAddress"]["address"] = "other@example.test"
    elif bad == "cancelled":
        data["event"]["isCancelled"] = True
    elif bad == "time_changed":
        data["event"]["start"]["dateTime"] = "2026-09-11T09:00:00Z"
    elif bad == "no_join":
        data["event"]["onlineMeeting"] = {}
    elif bad == "ambiguous":
        data["meetings"]["value"] *= 2
    elif bad == "wrong_join":
        data["meetings"]["value"][0]["joinWebUrl"] = "https://teams.microsoft.com/other"
    elif bad == "no_thread":
        data["meetings"]["value"][0]["chatInfo"] = {}
    elif bad == "old_chat":
        data["chat"]["topic"] = "September 2026 Release Bug Bash"
    else:
        data["chat"]["chatType"] = "group"
    with pytest.raises(ValueError):
        M.resolve(S.delivered_invite(_context(state)))


def test_recorder_binds_verified_thread_and_replay_cannot_replace_it(state, graph, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = ["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
            "--transport", "graph"]
    assert cli.main(args + ["--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert not ReleaseState.load(str(path)).is_done("bug_bash", "activate_chat")
    assert cli.main(args) == 0
    saved = ReleaseState.load(str(path))
    assert A.stored_chat_id(_context(saved)) == "19:meeting_CURRENT@thread.v2"
    before = path.read_bytes()
    assert cli.main(args) == 0
    assert cli.main(args + ["--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert path.read_bytes() == before
    assert "reopen" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["early", "no_receipt", "uncertain", "reopened_invite",
                                    "other_event", "changed_owner", "changed_month", "bare_chat"])
def test_stale_binding_blocks_initial_updates_before_progress_reads(state, monkeypatch, change):
    seed_invite(state, "19:meeting_CURRENT@thread.v2")
    record = next(iter(state.notification_deliveries.values()))
    if change == "early":
        state.steps.pop("bug_bash.send_invite")
    elif change == "no_receipt":
        record["attempts"][-1].pop("receipt")
    elif change == "uncertain":
        record["status"] = "uncertain"
    elif change == "reopened_invite":
        invite = state.get_step("bug_bash", "send_invite")
        invite.data["notification_execution_id"] = "new-execution"
        state.set_step("bug_bash", "send_invite", invite)
    elif change == "other_event":
        record["attempts"][-1]["receipt"]["id"] = "new-event"
    elif change == "changed_owner":
        state.owner_email = "another@example.test"
    elif change == "changed_month":
        state.target_month = "2026-11"
    else:
        state.set_step("bug_bash", "activate_chat", StepState(status="done", data={
            "chat_id": "19:meeting_CURRENT@thread.v2"}))
    def forbidden(*args):
        pytest.fail("Progress must not be read for an unbound/stale destination")
    monkeypatch.setattr(BU, "gather", forbidden)
    assert A.stored_chat_id(_context(state)) is None
    assert _invoke(BU.build, state).kind == "blocked"


def test_recorder_enforces_predecessors_without_writing(state, tmp_path, monkeypatch):
    state.steps.pop("bug_bash.send_invite")
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    monkeypatch.setattr(M, "resolve", lambda *_: pytest.fail("Out of order Graph resolution"))
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
                     "--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert path.read_bytes() == before


def test_prepared_update_stops_if_invite_changes_even_when_chat_id_does_not(state):
    seed_invite(state, "19:meeting_CURRENT@thread.v2")
    scope = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates",
             "state_matches": A.chat_state_matches(_context(state))}
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    item = D.descriptor(state, "update", scope, "workiq_send_chat_message",
                        {"chatId": A.stored_chat_id(_context(state)), "content": "test"})
    D.offer(orch, item)
    record = state.notification_deliveries[
        state.get_step("bug_bash", "send_invite").data["notification_id"]]
    record["attempts"][0]["receipt"]["id"] = "new-event"
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


def test_poller_rejects_stale_chat_before_gather(state, tmp_path, monkeypatch, capsys):
    seed_invite(state, "19:meeting_CURRENT@thread.v2")
    binding = state.get_step("bug_bash", "activate_chat")
    binding.data.pop("invite")
    state.set_step("bug_bash", "activate_chat", binding)
    state.save(str(tmp_path / state.release_id / "release-state.json"))
    monkeypatch.setattr(BU, "gather", lambda *_: pytest.fail("Stale chat gathered progress"))
    assert cli.main(["--runs-root", str(tmp_path), "post-bugbash-update", "--release", state.release_id,
                     "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "no_chat"


@pytest.fixture
def mcp_observation(state):
    invite = S.delivered_invite(_context(state))
    owner_id = "00000000-0000-0000-0000-000000000001"
    thread = "19:meeting_CURRENT@thread.v2"
    join = ("https://teams.microsoft.com/l/meetup-join/" + quote(thread, safe="")
            + "/0?context=" + quote(json.dumps({"Oid": owner_id}), safe=""))
    return {
        "version": 1,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "profile": {"userPrincipalName": state.owner_email, "id": owner_id},
        "event": {
            "id": invite["event_id"], "subject": invite["subject"], "isOnlineMeeting": True,
            "organizer": {"address": state.owner_email},
            "start": {"dateTime": "2026-09-11T16:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-09-11T18:00:00.0000000", "timeZone": "UTC"},
        },
        "calendar": {"events": [{
            "id": invite["event_id"], "subject": invite["subject"], "isCancelled": False,
            "start": "2026-09-11T09:00:00", "end": "2026-09-11T11:00:00",
            "timeZone": "America/Los_Angeles",
        }]},
        "online_meeting": {"available": False, "reason": "not_ready_or_not_transcribed",
                           "meeting": {"calendarEventId": invite["event_id"],
                                       "subject": invite["subject"], "onlineMeetingId": "online-1",
                                       "joinWebUrl": join}},
        "chat": {"id": thread, "topic": invite["subject"], "chatType": "meeting"},
    }


def test_mcp_verifies_exact_event_and_chat_without_transcript_or_graph(state, mcp_observation, monkeypatch):
    monkeypatch.setattr(G, "_graph_get", lambda *a: pytest.fail("MCP must not read Graph"))
    result = M.resolve_mcp(S.delivered_invite(_context(state)), mcp_observation)
    assert result["chat_id"] == mcp_observation["chat"]["id"]
    assert result["event_id"] == "event-1"
    assert result["online_meeting_id"] == "online-1"
    assert result["verified_via"] == "workiq_mcp"


@pytest.mark.parametrize("bad", [
    "schema", "stale", "future", "no_timezone", "wrong_user", "no_owner_id",
    "wrong_event", "wrong_subject", "wrong_organizer", "not_online", "time_changed",
    "cancelled", "missing_cancelled", "no_calendar_event", "duplicate_calendar_event",
    "calendar_time_changed", "partial_calendar", "no_meeting", "meeting_error",
    "wrong_meeting_event", "wrong_meeting_subject", "no_meeting_id", "no_join",
    "wrong_host", "bad_thread", "wrong_join_owner", "old_chat", "nonmeeting", "wrong_chat",
])
def test_mcp_rejects_incomplete_stale_or_mismatched_proof(state, mcp_observation, bad):
    proof = mcp_observation
    if bad == "schema":
        proof["version"] = 2
    elif bad in ("stale", "future", "no_timezone"):
        dt = datetime.now(timezone.utc)
        proof["observed_at"] = (
            (dt - timedelta(minutes=6)).isoformat() if bad == "stale"
            else (dt + timedelta(minutes=1)).isoformat() if bad == "future"
            else dt.replace(tzinfo=None).isoformat())
    elif bad == "wrong_user":
        proof["profile"]["userPrincipalName"] = "other@example.test"
    elif bad == "no_owner_id":
        proof["profile"].pop("id")
    elif bad == "wrong_event":
        proof["event"]["id"] = "different-event"
    elif bad == "wrong_subject":
        proof["event"]["subject"] = "Other release"
    elif bad == "wrong_organizer":
        proof["event"]["organizer"]["address"] = "other@example.test"
    elif bad == "not_online":
        proof["event"]["isOnlineMeeting"] = False
    elif bad == "time_changed":
        proof["event"]["start"]["dateTime"] = "2026-09-11T09:00:00Z"
    elif bad == "cancelled":
        proof["calendar"]["events"][0]["isCancelled"] = True
    elif bad == "missing_cancelled":
        proof["calendar"]["events"][0].pop("isCancelled")
    elif bad == "no_calendar_event":
        proof["calendar"]["events"] = []
    elif bad == "duplicate_calendar_event":
        proof["calendar"]["events"] *= 2
    elif bad == "calendar_time_changed":
        proof["calendar"]["events"][0]["end"] = "2026-09-11T12:00:00"
    elif bad == "partial_calendar":
        proof["calendar"]["nextLink"] = "https://graph.microsoft.com/next"
    elif bad == "no_meeting":
        proof["online_meeting"].pop("meeting")
    elif bad == "meeting_error":
        proof["online_meeting"]["error"] = "Access denied"
    elif bad == "wrong_meeting_event":
        proof["online_meeting"]["meeting"]["calendarEventId"] = "different-event"
    elif bad == "wrong_meeting_subject":
        proof["online_meeting"]["meeting"]["subject"] = "Other release"
    elif bad == "no_meeting_id":
        proof["online_meeting"]["meeting"]["onlineMeetingId"] = ""
    elif bad == "no_join":
        proof["online_meeting"]["meeting"].pop("joinWebUrl")
    elif bad == "wrong_host":
        proof["online_meeting"]["meeting"]["joinWebUrl"] = proof["online_meeting"]["meeting"]["joinWebUrl"].replace(
            "teams.microsoft.com", "example.test")
    elif bad == "bad_thread":
        proof["online_meeting"]["meeting"]["joinWebUrl"] = proof["online_meeting"]["meeting"]["joinWebUrl"].replace(
            "meeting_CURRENT", "not_a_meeting")
    elif bad == "wrong_join_owner":
        proof["profile"]["id"] = "00000000-0000-0000-0000-000000000002"
    elif bad == "old_chat":
        proof["chat"]["topic"] = "Other release"
    elif bad == "nonmeeting":
        proof["chat"]["chatType"] = "group"
    else:
        proof["chat"]["id"] = "19:meeting_OTHER@thread.v2"
    with pytest.raises(ValueError):
        M.resolve_mcp(S.delivered_invite(_context(state)), proof)


def test_default_recorder_requests_fresh_mcp_reads_without_state_changes(state, tmp_path, monkeypatch, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    monkeypatch.setattr(M, "resolve", lambda *a: pytest.fail("Graph must be explicit opt-in"))
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat",
                     "--release", state.release_id]) == 0
    request = json.loads(capsys.readouterr().out)
    assert request["decision"] == "needs_mcp_evidence"
    assert request["reads"][1]["payload"]["eventId"] == "event-1"
    assert path.read_bytes() == before


def test_mcp_recorder_stores_binding_only_after_full_validation(state, mcp_observation, tmp_path, monkeypatch):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    proof_path = tmp_path / "meeting-mcp.json"
    proof_path.write_text(json.dumps(mcp_observation), encoding="utf-8")
    monkeypatch.setattr(M, "resolve", lambda *a: pytest.fail("MCP must not use Graph"))
    args = ["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
            "--mcp-evidence-file", str(proof_path)]
    before = path.read_bytes()
    assert cli.main(args + ["--chat-id", "19:meeting_OTHER@thread.v2"]) == 1
    assert path.read_bytes() == before
    assert cli.main(args) == 0
    saved = ReleaseState.load(str(path))
    assert A.stored_chat_id(_context(saved)) == mcp_observation["chat"]["id"]
    before = path.read_bytes()
    assert cli.main(args) == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("proof", ["missing", "malformed", "incomplete"])
def test_bad_mcp_evidence_file_never_changes_release_state(state, tmp_path, proof):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    proof_path = tmp_path / "meeting-mcp.json"
    if proof != "missing":
        proof_path.write_text("{" if proof == "malformed" else "{}", encoding="utf-8")
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
                     "--mcp-evidence-file", str(proof_path)]) == 1
    assert path.read_bytes() == before
