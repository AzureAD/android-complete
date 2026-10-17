"""Exact event-to-thread binding from native observations; no real provider calls."""
from tests._context import context as _context, invoke as _invoke
from copy import deepcopy
import json
from datetime import datetime, timedelta, timezone

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


@pytest.fixture(autouse=True)
def no_graph_or_azure_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Chat resolution must not acquire Graph tokens or invoke Azure CLI")
    monkeypatch.setattr(G, "_graph_get", forbidden, raising=False)
    monkeypatch.setattr(G, "_token", forbidden, raising=False)
    monkeypatch.setattr(G.P, "_az_json", forbidden, raising=False)
    original_run = G.subprocess.run
    def no_az(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and command:
            executable = str(command[0]).replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if executable in ("az", "az.cmd", "az.exe"):
                forbidden()
        return original_run(command, *args, **kwargs)
    monkeypatch.setattr(G.subprocess, "run", no_az)


def observation(tool, arguments, owner, result):
    return {"version": 1, "tool": tool, "arguments": arguments, "account": owner,
            "observed_at": datetime.now(timezone.utc).isoformat(), "result": result}


def observations(invite, data):
    return {
        "profile_observation": observation(M.PROFILE_TOOL, {}, invite["owner"], data["me"]),
        "event_observation": observation(M.EVENT_TOOL, M.event_arguments(invite), invite["owner"], data["event"]),
        "chat_observation": observation(M.CHAT_TOOL, {"chatId": "19:meeting_CURRENT@thread.v2"},
                                        invite["owner"], data["chat"]),
    }


def write_observations(root, invite, data):
    flags = []
    for name, value in observations(invite, data).items():
        path = root / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        flags += ["--" + name.replace("_", "-"), str(path)]
    return flags


@pytest.fixture
def native(state):
    invite = S.delivered_invite(_context(state))
    url = "https://teams.microsoft.com/l/meetup-join/19%3Ameeting_CURRENT%40thread.v2/0?context=a&b=c"
    data = {
        "me": {"id": "profile-1", "userPrincipalName": state.owner_email},
        "event": {"id": "event-1", "subject": invite["subject"], "isCancelled": False,
                  "isOnlineMeeting": True, "organizer": {"emailAddress": {"address": state.owner_email}},
                  "attendees": [{"type": "required", "emailAddress": {"address": "team@example.test"}}],
                  "start": {"dateTime": "2026-09-11T16:00:00Z", "timeZone": "UTC"},
                  "end": {"dateTime": "2026-09-11T18:00:00Z", "timeZone": "UTC"},
                  "onlineMeeting": {"joinUrl": url}},
        "chat": {"id": "19:meeting_CURRENT@thread.v2", "topic": invite["subject"], "chatType": "meeting"},
    }
    return data


def test_resolve_event_to_join_url_to_thread_without_title_search(state, native):
    invite = S.delivered_invite(_context(state))
    out = M.resolve(invite, **observations(invite, native))
    assert out["chat_id"] == native["chat"]["id"]
    assert out["verification"] == "native_connectors"
    assert out["connector_evidence"]["event"]["arguments"]["eventId"] == "event-1"
    assert out["connector_evidence"]["chat"]["tool"] == "microsoft_teams-GetChat"
    assert "online_meeting_id" not in out
    assert A.meeting_matches(invite, out, out["chat_id"])


def test_native_profile_accepts_owner_upn_when_smtp_mail_differs(state, native):
    invite = S.delivered_invite(_context(state))
    native["me"].update(mail="owner.smtp@example.test", email="owner.smtp@example.test")
    bound = M.resolve(invite, **observations(invite, native))
    assert bound["connector_evidence"]["profile"]["result"] == native["me"]
    assert A.meeting_matches(invite, bound, bound["chat_id"])


@pytest.mark.parametrize("identity", [None, "", " "])
def test_native_profile_requires_nonempty_provider_identity(state, native, identity):
    invite = S.delivered_invite(_context(state))
    native["me"]["id"] = identity
    with pytest.raises(ValueError):
        M.resolve(invite, **observations(invite, native))


def test_persisted_native_evidence_contains_only_necessary_contract_fields(state, native):
    invite = S.delivered_invite(_context(state))
    native["me"].update(displayName="Private Name", businessPhones=["private"], department="private")
    native["event"].update(body={"content": "private"}, attachments=[{"name": "private"}])
    native["event"]["attendees"][0].update(status={"response": "accepted"}, displayName="Private Name")
    native["event"]["attendees"][0]["emailAddress"]["name"] = "Private Name"
    native["event"]["organizer"]["emailAddress"]["name"] = "Private Name"
    native["event"]["onlineMeeting"]["privateExtra"] = "private"
    native["chat"].update(body="private", members=[{"private": True}])
    before = deepcopy(native)
    bound = M.resolve(invite, **observations(invite, native))
    evidence = bound["connector_evidence"]
    assert evidence["profile"]["result"] == {
        "id": "profile-1", "userPrincipalName": state.owner_email}
    assert set(evidence["event"]["result"]) == set(M.EVENT_FIELDS)
    assert evidence["event"]["result"]["attendees"] == [
        {"type": "required", "emailAddress": {"address": "team@example.test"}}]
    assert evidence["event"]["result"]["organizer"] == {
        "emailAddress": {"address": state.owner_email}}
    assert evidence["event"]["result"]["onlineMeeting"] == {
        "joinUrl": native["event"]["onlineMeeting"]["joinUrl"]}
    assert set(evidence["chat"]["result"]) == {"id", "topic", "chatType"}
    assert "private" not in json.dumps(bound)
    assert native == before
    assert A.meeting_matches(invite, bound, bound["chat_id"])


def test_missing_observations_print_exact_read_requests_without_recording(state, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
                     "--chat-id", "19:meeting_CURRENT@thread.v2"]) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert handoff["status"] == "needs-connector-evidence"
    assert handoff["completed"] is False and handoff["permission_to_send"] is False
    assert [(request["tool"], request["arguments"]) for request in handoff["requests"]] == [
        (M.PROFILE_TOOL, {}), (M.EVENT_TOOL, M.event_arguments(S.delivered_invite(_context(state))))]
    assert all(request["read_only"] for request in handoff["requests"])
    assert "--chat-observation" in handoff["followup_flags"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", ["wrong_user", "wrong_event", "wrong_subject", "wrong_organizer",
                                 "cancelled", "time_changed", "no_join",
                                 "wrong_join", "no_thread", "old_chat", "wrong_chat", "nonmeeting"])
def test_resolver_rejects_wrong_or_ambiguous_meeting(state, native, bad):
    data = native
    invite = S.delivered_invite(_context(state))
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
    elif bad == "wrong_join":
        invite["join_url"] = data["event"]["onlineMeeting"]["joinUrl"] + "&changed=1"
    elif bad == "no_thread":
        data["event"]["onlineMeeting"]["joinUrl"] = "https://teams.microsoft.com/meet/123"
    elif bad == "old_chat":
        data["chat"]["topic"] = "September 2026 Release Bug Bash"
    elif bad == "wrong_chat":
        data["chat"]["id"] = "19:meeting_OLD@thread.v2"
    else:
        data["chat"]["chatType"] = "group"
    with pytest.raises(ValueError):
        M.resolve(invite, **observations(invite, data))


def test_recorder_binds_verified_thread_and_replay_cannot_replace_it(state, native, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = ["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id]
    args += write_observations(tmp_path, S.delivered_invite(_context(state)), native)
    assert cli.main(args + ["--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert not ReleaseState.load(str(path)).is_done("bug_bash", "activate_chat")
    assert cli.main(args) == 0
    saved = ReleaseState.load(str(path))
    assert A.stored_chat_id(_context(saved)) == "19:meeting_CURRENT@thread.v2"
    assert saved.get_step("bug_bash", A.ID).data["meeting"]["verification"] == "native_connectors"
    assert saved.notification_deliveries == state.notification_deliveries
    before = path.read_bytes()
    assert cli.main(args) == 0
    assert cli.main(args + ["--chat-id", "19:meeting_OLD@thread.v2"]) == 1
    assert path.read_bytes() == before
    assert "reopen" in capsys.readouterr().out


def test_valid_current_event_requests_only_its_exact_chat(state, native, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    invite = S.delivered_invite(_context(state))
    flags = write_observations(tmp_path, invite, native)
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat",
                     "--release", state.release_id] + flags[:-2]) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert len(handoff["requests"]) == 1
    assert handoff["requests"][0]["tool"] == M.CHAT_TOOL
    assert handoff["requests"][0]["arguments"] == {"chatId": "19:meeting_CURRENT@thread.v2"}
    assert not handoff["completed"] and path.read_bytes() == before


def test_opaque_join_requests_manual_recovery_not_a_guessed_provider(state, native, tmp_path, capsys):
    native["event"]["onlineMeeting"]["joinUrl"] = "https://teams.microsoft.com/meet/123?p=opaque"
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    flags = write_observations(tmp_path, S.delivered_invite(_context(state)), native)
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat",
                     "--release", state.release_id] + flags) == 0
    handoff = json.loads(capsys.readouterr().out)
    assert handoff["status"] == "needs-connector-evidence" and not handoff["completed"]
    assert handoff["requests"] == []
    assert "No supported native" in handoff["missing_capability"]
    assert "--chat-link" in handoff["missing_capability"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    "tool", "event_args", "chat_args", "account", "version", "stale", "future",
    "unbound_time", "raw", "summary", "error",
])
def test_native_observations_require_exact_fresh_owner_bound_read_evidence(state, native, change):
    invite = S.delivered_invite(_context(state))
    evidence = observations(invite, native)
    envelope = evidence["event_observation"]
    if change == "tool":
        envelope["tool"] = "workiq_create_event"
    elif change == "event_args":
        envelope["arguments"]["eventId"] = "other"
    elif change == "chat_args":
        evidence["chat_observation"]["arguments"]["chatId"] = "19:meeting_OLD@thread.v2"
    elif change == "account":
        envelope["account"] = "other@example.test"
    elif change == "version":
        envelope["version"] = True
    elif change in ("stale", "future"):
        offset = -901 if change == "stale" else 60
        envelope["observed_at"] = (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()
    elif change == "unbound_time":
        envelope["observed_at"] = "2026-10-16T10:00:00"
    elif change == "raw":
        evidence["event_observation"] = native["event"]
    elif change == "summary":
        envelope["result"] = "I checked the event and chat"
    else:
        envelope["result"] = {"error": "HTTP 403"}
    with pytest.raises(ValueError):
        M.resolve(invite, **evidence)


def test_provider_denial_holds_state_and_offers_manual_recovery(state, native, tmp_path, capsys):
    native["event"] = {"error": "HTTP 403"}
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    flags = write_observations(tmp_path, S.delivered_invite(_context(state)), native)
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat",
                     "--release", state.release_id] + flags) == 1
    assert "--chat-link" in capsys.readouterr().out
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", [None, "not-json", '{"version":1}'])
def test_unusable_observation_files_do_not_change_state(state, tmp_path, contents):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    profile = tmp_path / "profile-observation.json"
    if contents is not None:
        profile.write_text(contents, encoding="utf-8")
    assert cli.main(["--runs-root", str(tmp_path), "record-bugbash-chat",
                     "--release", state.release_id, "--profile-observation", str(profile)]) == 1
    assert path.read_bytes() == before


def test_native_proof_is_frozen_into_notification_claims(state, native):
    invite = S.delivered_invite(_context(state))
    binding = M.resolve(invite, **observations(invite, native))
    state.set_step("bug_bash", A.ID, StepState(status="done", data={
        "chat_id": binding["chat_id"], "invite": invite, "meeting": binding,
    }))
    assert A.stored_chat_id(_context(state)) == binding["chat_id"]
    scope = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates",
             "state_matches": A.chat_state_matches(_context(state))}
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    item = D.descriptor(state, "native-update", scope, "workiq_send_chat_message",
                        {"chatId": binding["chat_id"], "content": "test"})
    D.offer(orch, item)
    event = state.steps["bug_bash.activate_chat"]["data"]["meeting"]["connector_evidence"]["event"]
    event["result"]["attendees"].append(
        {"type": "required", "emailAddress": {"address": "unexpected@example.test"}})
    assert A.stored_chat_id(_context(state)) is None
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


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
    monkeypatch.setattr(M, "resolve", lambda *_, **__: pytest.fail("Out of order native resolution"))
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


CHAT_LINK = "https://teams.microsoft.com/l/message/19%3Ameeting_CURRENT%40thread.v2/123"


def manual_args(state, tmp_path):
    return ["--runs-root", str(tmp_path), "record-bugbash-chat", "--release", state.release_id,
            "--chat-link", CHAT_LINK]


def confirmation(state, review):
    return ["--confirm-activated", "--review-hash", review["review_hash"],
            "--approved-by", state.owner_email, "--reason", "I opened this invite's chat and copied its link"]


def test_manual_preview_is_read_only_and_does_not_need_graph(state, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(G, "_graph_get", lambda *_: pytest.fail("Manual recovery must not require Graph"))
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    assert cli.main(manual_args(state, tmp_path)) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "owner_attested" and not preview["permission_to_record"]
    assert preview["invitation"]["event_id"] == "event-1"
    assert "not independently verified" in preview["warning"]
    assert "online_meeting_id" not in preview
    assert path.read_bytes() == before


def test_owner_confirmed_link_persists_without_graph_and_replays_without_changes(
        state, tmp_path, monkeypatch, capsys):
    from steps.lib import mockctx
    from tests._harness import _bb_updates_state
    from tests.test_bugbash_mentions import progress, people

    monkeypatch.setattr(G, "_graph_get", lambda *_: pytest.fail("Graph remains forbidden"))
    state.pipeline_runs = _bb_updates_state().pipeline_runs
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = manual_args(state, tmp_path)
    assert cli.main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert cli.main(args + confirmation(state, preview)) == 0
    saved = ReleaseState.load(str(path))
    data = saved.get_step("bug_bash", A.ID).data
    assert saved.is_done("bug_bash", A.ID)
    assert A.stored_chat_id(_context(saved)) == "19:meeting_CURRENT@thread.v2"
    assert data["meeting"]["verification"] == "owner_attested"
    assert data["meeting"]["approved_by"] == state.owner_email
    assert "online_meeting_id" not in data["meeting"]
    assert "confirmed_at" in data and "verified_at" not in data
    assert saved.notification_deliveries == state.notification_deliveries
    members = tmp_path / "members.json"
    members.write_text(json.dumps(observation(
        "microsoft_teams-ListChatMembers", {"chatId": data["chat_id"]}, saved.owner_email, [
            {"userId": person["id"], "email": email, "displayName": person["name"]}
            for email, person in people().items()],
    )), encoding="utf-8")
    with mockctx.active({"progress": progress()}):
        update = _invoke(BU.build, saved, members_file=str(members))
    assert update.kind == "needs_skill" and update.outbound
    assert update.payload["chatId"] == data["chat_id"]
    assert update.payload["mentions"][0]["mentionText"] == "Alice & Co"
    assert not saved.is_done("bug_bash", BU.ID)
    before = path.read_bytes()
    assert cli.main(args + confirmation(state, preview)) == 0
    assert cli.main(args[:-1] + [CHAT_LINK + "?different=1"]) == 1
    assert path.read_bytes() == before


def test_manual_links_reject_nonmeeting_or_untrusted_destinations():
    for bad in (
        "19:meeting_CURRENT@thread.v2", "https://evil.example/l/chat/19:meeting_CURRENT@thread.v2/0",
        "http://teams.microsoft.com/l/chat/19:meeting_CURRENT@thread.v2/0",
        "https://user@teams.microsoft.com/l/chat/19:meeting_CURRENT@thread.v2/0",
        "https://teams.microsoft.com:444/l/chat/19:meeting_CURRENT@thread.v2/0",
        "https://teams.microsoft.com/l/chat/19:ordinary@thread.v2/0",
        "https://teams.microsoft.com/l/chat/0/0?users=other@example.test",
        CHAT_LINK + " ", "https://teams.microsoft.com/v2/", None,
    ):
        with pytest.raises(ValueError):
            M.chat_id_from_link(bad)
    for kind in ("chat", "message", "meetup-join"):
        assert M.chat_id_from_link(
            f"https://teams.cloud.microsoft/l/{kind}/19%3Ameeting_CURRENT%40thread.v2/0"
        ) == "19:meeting_CURRENT@thread.v2"


def test_manual_link_cannot_contradict_receipted_meeting_thread(state):
    invite = S.delivered_invite(_context(state))
    join = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_OTHER%40thread.v2/0"
    with pytest.raises(ValueError, match="conflicts"):
        M.manual_candidate({**invite, "join_url": join}, CHAT_LINK)
    same = join.replace("OTHER", "CURRENT")
    candidate = M.manual_candidate({**invite, "join_url": same}, CHAT_LINK)
    assert candidate["join_url"] == same
    # Modern numeric meeting links have no thread to compare: this is explicitly
    # owner attestation, not an invented onlineMeeting/chatInfo API observation.
    assert M.manual_candidate({**invite, "join_url": "https://teams.microsoft.com/meet/123"},
                              CHAT_LINK)["verification"] == "owner_attested"


def test_manual_record_requires_exact_review_owner_and_activation(state, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = manual_args(state, tmp_path)
    assert cli.main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    before = path.read_bytes()
    valid = confirmation(state, preview)
    for flags in (
        valid[1:], ["--confirm-activated"],
        ["--confirm-activated", "--review-hash", preview["review_hash"], "--reason", "confirmed"],
        ["--confirm-activated", "--review-hash", preview["review_hash"],
         "--approved-by", "other@example.test", "--reason", "confirmed"],
        ["--confirm-activated", "--review-hash", "stale", "--approved-by", state.owner_email,
         "--reason", "confirmed"],
        ["--confirm-activated", "--review-hash", preview["review_hash"],
         "--approved-by", state.owner_email, "--reason", " "],
        valid + ["--chat-id", "19:meeting_OTHER@thread.v2"],
    ):
        assert cli.main(args + flags) == 1
        assert path.read_bytes() == before


def test_manual_review_rejects_changed_invitation_before_recording(state, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = manual_args(state, tmp_path)
    assert cli.main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    next(iter(state.notification_deliveries.values()))["attempts"][-1]["receipt"]["id"] = "new-event"
    state.save(str(path))
    before = path.read_bytes()
    assert cli.main(args + confirmation(state, preview)) == 1
    assert "review changed" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_manual_provenance_is_required_and_bound_into_notification_claim(
        state, tmp_path, capsys):
    path = tmp_path / state.release_id / "release-state.json"
    state.save(str(path))
    args = manual_args(state, tmp_path)
    assert cli.main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert cli.main(args + confirmation(state, preview)) == 0
    saved = ReleaseState.load(str(path))
    for field, value in (("approved_by", "other@example.test"), ("reason", ""),
                         ("activated", False), ("review_hash", "stale"),
                         ("chat_link", CHAT_LINK.replace("CURRENT", "OTHER")),
                         ("invite_hash", "stale"), ("verification", "graph")):
        changed = deepcopy(saved)
        changed.steps["bug_bash.activate_chat"]["data"]["meeting"][field] = value
        assert A.stored_chat_id(_context(changed)) is None
    scope = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates",
             "state_matches": A.chat_state_matches(_context(saved))}
    orch = Orchestrator(C.DEFAULT_CONFIG, saved, mocks={})
    item = D.descriptor(saved, "manual-update", scope, "workiq_send_chat_message",
                        {"chatId": A.stored_chat_id(_context(saved)), "content": "test"})
    D.offer(orch, item)
    saved.steps["bug_bash.activate_chat"]["data"]["meeting"]["reason"] = "different review"
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


def test_changed_receipt_join_url_invalidates_prepared_chat_update(state):
    seed_invite(state, "19:meeting_CURRENT@thread.v2")
    scope = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates",
             "state_matches": A.chat_state_matches(_context(state))}
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    item = D.descriptor(state, "receipt-update", scope, "workiq_send_chat_message",
                        {"chatId": A.stored_chat_id(_context(state)), "content": "test"})
    D.offer(orch, item)
    next(iter(state.notification_deliveries.values()))["attempts"][-1]["receipt"]["onlineMeeting"] = {
        "joinUrl": "https://teams.microsoft.com/l/meetup-join/changed"}
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "worker")


def test_manual_binding_never_bypasses_prerequisites_or_skipped_decision(
        state, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_graph_get", lambda *_: pytest.fail("No Graph for manual path"))
    path = tmp_path / state.release_id / "release-state.json"
    for change in ("no_invite", "uncertain_receipt", "skipped"):
        changed = deepcopy(state)
        if change == "no_invite":
            changed.steps.pop("bug_bash.send_invite")
        elif change == "uncertain_receipt":
            next(iter(changed.notification_deliveries.values()))["status"] = "uncertain"
        else:
            changed.set_step("bug_bash", A.ID, StepState(status="skipped", note="owner chose manual posts"))
        changed.save(str(path))
        before = path.read_bytes()
        assert cli.main(manual_args(changed, tmp_path)) == 1
        assert path.read_bytes() == before
