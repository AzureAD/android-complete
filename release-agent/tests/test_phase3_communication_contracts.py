"""Focused Phase-3 communication regressions; provider calls are never required."""
from copy import deepcopy
from datetime import datetime
from html import escape

import pytest

from orchestrator import delivery as D
from steps.bug_bash import send_invite as S
from steps.bug_bash import activate_chat as A
from steps.bug_bash import notify_native_auth as N
from tests._context import context, invoke
from tests._context import fresh_orchestrator
from tests._harness import _invite_state, _active_step, CONFIG
from orchestrator.state import StepState
from steps.lib import mockctx
from tools import invite as I
from tools import bugbash_meeting as M
from tests._meeting import seed_invite
from tests.test_bugbash_chat_binding import observations as chat_observations


def invitation(st=None):
    with mockctx.active({"now": "2026-09-11T10:00:00-07:00", "flags": "{}"}):
        return invoke(S.build, st or _invite_state())


@pytest.mark.parametrize("step,field", [
    ("clone_plans_broker", "plan_id"), ("clone_plans_auth", "suite_id"),
])
@pytest.mark.parametrize("bad", [True, 0, -1, '12" onclick="bad', [], {}])
def test_invitation_requires_valid_plan_ids(step, field, bad):
    st = _invite_state()
    st.steps[f"bug_bash.{step}"]["data"][field] = bad
    assert invitation(st).kind == "blocked"


@pytest.mark.parametrize("status", ["pending", "skipped", "blocked", "in_progress"])
def test_invitation_requires_completed_plan_source(status):
    st = _invite_state()
    st.steps["bug_bash.clone_plans_broker"]["status"] = status
    assert invitation(st).kind == "blocked"


@pytest.mark.parametrize("path,value", [
    (("rcs",), {}), (("rcs",), ["unknown"]), (("rcs", 0, "auth"), []),
    (("rcs", 0, "auth", "build"), "unknown"),
    (("rcs", 0, "ecs", "run_id"), True), (("rcs", 0, "local"), {}),
])
def test_invitation_holds_malformed_or_missing_pipeline_evidence(path, value):
    st = _invite_state()
    node = st.pipeline_runs
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert invitation(st).kind == "blocked"


def test_invitation_freezes_sources_for_claim_and_does_not_claim_availability():
    st = _invite_state()
    before = deepcopy(st)
    result = invitation(st)
    assert st == before and result.outbound
    paths = [match["path"] for match in result.notification["state_matches"]]
    assert ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"] in paths
    assert ["steps", "bug_bash.clone_plans_auth", "data", "suite_id"] in paths
    assert ["pipeline_runs", "rcs", -1, "rc"] in paths and ["target_month"] in paths
    assert result.notification["completion"]["data"]["gate_preconditions"]
    assert "not availability or OOF evidence" in result.summary
    assert "recipient consent" in result.summary
    assert set(result.payload) == {
        "subject", "attendees", "body", "bodyContentType", "start", "end",
        "timeZone", "isOnlineMeeting",
    }


def test_invitation_invalid_clock_holds_and_flags_are_html_escaped():
    with mockctx.active({"now": "unknown", "flags": "{}"}):
        assert invoke(S.build, _invite_state()).kind == "blocked"
    flags = '{x:"<script>bad</script>"}'
    assert escape(flags).replace("{", "{ ").replace("}", " }") == I.format_flags_html(flags)


def test_invitation_notice_is_rounded_up_not_truncated():
    start, _, _ = I.schedule_bugbash(datetime(2026, 9, 11, 10, 30, 1))
    assert start == datetime(2026, 9, 11, 13)


@pytest.mark.parametrize("change", ["target", "descriptor", "attempt", "scope", "receipt", "time"])
def test_chat_activation_holds_malformed_invitation_evidence(change):
    st = _invite_state()
    seed_invite(st)
    record = next(iter(st.notification_deliveries.values()))
    if change == "target":
        record["descriptor"]["target"] = {"attendees": ["other@example.test"]}
        item = record["descriptor"]
        item["hash"] = D.fingerprint({k: v for k, v in item.items() if k != "hash"})
        record["attempts"][-1]["hash"] = item["hash"]
    elif change == "time":
        item = record["descriptor"]
        item["payload"]["start"] = "unknown"
        item["hash"] = D.fingerprint({k: v for k, v in item.items() if k != "hash"})
        record["attempts"][-1]["hash"] = item["hash"]
    elif change == "descriptor":
        record["descriptor"] = []
    elif change == "attempt":
        record["attempts"] = [True]
    elif change == "scope":
        record["descriptor"]["scope"] = "unknown"
    else:
        record["attempts"][-1]["receipt"] = "unknown"
    before = deepcopy(st)
    result = invoke(A.build, st)
    assert result.kind == "blocked" and st == before


@pytest.mark.parametrize("field,value", [
    ("join_url", "https://evil.example/meeting"),
    ("join_url", "https://user@teams.microsoft.com/meeting"),
    ("online_meeting_id", True), ("event_id", "different"),
    ("chat_id", "19:meeting_OTHER@thread.v2"),
])
def test_stored_chat_rejects_malformed_or_mismatched_binding(field, value):
    st = _invite_state()
    seed_invite(st, "19:meeting_CURRENT@thread.v2")
    st.steps["bug_bash.activate_chat"]["data"]["meeting"][field] = value
    assert A.stored_chat_id(context(st)) is None


def test_invitation_preserves_actual_recipient_list_in_chat_source():
    st = _invite_state()
    seed_invite(st)
    assert S.delivered_invite(context(st))["attendees"] == ["team@example.test"]


def test_stored_chat_cannot_change_receipted_join_url():
    st = _invite_state()
    seed_invite(st)
    record = next(iter(st.notification_deliveries.values()))
    record["attempts"][-1]["receipt"]["onlineMeeting"] = {
        "joinUrl": "https://teams.microsoft.com/l/meetup-join/original",
    }
    invite = S.delivered_invite(context(st))
    from tests._meeting import meeting
    st.set_step("bug_bash", A.ID, StepState(status="done", data={
        "invite": invite, "chat_id": "19:meeting_CURRENT@thread.v2",
        "meeting": meeting(invite, "19:meeting_CURRENT@thread.v2"),
    }))
    assert A.stored_chat_id(context(st)) is None


def test_chat_preconditions_bind_identity_not_verification_or_display_timestamps():
    st = _invite_state()
    seed_invite(st, "19:meeting_CURRENT@thread.v2")
    before = A.chat_state_matches(context(st))
    st.steps["bug_bash.activate_chat"]["data"]["verified_at"] = "2026-09-11T20:00:00Z"
    st.steps["bug_bash.send_invite"]["note"] = "updated display note"
    assert A.chat_state_matches(context(st)) == before
    paths = [match["path"] for match in before]
    assert ["steps", "bug_bash.activate_chat", "data", "meeting", "online_meeting_id"] in paths
    assert not any("verified_at" in path for path in paths)


@pytest.mark.parametrize("kind", ["invitation", "native"])
def test_communication_claim_rejects_changed_plan_source(kind):
    st = _invite_state()
    sources = deepcopy(st.steps)
    _active_step(st, "bug_bash", S.ID if kind == "invitation" else N.ID)
    st.steps.update(sources)
    orch = fresh_orchestrator(CONFIG, st, mocks={})
    result = invitation(st) if kind == "invitation" else invoke(
        N.build, st, engineer="engineer@example.test", engineer_source="owner and directory verified")
    step_id = S.ID if kind == "invitation" else N.ID
    item = D.descriptor(st, f"test:{step_id}", {
        "kind": "step", "phase": "bug_bash", "step": step_id,
        "state_matches": result.notification["state_matches"],
    }, result.tool, result.payload, {"kind": "step", "record_as": step_id})
    D.offer(orch, item)
    st.steps["bug_bash.clone_plans_broker"]["data"]["plan_id"] = 999
    with pytest.raises(ValueError, match="source checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "test-worker")


def test_invitation_source_identity_tracks_latest_rc_but_not_poll_timestamps():
    st = _invite_state()
    matches = invitation(st).notification["state_matches"]
    st.pipeline_runs["rcs"][-1]["resolved_at"] = "2026-09-11T20:00:00Z"
    st.steps["bug_bash.clone_plans_broker"]["note"] = "refreshed display note"
    S._check_source_matches(context(st), {"state_matches": matches})
    newer = deepcopy(st.pipeline_runs["rcs"][-1])
    newer["rc"] = 2
    newer["auth"]["build"]["rc"] = 2
    st.pipeline_runs["rcs"].append(newer)
    with pytest.raises(ValueError, match="source checkpoint changed"):
        S._check_source_matches(context(st), {"state_matches": matches})


@pytest.mark.parametrize("engineer,source", [
    ("alias", "directory"), ("a@@example.test", "directory"),
    (" engineer@example.test", "directory"), ("engineer@example.test\n", "directory"),
    ("engineer@example.test", " "), ("", "directory"), (None, "directory"),
])
def test_native_notification_requires_explicit_verified_recipient(engineer, source):
    assert invoke(N.build, _invite_state(), engineer=engineer, engineer_source=source).kind == "blocked"


def test_native_notification_escapes_owner_and_freezes_exact_broker_source():
    st = _invite_state()
    st.owner_name = '<img src=x onerror="bad">'
    result = invoke(N.build, st, engineer="engineer@example.test", engineer_source="owner and directory")
    assert result.kind == "needs_skill" and result.outbound
    assert set(result.payload) == {"userIdOrUpn", "content", "contentType"}
    assert result.payload["userIdOrUpn"] == "engineer@example.test"
    assert "<img" not in result.payload["content"] and "&lt;img" in result.payload["content"]
    assert "recipient approval" in result.summary
    assert ["steps", "bug_bash.clone_plans_broker", "data", "plan_id"] in [
        m["path"] for m in result.notification["state_matches"]]


@pytest.mark.parametrize("status", ["pending", "done", "skipped"])
def test_native_notification_engineer_only_never_autoattests_delivery(status):
    st = _invite_state()
    st.set_step("bug_bash", N.ID, StepState(status=status, data={"engineer": "engineer@example.test"}))
    assert invoke(N.build, st).kind == "blocked"
    assert N.notified_engineer(context(st)) is None


def native_receipt():
    st = _invite_state()
    result = invoke(N.build, st, engineer="engineer@example.test", engineer_source="owner and directory")
    item = D.descriptor(st, "test:native", {
        "kind": "step", "phase": "bug_bash", "step": N.ID,
        "release_matches": {"owner_email": st.owner_email, "ccd": st.ccd},
        "state_matches": result.notification["state_matches"],
    }, result.tool, result.payload, {
        "kind": "step", "record_as": N.ID, **result.notification["completion"],
    })
    st.notification_deliveries[item["id"]] = {
        "descriptor": item, "status": "sent", "completion": {"status": "applied"},
        "attempts": [{"id": "native-execution", "status": "sent", "owner": "test-worker",
                      "hash": item["hash"], "evidence": "synthetic provider acknowledgement",
                      "acknowledged_at": "2026-09-11T20:00:00Z"}],
    }
    st.set_step("bug_bash", N.ID, StepState(status="done", data={
        **result.notification["completion"]["data"], "notification_id": item["id"],
        "notification_execution_id": "native-execution",
    }))
    return st


def test_native_notification_replay_requires_real_exact_terminal_evidence():
    st = native_receipt()
    before = deepcopy(st)
    assert N.notified_engineer(context(st)) == "engineer@example.test"
    assert invoke(N.build, st).kind == "done" and st == before


def test_native_notification_binding_ignores_cosmetic_source_checkpoint_changes():
    st = native_receipt()
    st.steps["bug_bash.clone_plans_broker"]["note"] = "updated display note"
    st.steps["bug_bash.clone_plans_broker"]["completed_at"] = "2026-09-11T20:00:00Z"
    assert N.notified_engineer(context(st)) == "engineer@example.test"


@pytest.mark.parametrize("change", ["engineer", "source", "owner", "month", "plan", "uncertain", "completion"])
def test_native_notification_changed_or_partial_receipt_holds(change):
    st = native_receipt()
    row = next(iter(st.notification_deliveries.values()))
    if change in ("engineer", "source"):
        st.steps["bug_bash.notify_native_auth"]["data"][
            "engineer" if change == "engineer" else "engineer_source"] = "other@example.test"
    elif change == "owner":
        st.owner_email = "other@example.test"
    elif change == "month":
        st.target_month = "2026-12"
    elif change == "plan":
        st.steps["bug_bash.clone_plans_broker"]["data"]["plan_id"] = 999
    elif change == "uncertain":
        row["status"] = "uncertain"
    else:
        row["completion"]["status"] = "suppressed"
    assert N.notified_engineer(context(st)) is None
    assert invoke(N.build, st).kind == "blocked"


@pytest.fixture
def live_event(monkeypatch):
    st = _invite_state()
    seed_invite(st)
    invite = S.delivered_invite(context(st))
    invite.update(optionalAttendees=["optional@example.test"], resourceAttendees=["room@example.test"])
    join = "https://teams.microsoft.com/l/meetup-join/19%3Ameeting_CURRENT%40thread.v2/0"
    data = {
        "me": {"id": "profile-1", "userPrincipalName": st.owner_email},
        "event": {"id": invite["event_id"], "subject": invite["subject"], "isCancelled": False,
                  "isOnlineMeeting": True,
                  "organizer": {"emailAddress": {"address": st.owner_email}},
                  "start": {"dateTime": invite["start"], "timeZone": invite["timeZone"]},
                  "end": {"dateTime": invite["end"], "timeZone": invite["timeZone"]},
                  "onlineMeeting": {"joinUrl": join},
                  "attendees": [
                      {"type": "required", "emailAddress": {"address": "TEAM@example.test"}},
                      {"type": "optional", "emailAddress": {"address": "optional@example.test"}},
                      {"type": "resource", "emailAddress": {"address": "room@example.test"}},
                  ]},
        "chat": {"id": "19:meeting_CURRENT@thread.v2", "topic": invite["subject"], "chatType": "meeting"},
    }
    from tools import distribution
    def forbidden(*args, **kwargs):
        pytest.fail("Native meeting evidence must not use Graph/Azure CLI")
    monkeypatch.setattr(distribution, "_graph_get", forbidden, raising=False)
    monkeypatch.setattr(distribution, "_token", forbidden, raising=False)
    monkeypatch.setattr(distribution.P, "_az_json", forbidden, raising=False)
    return invite, data


def test_live_event_preserves_exact_recipient_types_without_reordering_payload(live_event):
    invite, data = live_event
    data["event"]["attendees"].reverse()
    before = deepcopy(invite)
    bound = M.resolve(invite, **chat_observations(invite, data))
    assert bound["chat_id"] == "19:meeting_CURRENT@thread.v2"
    assert invite == before
    event_read = bound["connector_evidence"]["event"]
    assert event_read["tool"] == "workiq_get_event"
    assert event_read["arguments"]["eventId"] == "event-1"
    assert "attendees" in event_read["arguments"]["select"]


@pytest.mark.parametrize("change", [
    "missing", "null", "object", "empty", "extra", "removed", "duplicate", "address",
    "type", "missing_type", "unknown_type", "missing_address", "partial", "root_partial",
    "wrong_count", "bool_count", "missing_approved", "malformed_approved",
])
def test_live_event_holds_missing_malformed_partial_or_changed_recipients(live_event, change):
    invite, data = live_event
    event = data["event"]
    if change == "missing":
        event.pop("attendees")
    elif change in ("null", "object", "empty"):
        event["attendees"] = {"null": None, "object": {}, "empty": []}[change]
    elif change == "extra":
        event["attendees"].append({"type": "required", "emailAddress": {"address": "other@example.test"}})
    elif change == "removed":
        event["attendees"].pop()
    elif change == "duplicate":
        event["attendees"].append(deepcopy(event["attendees"][0]))
    elif change == "address":
        event["attendees"][0]["emailAddress"]["address"] = "other@example.test"
    elif change == "type":
        event["attendees"][0]["type"] = "optional"
    elif change == "missing_type":
        event["attendees"][0].pop("type")
    elif change == "unknown_type":
        event["attendees"][0]["type"] = "unknown"
    elif change == "missing_address":
        event["attendees"][0]["emailAddress"] = {}
    elif change in ("partial", "root_partial"):
        event["attendees@odata.nextLink" if change == "partial" else "@odata.nextLink"] = "next"
    elif change in ("wrong_count", "bool_count"):
        event["attendees@odata.count"] = 10 if change == "wrong_count" else True
    elif change == "missing_approved":
        invite.pop("attendees")
    else:
        invite["optionalAttendees"] = "optional@example.test"
    before = deepcopy(invite)
    with pytest.raises(ValueError):
        M.resolve(invite, **chat_observations(invite, data))
    assert invite == before


@pytest.mark.parametrize("node,value", [
    ("me", {"userPrincipalName": None}), ("event", {"organizer": None}),
    ("event", {"start": []}), ("chat", {"id": None}),
])
def test_live_event_holds_malformed_provider_shapes(live_event, node, value):
    invite, data = live_event
    data[node].update(value)
    with pytest.raises(ValueError):
        M.resolve(invite, **chat_observations(invite, data))


@pytest.mark.parametrize("change", ["join", "external", "credentials", "owner"])
def test_live_event_rejects_wrong_join_or_owner(live_event, change):
    invite, data = live_event
    if change == "join":
        invite["join_url"] = "https://teams.microsoft.com/l/meetup-join/other"
    elif change in ("external", "credentials"):
        data["event"]["onlineMeeting"]["joinUrl"] = (
            "https://external.example/join" if change == "external"
            else "https://user@teams.microsoft.com/l/meetup-join/original")
    else:
        data["event"]["organizer"]["emailAddress"]["address"] = "other@example.test"
    with pytest.raises(ValueError):
        M.resolve(invite, **chat_observations(invite, data))
