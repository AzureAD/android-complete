"""Name/GUID mention rendering and first/poller payload parity; no live sends."""
from tests._context import context as _context, invoke as _invoke, command_inputs
from argparse import Namespace
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from orchestrator import cli_common as C, delivery as D
from orchestrator.commands import step_action, bugbash_update
from tests._context import fresh_orchestrator as Orchestrator
from orchestrator.state import StepState
from steps.bug_bash import bugbash_updates as U
from steps.lib import mockctx
from tests._harness import _active_phase, _bb_updates_state, CONFIG
from tools import bugbash as B, distribution as G

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
CHAT = "19:meeting_X@thread.v2"
OWNER = "owner@example.test"


def member_snapshot():
    return {"id": CHAT, "chatType": "meeting", "members": [
        {"id": "membership-record", "userId": ALICE, "email": "a@example.test",
         "displayName": "Alice & Co"},
        {"id": "other-membership", "userId": BOB, "email": "b@example.test",
         "displayName": "Bob"},
    ]}


def member_envelope(result=None, *, tool="workiq_get_chat", owner=OWNER, chat=CHAT):
    return {"version": 1, "tool": tool, "arguments": {"chatId": chat},
            "account": owner, "observed_at": datetime.now(timezone.utc).isoformat(),
            "result": member_snapshot() if result is None else result}


def directory_envelope(upn="a@example.test", *, user_id=ALICE, owner=OWNER):
    return {"version": 1, "tool": "workiq.fetch",
            "arguments": {"entityUrls": [B.mention_directory_path(upn)]},
            "account": owner, "observed_at": datetime.now(timezone.utc).isoformat(),
            "result": {"id": user_id, "userPrincipalName": upn,
                       "displayName": "Directory name is not the mention label",
                       "mail": "alice.smith@example.test"}}


def progress():
    return {"total": 2, "done": 1, "remaining": 1, "unassigned": 0, "owners": {
        "a@example.test": {"name": "a@example.test", "total": 1, "done": 0, "remaining": 1,
                           "tests": [{"id": "1", "name": "Login <test>", "url": "https://example.test/1",
                                      "state": "notrun", "products": ["Broker"]}]},
        "b@example.test": {"name": "b@example.test", "total": 1, "done": 1, "remaining": 0,
                           "tests": [{"id": "2", "name": "Done", "url": "https://example.test/2",
                                      "state": "passed", "products": ["Authenticator"]}]}}}


def people():
    return {"a@example.test": {"id": ALICE, "name": "Alice & Co"},
            "b@example.test": {"id": BOB, "name": "Bob"}}


def state():
    st = _active_phase(_bb_updates_state(), "bug_bash")
    phase = next(p for p in Orchestrator(CONFIG, st, mocks={}).config["phases"] if p["id"] == "bug_bash")
    for step in phase["steps"]:
        if step["id"] == U.ID:
            break
        if not st.is_done("bug_bash", step["id"]):
            st.set_step("bug_bash", step["id"], StepState(status="done"))
    return st


def test_payload_has_display_name_tags_and_real_guid_identities():
    content, mentions = B.render_update(progress(), "October", [], people())
    assert '<at id="0">Alice &amp; Co</at>' in content
    assert "a@example.test" not in content and "b@example.test" not in content
    assert "<b>Bob</b>" in content and "Login &lt;test&gt;" in content
    assert mentions == [{"id": 0, "mentionText": "Alice & Co",
                         "mentioned": {"user": {"id": ALICE, "displayName": "Alice & Co",
                                                  "userIdentityType": "aadUser"}}}]


@pytest.mark.parametrize("person", [{}, {"id": "a@example.test", "name": "Alice"},
                                     {"id": ALICE, "name": "a@example.test"}])
def test_unresolved_identity_never_produces_fake_upn_mentions(person):
    with pytest.raises(ValueError):
        B.render_update(progress(), "October", [], {"a@example.test": person})


@pytest.mark.parametrize("tool", ["workiq_get_chat", "microsoft_teams-ListChatMembers"])
def test_member_resolution_uses_user_guid_not_membership_id_without_az(monkeypatch, tool):
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: pytest.fail("No az/CLI for mentions"))
    result = member_snapshot() if tool == "workiq_get_chat" else {"members": member_snapshot()["members"]}
    ok, found, detail = B.resolve_mention_people(
        CHAT, progress()["owners"], member_observation=member_envelope(result, tool=tool), owner=OWNER)
    assert ok and not detail and found == people()
    assert not hasattr(G, "_graph_get") and not hasattr(G, "_graph_token")


def test_native_member_response_is_chat_bound_and_renders_without_graph(monkeypatch, tmp_path):
    st = state()
    snapshot = member_envelope()
    path = tmp_path / "members.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: pytest.fail("Native members avoid az"))
    with mockctx.active({}):
        ok, payload, detail = U.prepare_update(_context(st), progress(), str(path))
    assert ok and not detail and payload["mentions"][0]["mentioned"]["user"]["id"] == ALICE
    snapshot["result"]["id"] = "19:meeting_OLD@thread.v2"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with mockctx.active({"people": people()}):
        ok, payload, detail = U.prepare_update(_context(st), progress(), str(path))
    assert not ok and payload is None and "this meeting" in detail


@pytest.mark.parametrize("overrides", [
    {"chatType": "group"}, {"members": None},
    {"@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
    {"members@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
])
def test_native_member_response_rejects_wrong_type_or_partial_members(overrides):
    observation = member_envelope({**member_snapshot(), **overrides})
    ok, _, detail = B.resolve_mention_people(CHAT, {}, member_observation=observation, owner=OWNER)
    assert not ok and detail


@pytest.mark.parametrize("change", ["stale", "account", "chat", "legacy", "tool", "truncated", "no_userid"])
def test_member_envelope_rejects_unbound_or_partial_evidence(change):
    observation = member_envelope()
    if change == "stale":
        observation["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    elif change == "account":
        observation["account"] = "other@example.test"
    elif change == "chat":
        observation["arguments"]["chatId"] = "old-chat"
    elif change == "legacy":
        observation = observation["result"]
    elif change == "tool":
        observation["tool"] = "workiq_search_people"
    elif change == "truncated":
        observation["result"]["hasMoreResults"] = True
    else:
        observation["result"]["members"][0].pop("userId")
    ok, found, detail = B.resolve_mention_people(
        CHAT, progress()["owners"], member_observation=observation, owner=OWNER)
    assert not ok and found is None and detail


def test_upn_smtp_difference_holds_for_explicit_directory_evidence_without_raw_graph(monkeypatch):
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: pytest.fail("No az directory fallback"))
    observation = member_envelope()
    observation["result"]["members"][0]["email"] = "alice.smith@example.test"
    owners = {"a@example.test": progress()["owners"]["a@example.test"]}
    with pytest.raises(G.ConnectorObservationRequired) as failure:
        B.resolve_mention_people(CHAT, owners, member_observation=observation, owner=OWNER)
    assert failure.value.tool == "workiq.fetch"
    assert failure.value.arguments == {"entityUrls": [B.mention_directory_path("a@example.test")]}
    assert "verified native CLI" in str(failure.value)


@pytest.mark.parametrize("missing_email", [False, True])
def test_directory_evidence_maps_assigned_upn_to_real_member_id_and_member_display_name(
        monkeypatch, missing_email):
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: pytest.fail("No raw Graph/az directory read"))
    observation = member_envelope()
    if missing_email:
        observation["result"]["members"][0].pop("email")
    else:
        observation["result"]["members"][0]["email"] = "alice.smith@example.test"
    observation["directory_observations"] = [directory_envelope()]
    ok, found, detail = B.resolve_mention_people(
        CHAT, progress()["owners"], member_observation=observation, owner=OWNER)
    assert ok and not detail and found == people()
    content, mentions = B.render_update(progress(), "October", [], found)
    assert mentions[0]["mentioned"]["user"]["id"] == ALICE
    assert mentions[0]["mentionText"] == "Alice & Co"
    assert "Directory name is not" not in content


@pytest.mark.parametrize("change", [
    "stale", "wrongaccount", "wrong_upn", "nonmember", "wrong_arguments",
    "wrong_tool", "duplicate", "malformed", "conflicting_email_identity", "other_member",
])
def test_directory_evidence_rejects_stale_malformed_or_other_user(change):
    observation = member_envelope()
    observation["result"]["members"][0]["email"] = "alice.smith@example.test"
    directory = directory_envelope()
    observation["directory_observations"] = [directory]
    if change == "stale":
        directory["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    elif change == "wrongaccount":
        directory["account"] = "other@example.test"
    elif change == "wrong_upn":
        directory["result"]["userPrincipalName"] = "other@example.test"
    elif change == "nonmember":
        directory["result"]["id"] = "33333333-3333-3333-3333-333333333333"
    elif change == "wrong_arguments":
        directory["arguments"]["entityUrls"] = [B.mention_directory_path("other@example.test")]
    elif change == "wrong_tool":
        directory["tool"] = "workiq_search_people"
    elif change == "duplicate":
        observation["directory_observations"].append(deepcopy(directory))
    elif change == "malformed":
        directory["result"].pop("id")
    elif change == "conflicting_email_identity":
        observation["result"]["members"][0]["email"] = "a@example.test"
        directory["result"]["id"] = BOB
    else:
        directory["result"]["id"] = BOB
    ok, found, detail = B.resolve_mention_people(
        CHAT, progress()["owners"], member_observation=observation, owner=OWNER)
    assert not ok and found is None and detail


@pytest.mark.parametrize("failure", ["missing", "stale", "wrong_user"])
def test_initial_and_periodic_directory_evidence_fail_closed_with_actionable_missing_read(
        tmp_path, capsys, failure):
    st = state()
    observation = member_envelope()
    observation["result"]["members"][0]["email"] = "alice.smith@example.test"
    if failure != "missing":
        observation["directory_observations"] = [directory_envelope()]
        if failure == "stale":
            observation["directory_observations"][0]["observed_at"] = (
                datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        else:
            observation["directory_observations"][0]["result"]["userPrincipalName"] = "other@example.test"
    path = tmp_path / "members.json"
    path.write_text(json.dumps(observation), encoding="utf-8")
    with mockctx.active({"progress": progress()}):
        initial = _invoke(U.build, st, members_file=str(path))
    if failure == "missing":
        assert initial.kind == "needs_skill" and not initial.outbound and initial.tool == "workiq.fetch"
        assert initial.payload["entityUrls"] == [B.mention_directory_path("a@example.test")]
    else:
        assert initial.kind == "blocked" and initial.reason
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T10:00:00-07:00", force=True, members_file=str(path))
    with command_inputs("bug_bash.bugbash_updates", {"progress": progress()}):
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    periodic = json.loads(capsys.readouterr().out)
    assert periodic["decision"] == "error" and periodic["notifications"] == []
    assert not periodic["permission_to_send"]
    if failure == "missing":
        assert periodic["connector_reads"] == initial.payload["_connector_reads"]
        assert "directory_observations" in periodic["followup"]


def test_missing_members_requests_read_before_initial_update_without_send(monkeypatch):
    monkeypatch.setattr(G.subprocess, "run", lambda *a, **kw: pytest.fail("No az member fallback"))
    with mockctx.active({"progress": progress()}):
        out = _invoke(U.build, state())
    assert out.kind == "needs_skill" and not out.outbound
    assert out.tool == "microsoft_teams-ListChatMembers"
    assert out.payload["chatId"] == CHAT and out.payload["_connector_reads"]
    assert "members_file" in out.payload["_followup"]


@pytest.mark.parametrize("native_response", [False, True, "directory"])
def test_both_producers_preserve_the_same_mentions_in_claimed_payload(
        monkeypatch, tmp_path, capsys, native_response):
    st = state()
    spec = {"progress": progress(), "people": people()}
    params, members_file = [], None
    if native_response:
        spec.pop("people")
        path = tmp_path / "members.json"
        observation = member_envelope()
        if native_response == "directory":
            observation["result"]["members"][0]["email"] = "alice.smith@example.test"
            observation["directory_observations"] = [directory_envelope()]
        path.write_text(json.dumps(observation), encoding="utf-8")
        members_file = str(path)
        params = [f"members_file={path}"]
    monkeypatch.setattr(step_action.mocks_mod, "load_mocks", lambda: {"bug_bash.bugbash_updates": spec})
    orch = Orchestrator(CONFIG, st, mocks={})
    out = step_action.prepare_step(Namespace(phase="bug_bash", step=U.ID, release=st.release_id,
                                             param=params), st, orch)
    initial = out["notifications"][0]
    assert "_mentions" not in initial["payload"]
    D.offer(orch, initial)
    claimed = D.claim(orch, initial["id"], initial["hash"], "test-worker")
    assert claimed["payload"]["mentions"][0]["mentioned"]["user"]["id"] == ALICE
    assert claimed["payload"]["mentions"][0]["mentionText"] == "Alice & Co"
    st = state()
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T10:00:00-07:00", force=True, members_file=members_file)
    with command_inputs("bug_bash.bugbash_updates", spec):
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    periodic = json.loads(capsys.readouterr().out)["notifications"][0]
    assert periodic["payload"] == initial["payload"]
    assert periodic["payload"]["content"].count("<li>") == 2
    assert "[Broker]" in periodic["payload"]["content"] and "[Authenticator]" in periodic["payload"]["content"]
    assert '<li>✅ <b>[Authenticator]</b>' in periodic["payload"]["content"]
    assert "(Passed — resolved)" not in periodic["payload"]["content"]


def test_resolution_failure_stages_no_periodic_notification(monkeypatch, tmp_path, capsys):
    st = state()
    C.save_state(st, str(tmp_path), st.release_id)
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T10:00:00-07:00", force=True)
    before = deepcopy(st.notification_deliveries)
    with command_inputs("bug_bash.bugbash_updates", {"progress": progress()}):
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "error" and out["notifications"] == []
    assert out["chatId"] == "19:meeting_X@thread.v2" and "--members-file" in out["detail"]
    assert out["connector_reads"] and not out["permission_to_send"]
    assert C.load_state(str(tmp_path), st.release_id).notification_deliveries == before
