"""Exact event-to-thread binding; no real Graph reads, sends or release state."""
from copy import deepcopy
import json

import pytest

from orchestrator import cli, cli_common as C, delivery as D
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import activate_chat as A, bugbash_updates as BU, send_invite as S
from tests._meeting import seed_invite, meeting
from tools import bugbash_meeting as M, distribution as G


@pytest.fixture
def state():
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", readiness_signed=True,
                      owner_email="owner@example.test", timezone="America/Los_Angeles")
    for phase in Orchestrator(C.DEFAULT_CONFIG, st, mocks={}).config["phases"]:
        for spec in phase["steps"]:
            if phase["id"] == "bug_bash" and spec["id"] == "activate_chat":
                seed_invite(st)
                return st
            st.set_step(phase["id"], spec["id"], StepState(status="done"))


@pytest.fixture
def graph(state, monkeypatch):
    invite = S.delivered_invite(state)
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
    out = M.resolve(S.delivered_invite(state))
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
        M.resolve(S.delivered_invite(state))


def test_recorder_binds_verified_thread_and_replay_cannot_replace_it(state, graph, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = ["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id]
    assert cli.main(args + ["--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert not ReleaseState.load(str(path)).is_done("bug_bash", "activate_chat")
    assert cli.main(args) == 0
    saved = ReleaseState.load(str(path))
    assert A.stored_chat_id(saved) == "19:meeting_CURRENT@thread.v2"
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
        state.get_step("bug_bash", "send_invite").data["_execution"]["id"] = "new-execution"
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
    assert A.stored_chat_id(state) is None
    assert BU.build(state).kind == "blocked"


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
             "state_matches": A.chat_state_matches(state)}
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    item = D.descriptor(state, "update", scope, "workiq_send_chat_message",
                        {"chatId": A.stored_chat_id(state), "content": "test"})
    D.offer(orch, item)
    record = state.notification_deliveries[state.get_step("bug_bash", "send_invite").data[
        "_execution"]["notification_id"]]
    record["attempts"][0]["receipt"]["id"] = "new-event"
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


def test_poller_rejects_stale_chat_before_gather(state, tmp_path, monkeypatch, capsys):
    seed_invite(state, "19:meeting_CURRENT@thread.v2")
    state.get_step("bug_bash", "activate_chat").data.pop("invite")
    state.save(str(tmp_path / state.release_id / "release-state.json"))
    monkeypatch.setattr(BU, "gather", lambda *_: pytest.fail("Stale chat gathered progress"))
    assert cli.main(["--runs-root", str(tmp_path), "post-bugbash-update", "--release", state.release_id,
                     "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "no_chat"
