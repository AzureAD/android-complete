"""The canonical eleven-step phase, real handlers/CLI, and offline providers only."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from orchestrator import automations, cli, cli_common as C, mocks
from orchestrator.registry import AutomationRegistry
from orchestrator.state import ReleaseState
from steps.bug_bash import activate_chat, send_invite, ui_results
from tests._automation import observed
from tests._context import context
from tests.test_bugbash_chat_binding import write_observations
from tests.test_bugbash_mentions import member_envelope
from tests.test_distribution_current_layouts import (
    cli as distribute, distribution_inputs, TESTER,
)
from tests.test_phase3_current_layouts import current_layouts, OWNER
from tools import testplans as T


@pytest.mark.parametrize("current_layouts", [{"full_phase": True}], indirect=True)
@pytest.mark.parametrize("chat_binding", ["native", "owner_confirmed"])
@pytest.mark.parametrize("completion", ["all_complete", "owner_signoff"])
def test_complete_phase3_with_current_layouts_and_durable_cli_handoffs(
        current_layouts, monkeypatch, tmp_path, capsys, record_property, chat_binding, completion):
    ado, cloned, protected_points, _, fill = current_layouts
    state = fill()
    release = state.release_id
    config = str(tmp_path / "config" / "phases.yaml")
    runs = str(tmp_path / "runs")
    prefix = ["--config", config, "--runs-root", runs]
    original_rc = deepcopy(cloned.pipeline_runs)
    shared_cases = deepcopy(ado.shared)
    trace = ["clone_plans_broker", "clone_plans_auth", "ui_test_status"]

    def load():
        return ReleaseState.load(C.state_path(runs, release))

    def command(*arguments, expected=0, json_output=True):
        capsys.readouterr()
        code = cli.main([*prefix, *arguments, "--release", release])
        output = capsys.readouterr().out
        assert code == expected, output
        return json.loads(output) if json_output else output

    def deliver(item, receipt):
        assert not item["permission_to_send"] and not item["stop_reason"]
        claim = command(
            "notification", "claim", "--id", item["id"], "--hash", item["hash"],
            "--executor", "offline-phase3-worker")
        assert claim["permission_to_send"]
        path = tmp_path / "synthetic-provider-receipt.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        result = command(
            "notification", "result", "--id", item["id"],
            "--execution-id", claim["execution_id"], "--outcome", "sent",
            "--evidence", "Synthetic offline provider accepted the exact payload",
            "--receipt-file", str(path))
        assert result["status"] == "sent"
        capsys.readouterr()

    def prepare(step, *parameters):
        output = command(
            "notification", "prepare", "--source", "step", "--phase", "bug_bash",
            "--step", step, *parameters)
        assert len(output["notifications"]) == 1, output
        assert not load().is_done("bug_bash", step)
        return output["notifications"][0]

    assert state.is_done("bug_bash", "ui_test_status")
    result = ui_results.completed_result(context(state))
    assert result["broker"]["failed_case_ids"] and result["auth"]["failed_case_ids"]
    assert result["monthly"]["applied_points"]
    assert not state.gate_decisions
    inputs = distribution_inputs(monkeypatch, state)
    step_inputs = {
        "bug_bash.clone_plans_broker": {"name": "TEST ONLY - current Broker"},
        "bug_bash.clone_plans_auth": {
            "name": "TEST ONLY - current Auth", "layout": result["auth"]["target"]["layout"],
        },
        "bug_bash.distribute_tests": inputs,
        "bug_bash.send_invite": {"flags": "{}", "send_to": [OWNER, TESTER]},
    }
    code, preview = distribute(monkeypatch, state, tmp_path, capsys)
    assert code == 0 and not preview["permission_to_execute"]
    code, applied = distribute(
        monkeypatch, state, tmp_path, capsys, "--apply", "--review-hash", preview["review_hash"])
    assert code == 0 and applied["valid"]
    assert load().is_done("bug_bash", "distribute_tests")
    trace.append("distribute_tests")
    monkeypatch.setattr(mocks, "load_mocks", lambda *a: deepcopy(step_inputs))

    before = load()
    pending = command(
        "step-action", "--phase", "bug_bash", "--step", "activate_chat")
    assert pending["kind"] == "blocked"
    assert load().steps == before.steps
    item = prepare("send_invite")
    assert item["payload"]["attendees"] == [OWNER, TESTER]
    chat_id = "19:meeting_CURRENT@thread.v2"
    join_url = "https://teams.microsoft.com/l/meetup-join/19%3Ameeting_CURRENT%40thread.v2/0"
    deliver(item, {"id": "offline-event", "onlineMeeting": {"joinUrl": join_url}})
    assert load().is_done("bug_bash", "send_invite")
    trace.append("send_invite")

    missing = command("record-bugbash-chat")
    assert missing["status"] == "needs-connector-evidence" and not missing["completed"]
    state = load()
    invite = send_invite.delivered_invite(context(state))
    native = {
        "me": {"id": "offline-owner", "userPrincipalName": OWNER},
        "event": {
            "id": invite["event_id"], "subject": invite["subject"], "isCancelled": False,
            "isOnlineMeeting": True, "organizer": {"emailAddress": {"address": OWNER}},
            "attendees": [
                {"type": "required", "emailAddress": {"address": upn}} for upn in invite["attendees"]
            ],
            "start": {"dateTime": invite["start"], "timeZone": invite["timeZone"]},
            "end": {"dateTime": invite["end"], "timeZone": invite["timeZone"]},
            "onlineMeeting": {"joinUrl": join_url},
        },
        "chat": {"id": chat_id, "topic": invite["subject"], "chatType": "meeting"},
    }
    if chat_binding == "native":
        flags = write_observations(tmp_path, invite, native)
        command("record-bugbash-chat", *flags, json_output=False)
        verification = "native_connectors"
    else:
        manual = command("record-bugbash-chat", "--chat-link", join_url)
        assert not manual["permission_to_record"] and not manual["permission_to_send"]
        assert not load().is_done("bug_bash", "activate_chat")
        command(
            "record-bugbash-chat", "--chat-link", join_url, "--confirm-activated",
            "--review-hash", manual["review_hash"], "--approved-by", OWNER,
            "--reason", "Synthetic owner activated this exact invitation chat", json_output=False)
        verification = "owner_attested"
    assert activate_chat.stored_chat_id(context(load())) == chat_id
    assert load().get_step("bug_bash", "activate_chat").data["meeting"]["verification"] == verification
    trace.append("activate_chat")

    people = [
        {"id": "membership-owner", "userId": "11111111-1111-1111-1111-111111111111",
         "email": OWNER, "displayName": "Release Owner"},
        {"id": "membership-tester", "userId": "22222222-2222-2222-2222-222222222222",
         "email": TESTER, "displayName": "Manual Tester"},
    ]
    members = tmp_path / "members.json"
    members.write_text(json.dumps(member_envelope(
        {"id": chat_id, "chatType": "meeting", "members": people}, chat=chat_id)),
        encoding="utf-8")
    item = prepare("bugbash_updates", "--param", f"members_file={members}")
    assert item["payload"]["chatId"] == chat_id and item["payload"]["mentions"]
    assert item["completion"]["automation"] == {"on_demand": "bug-bash-update-poller"}
    deliver(item, {"id": "offline-first-progress"})
    trace.append("bugbash_updates")

    state = load()
    planned = automations.plan(
        config, release, state.ccd, owner_timezone=state.timezone,
        scheduler_timezone=state.timezone)
    assert not planned["problems"]
    spec = next(s for s in planned["automations"] if s["slug"] == "bug-bash-update-poller")
    assert not spec["problems"] and spec["schedule"] == "every 3 hours"
    registry = AutomationRegistry(runs, release=release)
    entry = registry.prepare(**spec["registration"], spec=spec["provider_spec"])
    worker = registry.reconcile_create(entry["key"], observed({
        "id": "offline-bugbash-poller", "spec": spec["provider_spec"],
    }), spec=spec["provider_spec"])["entry"]
    assert worker["status"] == "active"
    assert not automations.cleanup_plan(state, registry.list(release=release), config)["removals"]

    off_hours = datetime.now(timezone.utc).date().isoformat() + "T23:00:00-07:00"
    assert command("post-bugbash-update", "--now", off_hours)["decision"] == "off_hours"
    update = command("post-bugbash-update", "--force", "--members-file", str(members))
    assert update["decision"] == "post" and update["remaining"] > 0
    pending = command("notification", "prepare", "--source", "pending")
    item = next(n for n in pending["notifications"] if n["id"] == update["notifications"][0]["id"])
    deliver(item, {"id": "offline-periodic-progress"})
    assert not load().get_step("bug_bash", "bugbash_updates").data.get("poll_complete")

    item = prepare(
        "notify_native_auth", "--param", "engineer=engineer@example.test",
        "--param", "engineer_source=synthetic verified directory")
    deliver(item, {"id": "offline-native-notice"})
    trace.append("notify_native_auth")

    review = command("step-action", "--phase", "bug_bash", "--step", "ui_failures")
    assert review["kind"] == "needs_human"
    assert not load().is_done("bug_bash", "ui_failures")
    assert "monthly failure" in load().get_step("bug_bash", "ui_failures").note
    command("approve", "--comment", "Synthetic premature approval", expected=1, json_output=False)
    assert not load().gate_decisions
    command(
        "done", "--phase", "bug_bash", "--step", "ui_failures",
        "--note", "Synthetic owner reviewed each current failure", json_output=False)
    assert load().get_step("bug_bash", "ui_failures").data["confirmation_preconditions"]
    trace.append("ui_failures")
    signoff = command("step-action", "--phase", "bug_bash", "--step", "native_auth_signoff")
    assert signoff["kind"] == "needs_human"
    assert not load().is_done("bug_bash", "native_auth_signoff")
    command(
        "done", "--phase", "bug_bash", "--step", "native_auth_signoff",
        "--note", "Synthetic owner received Native Auth sign-off", json_output=False)
    trace.append("native_auth_signoff")
    assert not load().is_done("bug_bash", "bugbash_complete")
    assert not load().gate_decisions

    if completion == "all_complete":
        state = load()
        broker = state.resources["broker_test_plan"]
        auth = state.get_step("bug_bash", "clone_plans_auth").data
        release_suites = {
            (broker["plan_id"], broker["suites"]["broker"]),
            (T.AUTH_PLAN, auth["manual_suite_id"]),
            (T.AUTH_PLAN, auth["automated_suite_id"]),
            (T.AUTH_PLAN, auth["monthly"]["suite_id"]),
            *((broker["plan_id"], row["suite_id"]) for row in broker["scenario_bindings"]),
        }
        for key in release_suites:
            for point in ado.points[key]:
                point["outcome"] = "Passed"
        final = command("post-bugbash-update", "--force", "--members-file", str(members))
        assert final["decision"] == "complete"
        assert not load().get_step("bug_bash", "bugbash_updates").data.get("poll_complete")
        pending = command("notification", "prepare", "--source", "pending")
        item = next(n for n in pending["notifications"] if n["id"] == final["notifications"][0]["id"])
        deliver(item, {"id": "offline-final-progress"})
        assert load().get_step("bug_bash", "bugbash_updates").data["poll_complete"]
        assert not load().is_done("bug_bash", "bugbash_complete")
    else:
        assert not automations.cleanup_plan(
            load(), registry.list(release=release), config)["removals"]
        command(
            "approve", "--comment", "Synthetic owner accepted investigated remaining work",
            json_output=False)
        assert load().is_done("bug_bash", "bugbash_complete")
        assert not load().get_step("bug_bash", "bugbash_updates").data.get("poll_complete")
    cleanup = automations.cleanup_plan(load(), registry.list(release=release), config)
    assert not cleanup["problems"]
    assert [row["id"] for row in cleanup["removals"]] == ["offline-bugbash-poller"]
    deletion = registry.claim_delete("offline-bugbash-poller", "offline-phase3-worker")
    registry.delete_result(
        "offline-bugbash-poller", deletion["attempt_id"], "deleted", "Synthetic provider deletion")
    assert not registry.list(release=release)
    if completion == "all_complete":
        command("approve", "--comment", "Synthetic owner approved Phase 3", json_output=False)
    trace.append("bugbash_complete")

    state, orch = C.load_orch(runs, release, config)
    assert trace == [step.id for step in orch.workflow.phase("bug_bash").steps]
    assert all(state.is_done("bug_bash", step) for step in trace)
    assert orch.scheduling().phase("bug_bash").complete
    assert state.gate_decisions[-1]["step"] == "bug_bash.bugbash_complete"
    assert state.pipeline_runs == original_rc
    assert state.ccd == cloned.ccd
    assert all(ado.cases[cid] == case for cid, case in shared_cases.items())
    assert ado.points[7000, 7001] == protected_points[7000, 7001]
    assert all(key.startswith("bug_bash.") for key in state.steps)
    assert command("post-bugbash-update", "--force")["decision"] == "stopped"
    record_property("phase3_steps", ",".join(trace))
    record_property("providers", "in-memory ADO; synthetic M365/Scout receipts; no live calls")
