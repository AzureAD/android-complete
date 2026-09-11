"""Name/GUID mention rendering and first/poller payload parity; no live sends."""
from argparse import Namespace
from copy import deepcopy
import json

import pytest

from orchestrator import cli_common as C, delivery as D
from orchestrator.commands import step_action, bugbash_update
from orchestrator.engine import Orchestrator
from orchestrator.state import StepState
from steps.bug_bash import bugbash_updates as U
from steps.lib import mockctx
from tests._harness import _active_phase, _bb_updates_state, CONFIG
from tools import bugbash as B, distribution as G

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


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


def test_member_resolution_uses_user_guid_not_membership_id_and_follows_pages(monkeypatch):
    calls = []
    def get(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return True, {"value": [{"id": "membership-record", "userId": ALICE, "displayName": "Alice & Co",
                                     "email": "A@example.test"}],
                          "@odata.nextLink": "https://graph.microsoft.com/v1.0/next"}, ""
        return True, {"value": [{"id": "other-membership", "userId": BOB, "displayName": "Bob",
                                 "email": "b@example.test"}]}, ""
    monkeypatch.setattr(G, "_graph_get", get)
    ok, found, detail = B.resolve_mention_people("19:meeting_X@thread.v2", progress()["owners"])
    assert ok and not detail and found == people()
    assert len(calls) == 2


def test_native_member_response_is_chat_bound_and_renders_without_graph(monkeypatch, tmp_path):
    st = state()
    snapshot = {"id": "19:meeting_X@thread.v2", "chatType": "meeting", "members": [
        {"userId": ALICE, "email": "a@example.test", "displayName": "Alice & Co"},
        {"userId": BOB, "email": "b@example.test", "displayName": "Bob"},
    ]}
    path = tmp_path / "members.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(G, "_graph_get", lambda *a: pytest.fail("Native member response should avoid Graph"))
    with mockctx.active({}):
        ok, payload, detail = U.prepare_update(st, progress(), str(path))
    assert ok and not detail and payload["mentions"][0]["mentioned"]["user"]["id"] == ALICE
    snapshot["id"] = "19:meeting_OLD@thread.v2"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with mockctx.active({}):
        ok, payload, detail = U.prepare_update(st, progress(), str(path))
    assert not ok and payload is None and "this meeting" in detail


@pytest.mark.parametrize("overrides", [
    {"chatType": "group"}, {"members": None},
    {"@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
    {"members@odata.nextLink": "https://graph.microsoft.com/v1.0/next"},
])
def test_native_member_response_rejects_wrong_type_or_partial_members(overrides):
    observation = {"id": "chat", "chatType": "meeting", "members": [], **overrides}
    ok, _, detail = B.resolve_mention_people("chat", {}, member_observation=observation)
    assert not ok and detail


def test_upn_smtp_difference_resolves_directory_and_verifies_chat_membership(monkeypatch):
    calls = []
    def get(url, timeout):
        calls.append(url)
        if "/members" in url:
            return True, {"value": [{"userId": ALICE, "email": "alice.smith@example.test",
                                     "displayName": "Alice & Co"}]}, ""
        return True, {"id": ALICE, "displayName": "Alice & Co", "userPrincipalName": "a@example.test",
                      "mail": "alice.smith@example.test"}, ""
    monkeypatch.setattr(G, "_graph_get", get)
    owners = {"a@example.test": progress()["owners"]["a@example.test"]}
    assert B.resolve_mention_people("chat", owners) == (True, {"a@example.test": people()["a@example.test"]}, "")
    assert "/users/a%40example.test" in calls[1]


def test_unresolved_nonmember_blocks_initial_update_without_payload(monkeypatch):
    monkeypatch.setattr(G, "_graph_get", lambda url, timeout: (
        True, {"value": []} if "/members" in url else
        {"id": ALICE, "displayName": "Alice", "userPrincipalName": "a@example.test"}, ""))
    with mockctx.active({"progress": progress()}):
        out = U.build(state())
    assert out.kind == "blocked" and "not a verified user in this meeting" in out.reason


@pytest.mark.parametrize("native_response", [False, True])
def test_both_producers_preserve_the_same_mentions_in_claimed_payload(
        monkeypatch, tmp_path, capsys, native_response):
    st = state()
    spec = {"progress": progress(), "people": people()}
    params, members_file = [], None
    if native_response:
        spec.pop("people")
        path = tmp_path / "members.json"
        path.write_text(json.dumps({
            "id": "19:meeting_X@thread.v2", "chatType": "meeting",
            "members": [{"userId": p["id"], "displayName": p["name"], "email": upn}
                        for upn, p in people().items()]}), encoding="utf-8")
        members_file = str(path)
        params = [f"members_file={path}"]
        monkeypatch.setattr(G, "_graph_get", lambda *a: pytest.fail("Use the native member response"))
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
    with mockctx.active(spec):
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
    monkeypatch.setattr(G, "_graph_get", lambda *a: (False, None, "HTTP 403"))
    args = Namespace(runs_root=str(tmp_path), release=st.release_id, config=CONFIG,
                     now="2026-09-11T10:00:00-07:00", force=True)
    before = deepcopy(st.notification_deliveries)
    with mockctx.active({"progress": progress()}):
        assert bugbash_update.cmd_post_bugbash_update(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "error" and out["notifications"] == []
    assert out["chatId"] == "19:meeting_X@thread.v2" and "--members-file" in out["detail"]
    assert C.load_state(str(tmp_path), st.release_id).notification_deliveries == before
