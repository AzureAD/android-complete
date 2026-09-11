"""Bounded routine delivery state without weakening claims, receipts or lifecycle recovery."""
from argparse import Namespace
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from orchestrator import cli_common as C, delivery as D, schedule
from orchestrator.engine import Orchestrator
from orchestrator.commands import delivery_cmd, bugbash_update
from steps.bug_bash.activate_chat import chat_state_matches, stored_chat_id
from tests._harness import _bb_updates_state, _active_phase


@pytest.fixture
def clock(monkeypatch):
    current = [datetime.fromisoformat("2026-09-11T13:00:00-07:00")]
    monkeypatch.setattr(D, "now_iso", lambda: current[0].isoformat())
    monkeypatch.setattr(schedule, "now_local", lambda _: current[0])
    return current


@pytest.fixture
def orch(clock):
    st = _active_phase(_bb_updates_state(), "bug_bash")
    return Orchestrator(C.DEFAULT_CONFIG, st, mocks={}, as_of=clock[0])


def progress(orch, start="2026-09-11T12:00:00-07:00", hours=3, text="X" * 20000):
    end = datetime.fromisoformat(start) + timedelta(hours=hours)
    return D.descriptor(orch.state, f"bugbash:update:{start}", {
        "kind": "phase", "phase": "bug_bash", "step": "bugbash_updates", "until_flag": "poll_complete",
        "state_matches": chat_state_matches(orch.state), "expires_at": end.isoformat(),
    }, "workiq_send_chat_message", {"chatId": stored_chat_id(orch.state), "content": text,
                                    "contentType": "html", "mentions": []})


def claim(orch, item):
    D.offer(orch, item)
    return D.claim(orch, item["id"], item["hash"], "worker")


def sent(orch, item, receipt=None, finalize=True):
    attempt = claim(orch, item)
    D.result(orch, item["id"], attempt["execution_id"], "sent", "provider accepted", receipt)
    if finalize:
        delivery_cmd.finish(orch, item["id"])
    return attempt


def test_completed_routine_send_compacts_and_cannot_be_sent_again(orch):
    item = progress(orch)
    attempt = sent(orch, item, {"id": "message-123", "body": "do not keep duplicate full response"})
    before = len(json.dumps(orch.state.notification_deliveries[item["id"]]))
    assert D.prune_progress(orch)
    record = orch.state.notification_deliveries[item["id"]]
    assert set(record) == {"status", "sent_receipt"}
    receipt = record["sent_receipt"]
    assert receipt["message_id"] == "message-123"
    assert receipt["hash"] == item["hash"] and receipt["execution_id"] == attempt["execution_id"]
    assert receipt["chatId"] == item["target"]["chatId"]
    assert len(json.dumps(record)) < before / 20
    assert "payload" not in json.dumps(record) and "body" not in json.dumps(record)
    assert not D.available(orch, item)
    assert D.claim(orch, item["id"], item["hash"], "another-worker") == {
        "status": "already_sent", "permission_to_send": False}
    assert not D.result(orch, item["id"], attempt["execution_id"], "sent", "retry acknowledgement")
    assert not delivery_cmd.finish(orch, item["id"])
    assert not D.has_pending(orch, ["bug_bash.bugbash_updates"])
    preview = D.offer(orch, item)
    assert preview["delivery_status"] == "sent" and not preview["permission_to_send"]
    assert "payload" not in preview
    with pytest.raises(ValueError, match="hash"):
        D.claim(orch, item["id"], "f" * 64, "another-worker")
    with pytest.raises(ValueError, match="owning execution"):
        D.result(orch, item["id"], "different-attempt", "sent", "wrong worker")


def test_compact_receipt_survives_process_reload(orch, tmp_path, clock):
    item = progress(orch)
    attempt = sent(orch, item, {"id": "message"})
    D.prune_progress(orch)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    st = C.load_state(str(tmp_path), orch.state.release_id)
    restored = Orchestrator(C.DEFAULT_CONFIG, st, mocks={}, as_of=clock[0])
    assert not D.claim(restored, item["id"], item["hash"], "second-process")["permission_to_send"]
    assert not D.result(restored, item["id"], attempt["execution_id"], "sent", "retried receipt")


@pytest.mark.parametrize("status", ["prepared", "not_sent"])
def test_only_expired_unsent_snapshots_are_removed(orch, clock, status):
    item = progress(orch, start="2026-09-11T09:00:00-07:00")
    D.offer(orch, item)
    if status == "not_sent":
        orch.now_local = datetime.fromisoformat("2026-09-11T10:00:00-07:00")
        attempt = D.claim(orch, item["id"], item["hash"], "worker")
        D.result(orch, item["id"], attempt["execution_id"], "not_sent", "provider rejected")
    active = progress(orch)
    D.offer(orch, active)
    before = deepcopy(orch.state.notification_deliveries[active["id"]])
    assert D.prune_progress(orch)
    assert item["id"] not in orch.state.notification_deliveries
    assert orch.state.notification_deliveries[active["id"]] == before


@pytest.mark.parametrize("status", ["claimed", "uncertain", "sent_unfinalized"])
def test_interrupted_delivery_evidence_is_never_aged_out(orch, clock, status):
    item = progress(orch)
    attempt = claim(orch, item)
    if status != "claimed":
        D.result(orch, item["id"], attempt["execution_id"],
                 "sent" if status == "sent_unfinalized" else "uncertain", "provider outcome",
                 {"id": "message", "raw": "must survive recovery"})
    before = deepcopy(orch.state.notification_deliveries)
    clock[0] += timedelta(days=365)
    assert not D.prune_progress(orch)
    assert orch.state.notification_deliveries == before
    if status != "sent_unfinalized":
        with pytest.raises(ValueError, match="uncertain"):
            D.claim(orch, item["id"], item["hash"], "restart")


def test_receipts_age_out_only_after_maximum_cadence_grace(orch, clock):
    item = progress(orch)
    sent(orch, item)
    D.prune_progress(orch)
    # Extending a three-hour checkpoint to 24 hours must still not replay that send.
    clock[0] = datetime.fromisoformat("2026-09-12T14:59:59-07:00")
    assert not D.prune_progress(orch)
    assert "evidence" in orch.state.notification_deliveries[item["id"]]["sent_receipt"]
    clock[0] += timedelta(seconds=1)
    assert D.prune_progress(orch)
    assert item["id"] not in orch.state.notification_deliveries
    orch.now_local = clock[0]
    extended = progress(orch, hours=24)
    D.offer(orch, extended)
    with pytest.raises(ValueError, match="expired"):
        D.claim(orch, extended["id"], extended["hash"], "late-retry")


def test_never_claimed_progress_refresh_does_not_store_full_superseded_reports(orch):
    original = progress(orch, text="before")
    D.offer(orch, original)
    current = progress(orch, text="after")
    D.offer(orch, current)
    record = orch.state.notification_deliveries[original["id"]]
    assert "superseded" not in record and record["descriptor"]["payload"]["content"] == "after"
    with pytest.raises(ValueError, match="hash"):
        D.claim(orch, original["id"], original["hash"], "old-approval")
    approved = D.claim(orch, current["id"], current["hash"], "new-approval")
    assert approved["payload"]["content"] == "after"


def test_legacy_unclaimed_history_is_removed_but_active_payload_and_hash_stay(orch):
    item = progress(orch)
    D.offer(orch, item)
    record = orch.state.notification_deliveries[item["id"]]
    record["superseded"] = [{"descriptor": deepcopy(item), "prepared_at": D.now_iso()}] * 10
    assert D.prune_progress(orch)
    assert record is not orch.state.notification_deliveries[item["id"]]
    current = orch.state.notification_deliveries[item["id"]]
    assert "superseded" not in current and current["descriptor"] == item


def test_ever_claimed_known_failure_keeps_frozen_payload_for_retry(orch):
    item = progress(orch)
    attempt = claim(orch, item)
    D.result(orch, item["id"], attempt["execution_id"], "not_sent", "rejected")
    before = deepcopy(orch.state.notification_deliveries[item["id"]])
    assert not D.prune_progress(orch)
    assert D.offer(orch, progress(orch, text="new"))["payload"] == item["payload"]
    assert orch.state.notification_deliveries[item["id"]] == before
    assert D.claim(orch, item["id"], item["hash"], "retry")["payload"] == item["payload"]


def test_invitation_first_final_and_other_notifications_are_untouched(orch, clock):
    invitation = deepcopy(orch.state.notification_deliveries)
    scope = {"kind": "phase", "phase": "bug_bash", "step": "bugbash_updates", "until_flag": "poll_complete"}
    payload = {"chatId": stored_chat_id(orch.state), "content": "message", "contentType": "html"}
    for logical, completion in (
        ("step:bug_bash.bugbash_updates:once", {"kind": "step", "record_as": "bugbash_updates"}),
        ("bugbash:complete", {"kind": "step_data", "data": {"poll_complete": True}}),
        ("unrelated:notice", {}),
    ):
        item_scope = {**scope, "kind": "step"} if logical.startswith("step:") else scope
        item = D.descriptor(orch.state, logical, item_scope, "workiq_send_chat_message", payload, completion)
        D.offer(orch, item)
    before = deepcopy(orch.state.notification_deliveries)
    clock[0] += timedelta(days=365)
    assert not D.prune_progress(orch) and orch.state.notification_deliveries == before
    assert all(orch.state.notification_deliveries[k] == v for k, v in invitation.items())
    assert stored_chat_id(orch.state) == payload["chatId"]


@pytest.mark.parametrize("kind", ["state_match", "step_execution"])
def test_referenced_records_are_not_compacted_or_deleted(orch, clock, kind):
    item = progress(orch)
    sent(orch, item)
    if kind == "state_match":
        other = D.descriptor(orch.state, "other", {
            "kind": "phase", "phase": "bug_bash",
            "state_matches": [{"path": ["notification_deliveries", item["id"]], "hash": "reference"}],
        }, "workiq_send_chat_message", {"chatId": "test", "content": "test"})
        D.offer(orch, other)
    else:
        record = orch.state.get_step("bug_bash", "bugbash_updates")
        record.data["_execution"] = {"notification_id": item["id"]}
        orch.state.set_step("bug_bash", "bugbash_updates", record)
    before = deepcopy(orch.state.notification_deliveries)
    clock[0] += timedelta(days=365)
    assert not D.prune_progress(orch) and orch.state.notification_deliveries == before


def test_retention_uses_trusted_clock_not_preview_as_of(orch, clock):
    item = progress(orch)
    D.offer(orch, item)
    orch.now_local = clock[0] + timedelta(days=365)
    assert not D.prune_progress(orch)
    assert item["id"] in orch.state.notification_deliveries


@pytest.mark.parametrize("damage", ["status", "release", "hash", "id", "expires_at", "sent_at", "execution_id"])
def test_malformed_compact_receipts_fail_closed(orch, damage):
    item = progress(orch)
    sent(orch, item, {"id": "provider-message"})
    D.prune_progress(orch)
    record = orch.state.notification_deliveries[item["id"]]
    if damage == "status":
        record["status"] = "prepared"
    else:
        record["sent_receipt"][damage] = None if damage == "execution_id" else "invalid"
    with pytest.raises(ValueError, match="compact progress receipt"):
        D.preview(orch, record)
    with pytest.raises(ValueError):
        D.claim(orch, item["id"], item["hash"], "no-replay")


def test_pending_command_hides_settled_receipts_but_explicit_lookup_returns_them(orch, tmp_path):
    item = progress(orch)
    sent(orch, item, {"id": "message"})
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = Namespace(runs_root=str(tmp_path), release=orch.state.release_id, config=C.DEFAULT_CONFIG,
                     source="pending", id=None, as_of=None)
    result = delivery_cmd.prepare(args)
    assert item["id"] not in {n["id"] for n in result["notifications"]}
    stored = C.load_state(str(tmp_path), orch.state.release_id)
    assert D.is_progress_receipt(stored.notification_deliveries[item["id"]])
    args.id = item["id"]
    [notice] = delivery_cmd.prepare(args)["notifications"]
    assert not notice["permission_to_send"] and notice["delivery_status"] == "sent" and "payload" not in notice


def test_result_command_compacts_only_after_delivery_and_completion_are_saved(orch, tmp_path, capsys):
    item = progress(orch)
    attempt = claim(orch, item)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = Namespace(runs_root=str(tmp_path), release=orch.state.release_id, config=C.DEFAULT_CONFIG,
                     operation="result", id=item["id"], execution_id=attempt["execution_id"], outcome="sent",
                     evidence="accepted by provider", receipt_file=None, owner_review=False, as_of=None)
    assert delivery_cmd.cmd_notification(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "sent"
    stored = C.load_state(str(tmp_path), orch.state.release_id)
    assert D.is_progress_receipt(stored.notification_deliveries[item["id"]])
    assert delivery_cmd.cmd_notification(args) == 0
    assert not json.loads(capsys.readouterr().out)["recorded"]


def test_off_hours_tick_still_removes_expired_never_sent_updates(orch, tmp_path, clock, capsys):
    item = progress(orch)
    D.offer(orch, item)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    clock[0] = datetime.fromisoformat("2026-09-11T19:00:00-07:00")
    args = Namespace(runs_root=str(tmp_path), release=orch.state.release_id, config=C.DEFAULT_CONFIG,
                     now=clock[0].isoformat(), force=False)
    assert bugbash_update.cmd_post_bugbash_update(args) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "off_hours"
    assert item["id"] not in C.load_state(str(tmp_path), orch.state.release_id).notification_deliveries


def test_compaction_save_failure_preserves_success_and_never_replays_transport(orch, tmp_path, monkeypatch, capsys):
    item = progress(orch)
    attempt = claim(orch, item)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    real_save = C.save_state
    calls = []
    def fail_compaction(st, *args):
        calls.append(deepcopy(st.notification_deliveries[item["id"]]))
        if D.is_progress_receipt(calls[-1]):
            raise OSError("disk failure during retention")
        return real_save(st, *args)
    monkeypatch.setattr(C, "save_state", fail_compaction)
    args = Namespace(runs_root=str(tmp_path), release=orch.state.release_id, config=C.DEFAULT_CONFIG,
                     operation="result", id=item["id"], execution_id=attempt["execution_id"], outcome="sent",
                     evidence="provider accepted", receipt_file=None, owner_review=False, as_of=None)
    assert delivery_cmd.cmd_notification(args) == 1
    capsys.readouterr()
    assert len(calls) == 3 and calls[0]["status"] == "sent"
    assert calls[1]["completion"]["status"] == "applied"
    restored = C.load_state(str(tmp_path), orch.state.release_id)
    full = restored.notification_deliveries[item["id"]]
    assert not D.is_progress_receipt(full) and full["status"] == "sent"
    obj = Orchestrator(C.DEFAULT_CONFIG, restored, mocks={}, as_of=orch.now_local)
    assert not D.claim(obj, item["id"], item["hash"], "restart")["permission_to_send"]
    monkeypatch.setattr(C, "save_state", real_save)
    assert delivery_cmd.cmd_notification(args) == 0
    assert D.is_progress_receipt(C.load_state(str(tmp_path), orch.state.release_id).notification_deliveries[item["id"]])


def test_multi_day_run_retains_small_receipts_not_an_unbounded_html_history(orch, clock):
    start = clock[0].replace(hour=9, minute=0)
    for day in range(10):
        for hour in (9, 12, 15):
            clock[0] = (start + timedelta(days=day)).replace(hour=hour, minute=10)
            orch.now_local = clock[0]
            item = progress(orch, start=clock[0].replace(minute=0).isoformat())
            sent(orch, item, {"id": f"message-{day}-{hour}"})
            D.prune_progress(orch)
    routine = {key: value for key, value in orch.state.notification_deliveries.items()
               if key.startswith("bugbash:update:")}
    assert 1 <= len(routine) <= 6
    assert all(D.is_progress_receipt(record) for record in routine.values())
    assert len(json.dumps(routine)) < 5000  # Instead of 30 snapshots of 20KB each.
    assert "X" * 100 not in json.dumps(routine)


def test_sent_but_suppressed_routine_completion_still_prevents_replay(orch):
    item = progress(orch)
    sent(orch, item, {"id": "message"}, finalize=False)
    orch.state.target_month = "different"
    assert delivery_cmd.finish(orch, item["id"])
    assert orch.state.notification_deliveries[item["id"]]["completion"]["status"] == "suppressed"
    assert D.prune_progress(orch)
    assert not D.claim(orch, item["id"], item["hash"], "restart")["permission_to_send"]
