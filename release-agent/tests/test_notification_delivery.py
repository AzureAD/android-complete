"""Shared lifecycle/delivery contract; only isolated local fixtures, never transports."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

from orchestrator import automations, cli, cli_common as C, delivery as D, mocks, notifications, schedule
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState, StepState
from orchestrator.commands.delivery_cmd import finish

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setattr(mocks, "load_mocks", lambda: {})
    monkeypatch.setenv("RELEASE_AGENT_MOCKS", str(tmp_path / "missing.yaml"))
    st = ReleaseState(release_id="2026-09", ccd="2026-09-09", readiness_signed=True,
                      owner_email="owner@example.com", timezone="America/Los_Angeles")
    obj = Orchestrator(C.DEFAULT_CONFIG, st, mocks={}, as_of=datetime.fromisoformat("2026-09-09T12:00:00-07:00"))
    for p in obj.config["phases"]:
        if p["id"] == "ccd":
            break
        for s in p["steps"]:
            st.set_step(p["id"], s["id"], StepState(status="done"))
    st.current_phase = "ccd"
    return obj


def email(orch, channel="email", logical="example:2026-09-09"):
    return D.descriptor(
        orch.state, logical, {"kind": "phase", "phase": "ccd", "date": "2026-09-09"},
        "workiq_send_email" if channel == "email" else "m_send_teams_message",
        {"to": ["owner@example.com"], "subject": "Subject", "body": "Payload", "isHtml": False}
        if channel == "email" else {"message": "Payload"})


def acknowledge(orch, item, outcome="sent"):
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "worker")
    D.result(orch, item["id"], claim["execution_id"], outcome, "provider evidence")
    if outcome == "sent":
        finish(orch, item["id"])
    return claim


def test_partial_delivery_only_failed_channel_retries(orch):
    a, b = email(orch), email(orch, "teams")
    acknowledge(orch, a)
    first = acknowledge(orch, b, "not_sent")
    assert not D.available(orch, a) and D.available(orch, b)
    assert not D.claim(orch, a["id"], a["hash"], "B")["permission_to_send"]
    retry = D.claim(orch, b["id"], b["hash"], "B")
    assert retry["execution_id"] != first["execution_id"]
    assert retry["payload"] == b["payload"]


def test_ambiguous_and_lost_ack_never_replay(orch):
    item = email(orch)
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    with pytest.raises(ValueError, match="review"):
        D.claim(orch, item["id"], item["hash"], "A")
    D.result(orch, item["id"], claim["execution_id"], "uncertain", "timeout")
    with pytest.raises(ValueError, match="review"):
        D.result(orch, item["id"], claim["execution_id"], "not_sent", "guess")
    D.result(orch, item["id"], claim["execution_id"], "sent", "owner found provider receipt",
             {"raw": "actual-response"}, review=True)
    before = copy.deepcopy(orch.state.notification_deliveries)
    assert not D.result(orch, item["id"], claim["execution_id"], "sent", "replay", {"raw": "other"})
    assert before == orch.state.notification_deliveries


def test_claim_uses_approved_snapshot_not_later_preparation(orch):
    item = email(orch)
    D.offer(orch, item)
    D.claim(orch, item["id"], item["hash"], "first")
    changed = copy.deepcopy(item)
    changed["payload"]["body"] = "changed"
    changed["hash"] = D.fingerprint({k: v for k, v in changed.items() if k != "hash"})
    assert D.offer(orch, changed)["payload"] == item["payload"]
    with pytest.raises(ValueError, match="hash"):
        D.claim(orch, item["id"], changed["hash"], "worker")


@pytest.mark.parametrize("record_as", ["pr_reminder", "", None])
def test_preparation_rejects_builder_ownership_mismatch(orch, monkeypatch, record_as):
    from orchestrator.commands.step_action import prepare_step
    from orchestrator.outcomes import NeedsSkill
    from steps.ccd import final_reminder
    monkeypatch.setattr(final_reminder, "build", lambda _st: NeedsSkill(
        tool="workiq_send_email", payload={"to": ["owner@example.com"], "body": "PR reminder"},
        record_as=record_as, outbound=True))
    args = SimpleNamespace(phase="ccd", step="final_reminder", release=orch.state.release_id)
    before = copy.deepcopy(orch.state)
    with pytest.raises(ValueError, match="record_as"):
        prepare_step(args, orch.state, orch)
    assert orch.state == before


def test_claim_and_finalization_validate_captured_step_owner(orch):
    from orchestrator.commands.step_action import prepare_step
    args = SimpleNamespace(phase="ccd", step="final_reminder", release=orch.state.release_id)
    item = prepare_step(args, orch.state, orch)["notifications"][0]
    assert item["completion"]["record_as"] == "final_reminder"
    D.offer(orch, item)
    record = orch.state.notification_deliveries[item["id"]]
    record["descriptor"]["completion"]["record_as"] = "pr_reminder"
    record["descriptor"]["hash"] = D.fingerprint(
        {k: v for k, v in record["descriptor"].items() if k != "hash"})
    with pytest.raises(ValueError, match="record_as"):
        D.claim(orch, item["id"], record["descriptor"]["hash"], "A")
    assert not record["attempts"]
    record["descriptor"] = copy.deepcopy(item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider receipt")
    record["descriptor"]["scope"]["step"] = "pr_reminder"
    record["descriptor"]["hash"] = D.fingerprint(
        {k: v for k, v in record["descriptor"].items() if k != "hash"})
    with pytest.raises(ValueError, match="record_as"):
        finish(orch, item["id"])
    assert not orch.state.is_done("ccd", "final_reminder")
    assert not orch.state.is_done("ccd", "pr_reminder")
    record["descriptor"] = copy.deepcopy(item)
    assert finish(orch, item["id"])
    assert orch.state.is_done("ccd", "final_reminder")


@pytest.mark.parametrize("change", ["owner_email", "ccd"])
def test_unclaimed_step_refresh_preserves_audit_and_requires_new_approval(
        orch, tmp_path, monkeypatch, capsys, change):
    current = datetime.fromisoformat("2026-09-11T12:00:00-07:00")
    monkeypatch.setattr(schedule, "now_local", lambda _tz: current)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    base = ["--runs-root", str(tmp_path), "notification"]
    scope = ["--release", orch.state.release_id]
    prepare = base + ["prepare"] + scope + ["--source", "step", "--phase", "ccd",
                                            "--step", "final_reminder"]
    assert cli.main(prepare) == 0
    first = json.loads(capsys.readouterr().out)["notifications"][0]
    st = C.load_state(str(tmp_path), orch.state.release_id)
    original = copy.deepcopy(st.notification_deliveries[first["id"]])
    setattr(st, change, "new-owner@example.com" if change == "owner_email" else "2026-09-10")
    C.save_state(st, str(tmp_path), st.release_id)
    assert cli.main(prepare) == 0
    fresh = json.loads(capsys.readouterr().out)["notifications"][0]
    assert first["id"] == fresh["id"] and first["hash"] != fresh["hash"]
    record = C.load_state(str(tmp_path), st.release_id).notification_deliveries[first["id"]]
    assert record["superseded"][0]["descriptor"] == original["descriptor"]
    assert record["superseded"][0]["prepared_at"] == original["prepared_at"]
    assert record["superseded"][0]["superseded_at"]
    claim = base + ["claim"] + scope + ["--id", first["id"], "--executor", "A", "--hash"]
    assert cli.main(claim + [first["hash"]]) == 1
    assert not json.loads(capsys.readouterr().out)["permission_to_send"]
    assert cli.main(claim + [fresh["hash"]]) == 0
    granted = json.loads(capsys.readouterr().out)
    assert granted["permission_to_send"] and granted["payload"] == fresh["payload"]
    record = C.load_state(str(tmp_path), st.release_id).notification_deliveries[first["id"]]
    assert len(record["attempts"]) == len(record["superseded"]) == 1


@pytest.mark.parametrize("status", ["claimed", "uncertain", "sent", "not_sent"])
def test_source_or_payload_change_cannot_replace_ever_claimed_work(orch, status):
    item = email(orch)
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    if status != "claimed":
        D.result(orch, item["id"], claim["execution_id"], status, "provider evidence")
    original = copy.deepcopy(orch.state.notification_deliveries[item["id"]])
    orch.state.owner_email = "new-owner@example.com"
    changed = D.descriptor(
        orch.state, item["id"].rsplit(":", 1)[0],
        {**item["scope"], "release_matches": {"owner_email": orch.state.owner_email}},
        item["tool"], {**item["payload"], "to": [orch.state.owner_email], "body": "new body"})
    assert D.offer(orch, changed)["hash"] == item["hash"]
    assert orch.state.notification_deliveries[item["id"]] == original
    with pytest.raises(ValueError, match="hash"):
        D.claim(orch, changed["id"], changed["hash"], "B")


@pytest.mark.parametrize("expiry", [
    {"date": "2026-09-09"}, {"expires_at": "2026-09-09T18:00:00-07:00"},
])
def test_cli_claim_cannot_rewind_expired_work(orch, tmp_path, monkeypatch, capsys, expiry):
    monkeypatch.setattr(schedule, "now_local", lambda _tz:
                        datetime.fromisoformat("2026-09-10T00:02:00-07:00"))
    item = D.descriptor(orch.state, "expired", {"kind": "phase", "phase": "ccd", **expiry},
                        "workiq_send_email", {"to": ["owner@example.com"], "body": "expired"})
    D.offer(orch, item)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    base = ["--runs-root", str(tmp_path), "notification"]
    scope = ["--release", orch.state.release_id, "--id", item["id"]]
    assert cli.main(base + ["prepare"] + scope + [
        "--source", "pending", "--as-of", "2026-09-09T12:00:00-07:00"]) == 0
    assert not json.loads(capsys.readouterr().out)["notifications"][0]["stop_reason"]
    claim = base + ["claim"] + scope + ["--hash", item["hash"], "--executor", "A"]
    with pytest.raises(SystemExit) as rejected:
        cli.main(claim + ["--as-of", "2026-09-09"])
    assert rejected.value.code == 2
    capsys.readouterr()
    assert cli.main(claim) == 1
    out = json.loads(capsys.readouterr().out)
    assert "expired" in out["error"] and not out["permission_to_send"]
    assert not C.load_state(str(tmp_path), orch.state.release_id).notification_deliveries[item["id"]]["attempts"]


def test_cli_claim_rechecks_real_step_fire_time(orch, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(schedule, "now_local", lambda _tz:
                        datetime.fromisoformat("2026-09-09T08:45:00-07:00"))
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    base = ["--runs-root", str(tmp_path), "notification"]
    scope = ["--release", orch.state.release_id]
    assert cli.main(base + ["prepare"] + scope + ["--source", "step", "--phase", "ccd",
                    "--step", "final_reminder", "--as-of", "2026-09-09"]) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    assert cli.main(base + ["claim"] + scope + ["--id", item["id"], "--hash", item["hash"],
                                               "--executor", "A"]) == 1
    assert not json.loads(capsys.readouterr().out)["permission_to_send"]
    assert not C.load_state(str(tmp_path), orch.state.release_id).notification_deliveries[item["id"]]["attempts"]


def test_cli_receipt_after_midnight_needs_no_clock_override(orch, tmp_path, monkeypatch, capsys):
    item = D.descriptor(orch.state, "midnight", email(orch)["scope"], "workiq_send_email",
                        email(orch)["payload"],
                        {"release_field": "last_status_email_date", "date": "2026-09-09"})
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    monkeypatch.setattr(schedule, "now_local", lambda _tz:
                        datetime.fromisoformat("2026-09-10T00:02:00-07:00"))
    assert cli.main(["--runs-root", str(tmp_path), "notification", "result",
                    "--release", orch.state.release_id, "--id", item["id"],
                    "--execution-id", claim["execution_id"], "--outcome", "sent",
                    "--evidence", "provider receipt"]) == 0
    capsys.readouterr()
    assert C.load_state(str(tmp_path), orch.state.release_id).last_status_email_date == "2026-09-09"


def test_midnight_ack_preserves_prepared_day(orch):
    item = email(orch)
    item["completion"] = {"release_field": "last_status_email_date", "date": "2026-09-09"}
    item["hash"] = D.fingerprint({k: v for k, v in item.items() if k != "hash"})
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    orch.now_local = datetime.fromisoformat("2026-09-10T00:01:00-07:00")
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider success")
    assert finish(orch, item["id"])
    assert orch.state.last_status_email_date == "2026-09-09"


def test_explicit_clock_uses_owner_day_and_rejects_missing_timezone(orch, tmp_path):
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = SimpleNamespace(as_of="2026-09-10T06:55:00+00:00")
    _, local = C.load_orch(str(tmp_path), orch.state.release_id, C.DEFAULT_CONFIG, C.parse_as_of(args))
    assert local.now_local.isoformat() == "2026-09-09T23:55:00-07:00"
    assert local.as_of.isoformat() == "2026-09-09"
    assert D.scope_reason(local, email(local)["scope"]) == ""
    item = email(local)
    D.offer(local, item)
    claim = D.claim(local, item["id"], item["hash"], "worker")
    D.result(local, item["id"], claim["execution_id"], "sent", "provider success")
    local.tz = None
    assert "timezone" in D.scope_reason(local, {"kind": "release"})
    assert not finish(local, item["id"])
    assert not local.state.notification_deliveries[item["id"]].get("completion")
    with pytest.raises(ValueError):
        C.parse_as_of(SimpleNamespace(as_of="invalid"))


def test_native_auth_legacy_engineer_is_not_delivery_evidence(orch):
    from steps.bug_bash import notify_native_auth
    from orchestrator.outcomes import Blocked
    orch.state.set_step("bug_bash", "notify_native_auth",
                        StepState(status="pending", data={"engineer": "engineer@example.com"}))
    assert isinstance(notify_native_auth.build(orch.state), Blocked)


@pytest.mark.parametrize("content", ["[]", "''", "true"])
def test_empty_malformed_config_cannot_enable_default_delivery(tmp_path, content):
    (tmp_path / "notifications.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        notifications.load_config(str(tmp_path / "phases.yaml"))


@pytest.mark.parametrize("state", ["halted", "complete", "phase_done"])
def test_stale_claims_stop_even_if_prepared(orch, state):
    item = email(orch)
    D.offer(orch, item)
    if state == "halted":
        orch.state.halted = True
    elif state == "complete":
        orch.state.status = "complete"
    else:
        for s in orch.config["phases"][1]["steps"]:
            orch.state.set_step("ccd", s["id"], StepState(status="done"))
    with pytest.raises(ValueError):
        D.claim(orch, item["id"], item["hash"], "A")


def test_two_processes_only_one_send_permission(orch, tmp_path):
    item = D.descriptor(orch.state, "concurrent-example", {"kind": "release"},
                        "workiq_send_email", {"to": ["owner@example.com"], "body": "test"})
    D.offer(orch, item)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    command = [sys.executable, "-m", "orchestrator.cli", "--runs-root", str(tmp_path),
               "notification", "claim", "--release", orch.state.release_id,
               "--id", item["id"], "--hash", item["hash"], "--executor"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(subprocess.run, command + [who], cwd=ROOT, capture_output=True,
                               text=True, timeout=40) for who in ("A", "B")]
        rows = [json.loads(f.result().stdout) for f in futures]
    assert sum(bool(r.get("permission_to_send")) for r in rows) == 1
    assert "payload" not in next(r for r in rows if not r["permission_to_send"])


def test_notify_is_read_only_and_order_independent(orch, tmp_path):
    from orchestrator.commands.notify import _notify_payload
    for s in ("final_reminder", "pr_reminder", "localization"):
        orch.state.set_step("ccd", s, StepState(status="done"))
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    path = Path(C.state_path(str(tmp_path), orch.state.release_id))
    before = path.read_bytes()
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           as_of="2026-09-09", force=False)
    a = _notify_payload(args, orch.state.release_id, False)
    b = _notify_payload(args, orch.state.release_id, False)
    assert path.read_bytes() == before
    assert a == b


def test_config_preserves_partner_recipients_and_rejects_malformed(tmp_path):
    cfg = notifications.load_config(C.DEFAULT_CONFIG)
    assert cfg["status_email"]["recipients"]
    (tmp_path / "notifications.yaml").write_text("channels: {email: 'false'}", encoding="utf-8")
    with pytest.raises(ValueError):
        notifications.load_config(str(tmp_path / "phases.yaml"))


def test_cleanup_backstop_and_suspend(orch):
    entry = {"id": "old", "name": "old", "release": orch.state.release_id, "scope": "release",
             "steps": [], "cleanup_when": None}
    assert not automations.cleanup_plan(orch.state, [entry], C.DEFAULT_CONFIG)["removals"]
    orch.state.halted = True
    assert not automations.cleanup_plan(orch.state, [entry], C.DEFAULT_CONFIG)["removals"]
    orch.state.status = "complete"
    entries = [entry, {**entry, "id": "manual", "cleanup_when": "manual"},
               {**entry, "id": "shared", "scope": "shared"}]
    assert [r["id"] for r in automations.cleanup_plan(orch.state, entries, C.DEFAULT_CONFIG)["removals"]] == ["old"]


def test_noon_skip_without_start_cleanup_and_rc_phase_scope(orch):
    defs = {d["slug"]: d for d in automations.load_defs(C.DEFAULT_CONFIG)}
    orch.state.set_step("ccd", "localization", StepState(status="skipped"))
    entry = {**defs["ccd-noon"], "id": "noon", "name": "noon",
             "scope": "release", "release": orch.state.release_id}
    assert automations.cleanup_plan(orch.state, [entry], C.DEFAULT_CONFIG)["removals"]
    assert defs["build-verify-rc-poller"]["cleanup_when"] == "phase_done:build_verify"


def test_generated_worker_contract_cannot_omit_lifecycle():
    for spec in automations.plan(C.DEFAULT_CONFIG, "2026-09", "2026-09-09")["automations"]:
        assert spec["cleanup_when"]
        assert "notification claim --release 2026-09" in spec["prompt"]
        assert "finally" in spec["prompt"]
        assert "deregister only after deletion succeeds" in spec["prompt"]


@pytest.mark.parametrize("fail_save_at", [1, 2])
def test_send_success_persistence_failure_never_grants_resend(orch, tmp_path, monkeypatch, capsys,
                                                            fail_save_at):
    """Crash before receipt save or after receipt save/before completion are both recoverable."""
    monkeypatch.setattr(schedule, "now_local", lambda _tz: orch.now_local)
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    base = ["--runs-root", str(tmp_path), "notification"]
    scope = ["--release", orch.state.release_id]
    assert cli.main(base + ["prepare"] + scope + [
        "--source", "step", "--phase", "ccd", "--step", "final_reminder"]) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    assert cli.main(base + ["claim"] + scope + [
        "--id", item["id"], "--hash", item["hash"], "--executor", "A"]) == 0
    claim = json.loads(capsys.readouterr().out)
    save = C.save_state
    calls = 0

    def fail_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_save_at:
            raise OSError("simulated disk failure")
        return save(*args, **kwargs)

    monkeypatch.setattr(C, "save_state", fail_save)
    result = base + ["result"] + scope + [
        "--id", item["id"], "--execution-id", claim["execution_id"],
        "--outcome", "sent", "--evidence", "provider-confirmed receipt"]
    assert cli.main(result) == 1
    capsys.readouterr()
    persisted, current = C.load_orch(str(tmp_path), orch.state.release_id, C.DEFAULT_CONFIG,
                                    orch.now_local)
    assert not persisted.is_done("ccd", "final_reminder")
    if fail_save_at == 1:
        with pytest.raises(ValueError, match="review"):
            D.claim(current, item["id"], item["hash"], "B")
    else:
        assert not D.claim(current, item["id"], item["hash"], "B")["permission_to_send"]
    monkeypatch.setattr(C, "save_state", save)
    assert cli.main(result) == 0
    capsys.readouterr()
    restored = C.load_state(str(tmp_path), orch.state.release_id)
    assert restored.is_done("ccd", "final_reminder")
    assert len(restored.notification_deliveries[item["id"]]["attempts"]) == 1


def test_suspended_ack_defers_completion_and_skip_suppresses_it(orch):
    step = StepState(status="in_flight", data={"build_id": "123"})
    orch.state.set_step("ccd", "localization", step)
    item = D.descriptor(
        orch.state, "localization:123:initial",
        {"kind": "step", "phase": "ccd", "step": "localization"},
        "workiq_send_chat_message", {"chatId": "configured-chat", "content": "review PR"},
        {"kind": "step_data", "stamp": ["pr_announced_at"]})
    D.offer(orch, item)
    claim = D.claim(orch, item["id"], item["hash"], "A")
    orch.state.halted = True
    D.result(orch, item["id"], claim["execution_id"], "sent", "provider receipt")
    assert not finish(orch, item["id"])
    assert not orch.state.get_step("ccd", "localization").data.get("pr_announced_at")
    orch.state.halted = False
    assert finish(orch, item["id"])
    original = copy.deepcopy(orch.state.get_step("ccd", "localization"))
    assert not finish(orch, item["id"])
    assert original == orch.state.get_step("ccd", "localization")

    second = D.descriptor(orch.state, "localization:123:deadline", item["scope"], item["tool"],
                          item["payload"], {"kind": "step_data", "stamp": ["merge_deadline_alert_at"]})
    D.offer(orch, second)
    claim = D.claim(orch, second["id"], second["hash"], "B")
    step.status, step.note, step.by = "skipped", "owner declined", "human"
    orch.state.set_step("ccd", "localization", step)
    D.result(orch, second["id"], claim["execution_id"], "sent", "provider receipt")
    assert finish(orch, second["id"])
    assert orch.state.get_step("ccd", "localization") == step
    assert orch.state.notification_deliveries[second["id"]]["completion"]["status"] == "suppressed"


def test_required_timeout_only_blocks_after_success(orch, tmp_path, capsys):
    from orchestrator.commands.localization import cmd_check_localization, cmd_record_localization_run
    orch.state.set_step("ccd", "localization", StepState(
        status="in_flight", data={"build_id": "123", "started_at": "2026-09-09T11:00:00-07:00"}))
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           release=orch.state.release_id, as_of=None,
                           now="2026-09-09T15:00:00-07:00", complete="false", pr_status=None,
                           logs=None, logs_file=None)
    assert cmd_check_localization(args) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    st, current = C.load_orch(str(tmp_path), orch.state.release_id, C.DEFAULT_CONFIG, orch.now_local)
    assert st.get_step("ccd", "localization").status == "in_flight"
    current.now_local = datetime.fromisoformat(args.now)
    claim = D.claim(current, item["id"], item["hash"], "A")
    D.result(current, item["id"], claim["execution_id"], "not_sent", "provider rejected request")
    assert D.has_pending(current, ["ccd.localization"])
    claim = D.claim(current, item["id"], item["hash"], "B")
    D.result(current, item["id"], claim["execution_id"], "sent", "provider receipt")
    finish(current, item["id"])
    assert st.get_step("ccd", "localization").status == "blocked"
    assert not D.has_pending(current, ["ccd.localization"])
    C.save_state(st, str(tmp_path), st.release_id)
    before = Path(C.state_path(str(tmp_path), st.release_id)).read_bytes()
    args.build_id, args.started_at, args.run_url = "999", None, None
    assert cmd_record_localization_run(args) == 0
    assert Path(C.state_path(str(tmp_path), st.release_id)).read_bytes() == before


def test_future_timeout_preview_cannot_authorize_early_send(orch, tmp_path, capsys, monkeypatch):
    from orchestrator.commands.localization import cmd_check_localization
    orch.state.set_step("ccd", "localization", StepState(
        status="in_flight", data={"build_id": "123", "started_at": "2026-09-09T11:00:00-07:00"}))
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           release=orch.state.release_id, as_of=None,
                           now="2026-09-09T15:00:00-07:00", complete="false", pr_status=None,
                           logs=None, logs_file=None)
    assert cmd_check_localization(args) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    monkeypatch.setattr(schedule, "now_local", lambda zone: orch.now_local.astimezone(zone))
    claim_args = ["--config", C.DEFAULT_CONFIG, "--runs-root", str(tmp_path), "notification",
                  "claim", "--release", orch.state.release_id, "--id", item["id"],
                  "--hash", item["hash"], "--executor", "test-worker"]
    assert cli.main(claim_args) == 1
    assert "notification not due" in json.loads(capsys.readouterr().out)["error"]
    record = C.load_state(str(tmp_path), orch.state.release_id).notification_deliveries[item["id"]]
    assert record["status"] == "prepared" and record["attempts"] == []
    orch.now_local = datetime.fromisoformat("2026-09-09T14:00:00-07:00")
    assert cli.main(claim_args) == 0
    assert json.loads(capsys.readouterr().out)["permission_to_send"]


@pytest.mark.parametrize("command_name", ["cmd_check_localization", "cmd_post_bugbash_update", "cmd_poll_rc"])
def test_halted_poll_commands_never_offer_send(orch, tmp_path, capsys, monkeypatch, command_name):
    from orchestrator.commands import localization, bugbash_update, rc_poll
    orch.state.halted = True
    orch.state.set_step("build_verify", "mrwp_ecs", StepState(
        status="in_flight", data={"in_flight_since": "2026-09-01T00:00:00Z"}))
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    monkeypatch.setattr(Orchestrator, "run_until_gate", lambda _self: [])
    module = next(m for m in (localization, bugbash_update, rc_poll) if hasattr(m, command_name))
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           release=orch.state.release_id, as_of="2026-09-09",
                           now="2026-09-09T15:00:00-07:00", force=True)
    assert getattr(module, command_name)(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert not out.get("notifications") and not out.get("permission_to_send")


def test_new_rc_closing_step_and_expiry_invalidate_prepared_work(orch):
    orch.state.pipeline_runs = {"rcs": [{"rc": "first"}]}
    scope = {"kind": "phase", "phase": "ccd",
             "state_matches": [{"path": ["pipeline_runs", "rcs", -1, "rc"], "value": "first"}],
             "until_steps": ["ccd.localization"], "expires_at": "2026-09-09T18:00:00-07:00"}
    item = D.descriptor(orch.state, "scope-example", scope, "workiq_send_email",
                        {"to": ["configured@example.com"], "subject": "subject", "body": "text"})
    D.offer(orch, item)
    orch.state.pipeline_runs["rcs"].append({"rc": "second"})
    with pytest.raises(ValueError, match="checkpoint changed"):
        D.claim(orch, item["id"], item["hash"], "A")
    orch.state.pipeline_runs["rcs"].pop()
    orch.state.set_step("ccd", "localization", StepState(status="skipped"))
    with pytest.raises(ValueError, match="closing step"):
        D.claim(orch, item["id"], item["hash"], "A")
    orch.state.set_step("ccd", "localization", StepState())
    orch.now_local = datetime.fromisoformat("2026-09-09T18:00:00-07:00")
    with pytest.raises(ValueError, match="deadline expired"):
        D.claim(orch, item["id"], item["hash"], "A")


def test_incomplete_or_corrupt_ledger_requires_owner_recovery(orch):
    item = email(orch)
    D.offer(orch, item)
    record = orch.state.notification_deliveries[item["id"]]
    record["descriptor"]["payload"]["body"] = "tampered after approval"
    with pytest.raises(ValueError, match="corrupt"):
        D.claim(orch, item["id"], item["hash"], "A")
    entry = {"id": "worker", "name": "worker", "scope": "release", "release": orch.state.release_id,
             "steps": ["ccd.localization"], "cleanup_when": "steps_settled"}
    orch.state.set_step("ccd", "localization", StepState(status="blocked"))
    plan = automations.cleanup_plan(orch.state, [entry], C.DEFAULT_CONFIG)
    assert not plan["removals"] and plan["problems"]
    orch.state.notification_deliveries[item["id"]] = {"status": "sent"}
    with pytest.raises(ValueError, match="Incomplete"):
        D.preview(orch, orch.state.notification_deliveries[item["id"]])


def test_outbound_notification_steps_declare_contract():
    import ast
    import steps
    found = []
    for path in (ROOT / "steps").glob("*/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and node.value in D.TRANSPORTS for node in ast.walk(tree)):
            continue
        module = steps.get_step(path.parent.name, path.stem)
        assert module and module.NOTIFICATION, str(path)
        found.append(path.stem)
    assert len(found) >= 10


def test_release_wide_prompt_pins_all_advancement_and_cleanup():
    text = (ROOT / "skill" / "reference" / "starting-and-scheduling.md").read_text(encoding="utf-8")
    block = text[text.index("**prompt:**"):text.index("## Ensure the daily partner")]
    for command in ("status", "next", "tick", "automation cleanup"):
        assert f"{command} --release <YYYY-MM>" in block
    assert "finally" in block and "courtesy copies" in block


def test_no_delivery_done_revalidates_instead_of_inventing_receipt(orch, tmp_path, monkeypatch, capsys):
    from orchestrator.commands.notice import cmd_record_step
    from orchestrator.outcomes import Done, NeedsSkill
    from steps.ccd import final_reminder
    C.save_state(orch.state, str(tmp_path), orch.state.release_id)
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           release=orch.state.release_id, as_of="2026-09-09",
                           phase="ccd", step="final_reminder", status="pass",
                           detail="no external work required", execution_id=None)
    monkeypatch.setattr(final_reminder, "build", lambda _st: NeedsSkill(
        tool="workiq_send_email", payload={"to": ["configured@example.com"], "body": "hello"},
        record_as="final_reminder", outbound=True))
    assert cmd_record_step(args) == 1
    capsys.readouterr()
    assert not C.load_state(str(tmp_path), args.release).is_done("ccd", "final_reminder")
    monkeypatch.setattr(final_reminder, "build", lambda _st: Done("Verified nothing to send"))
    assert cmd_record_step(args) == 0
    result = C.load_state(str(tmp_path), args.release)
    assert result.is_done("ccd", "final_reminder") and not result.notification_deliveries


def test_bugbash_final_retries_failure_not_trigger_completion(orch, tmp_path, monkeypatch, capsys):
    from tests._harness import _active_phase, _bb_updates_state
    from orchestrator.commands.bugbash_update import cmd_post_bugbash_update
    from steps.bug_bash import bugbash_updates
    st = _bb_updates_state()
    _active_phase(st, "bug_bash")
    st.set_step("bug_bash", "bugbash_updates", StepState(status="done"))
    C.save_state(st, str(tmp_path), st.release_id)
    monkeypatch.setattr(bugbash_updates, "gather", lambda _st: (
        True, {"total": 2, "done": 2, "remaining": 0, "owners": []}, ""))
    args = SimpleNamespace(runs_root=str(tmp_path), config=C.DEFAULT_CONFIG,
                           release=st.release_id, now="2026-08-21T10:00:00-07:00", force=True)
    assert cmd_post_bugbash_update(args) == 0
    item = json.loads(capsys.readouterr().out)["notifications"][0]
    st, obj = C.load_orch(str(tmp_path), st.release_id, C.DEFAULT_CONFIG,
                          datetime.fromisoformat(args.now))
    claim = D.claim(obj, item["id"], item["hash"], "A")
    D.result(obj, item["id"], claim["execution_id"], "not_sent", "provider rejected")
    C.save_state(st, str(tmp_path), st.release_id)
    assert not st.get_step("bug_bash", "bugbash_updates").data.get("poll_complete")
    assert cmd_post_bugbash_update(args) == 0
    again = json.loads(capsys.readouterr().out)["notifications"][0]
    assert again["id"] == item["id"] and again["hash"] == item["hash"]
    claim = D.claim(obj, again["id"], again["hash"], "B")
    D.result(obj, again["id"], claim["execution_id"], "sent", "provider receipt")
    finish(obj, again["id"])
    C.save_state(st, str(tmp_path), st.release_id)
    assert st.get_step("bug_bash", "bugbash_updates").data["poll_complete"]
    assert cmd_post_bugbash_update(args) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "stopped"


def test_stale_terminal_rc_report_does_not_retire_poller(orch):
    from tests._harness import _active_phase
    _active_phase(orch.state, "build_verify")
    orch.state.set_step("build_verify", "rc_report", StepState(status="done"))
    spec = next(s for s in automations.load_defs(C.DEFAULT_CONFIG)
                if s["slug"] == "build-verify-rc-poller")
    entry = {**spec, "scope": "release", "release": orch.state.release_id, "id": "rc", "name": "rc"}
    assert not automations.cleanup_plan(orch.state, [entry], C.DEFAULT_CONFIG)["removals"]


def test_partner_preparation_honors_configured_closing_step(orch, monkeypatch):
    from tests._harness import _active_phase
    from orchestrator.commands import status_email_cmd
    _active_phase(orch.state, "finalize")
    orch.state.current_phase = "finalize"
    orch.now_local = datetime.fromisoformat("2026-09-11T17:00:00-07:00")
    monkeypatch.setattr(status_email_cmd, "_broker_changes", lambda _st: [])
    args = SimpleNamespace(config=C.DEFAULT_CONFIG, release=orch.state.release_id, force=True,
                           send_to=None)
    prepared = status_email_cmd.prepare_status_email(args, orch.state, orch)
    item = prepared["notifications"][0]
    D.offer(orch, item)
    orch.state.set_step("finalize", "final_status_email", StepState(status="skipped"))
    assert status_email_cmd.prepare_status_email(args, orch.state, orch)["skip"]
    with pytest.raises(ValueError, match="closing step"):
        D.claim(orch, item["id"], item["hash"], "A")
