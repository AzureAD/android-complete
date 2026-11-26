"""Unified state-driven hybrid model: optional non-blocking accelerators + shared locked idempotency."""
from copy import deepcopy
import json
import threading
import time
from unittest.mock import patch

import pytest

from orchestrator import automations as A, cli, cli_common as C
from orchestrator.agency_mcp import AgencyAmbiguousDeliveryError
from orchestrator.commands import deliver_cmd
from orchestrator.registry import AutomationRegistry, has_creation_receipt
from orchestrator.state import ReleaseState, StepState
from tests._automation import observed, seed_driver
from tests._harness import CONFIG, _active_phase
from tests._context import fresh_orchestrator


RID = "2026-12"


def release(root, *, ready=True):
    state = ReleaseState(
        release_id=RID,
        ccd="2026-12-09",
        timezone="UTC",
        owner_email="owner@example.test",
    )
    if ready:
        _active_phase(state, "preflight")
    else:
        fresh_orchestrator(CONFIG, state, step_defaults={})
    C.save_state(state, str(root), RID)
    return state


def call(root, capsys, *args):
    code = cli.main(["--runs-root", str(root), *args, "--release", RID])
    return code, capsys.readouterr().out


def driver(root, state, status):
    worker = next(
        item
        for item in A.plan(CONFIG, RID, state.ccd, owner_timezone=state.timezone)["automations"]
        if item["slug"] == "release-drainer"
    )
    spec, registry = worker["provider_spec"], AutomationRegistry(str(root), RID)
    entry = registry.prepare(**worker["registration"], spec=spec)
    if status == "prepared":
        return registry, worker, entry
    if status == "blocked":
        registry.reconcile_create(
            entry["key"],
            observed({"id": "one", "spec": spec}, {"id": "two", "spec": spec}),
            spec=spec,
        )
    else:
        claim = registry.reconcile_create(
            entry["key"], observed(), spec=spec, claim=True, executor="offline-provider"
        )
        if status != "creating":
            registry.create_result(
                entry["key"],
                claim["attempt_id"],
                "uncertain" if status == "uncertain" else "created",
                "synthetic provider result",
                automation_id="driver-id",
                spec=spec,
            )
        if status == "missing":
            registry.reconcile_create(entry["key"], observed(), spec=spec)
        elif status == "disabled":
            registry.reconcile_create(
                entry["key"],
                observed({"id": "driver-id", "spec": {**spec, "enabled": False}}),
                spec=spec,
            )
        elif status in ("deleting", "delete_uncertain"):
            deletion = registry.claim_delete("driver-id", "offline-cleanup")
            assert deletion["permission_to_delete"]
            if status == "delete_uncertain":
                registry.delete_result(
                    "driver-id", deletion["attempt_id"], "uncertain", "no provider confirmation"
                )
    return registry, worker, registry.get(key=entry["key"])


@pytest.mark.parametrize(
    "status",
    [
        "absent",
        "prepared",
        "creating",
        "uncertain",
        "blocked",
        "missing",
        "disabled",
        "deleting",
        "delete_uncertain",
        "active",
    ],
)
def test_automations_are_optional_and_never_block_workflow_progression(tmp_path, capsys, status):
    """Disabling, deleting, or omitting `morning-step-drain` never blocks status, readiness, or phase work."""
    state = release(tmp_path)
    if status != "absent":
        registry, _, entry = driver(tmp_path, state, status)
        before_registry = (tmp_path / RID / "_automations.json").read_bytes()
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, out = call(
        tmp_path,
        capsys,
        "status",
        "--handoff",
        "--json",
        "--no-pipeline-check",
        "--as-of",
        "2026-12-02",
    )
    report = json.loads(out)
    assert "startup_automation" not in report
    assert "execution_mode" not in report
    # Only real phase work is queued — never `automation:*` startup blockers!
    assert all(item["kind"] != "automation" for item in report["work_dispatch"]["pending_work"])
    assert any(item["kind"] == "engine" for item in report["work_dispatch"]["pending_work"])
    assert code == 2  # Real Phase 0 work remains executable regardless of automation registry state.
    assert path.read_bytes() == before
    if status != "absent":
        assert (tmp_path / RID / "_automations.json").read_bytes() == before_registry
        assert registry.get(key=entry["key"]) == entry


@pytest.mark.parametrize("surface", ["init", "checklist", "status", "next", "obligations"])
def test_every_surface_operates_cleanly_with_zero_automations(tmp_path, monkeypatch, capsys, surface):
    from orchestrator import environment as mocks
    from tools import checks

    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *args: (True, None, "offline"))
    if surface == "init":
        code, out = call(
            tmp_path,
            capsys,
            "init",
            "--json",
            "--owner-email",
            "owner@example.test",
            "--timezone",
            "UTC",
        )
    else:
        release(tmp_path, ready=surface != "checklist")
        if surface == "checklist":
            code, out = call(tmp_path, capsys, "checklist", "--attest-prompt", "--handoff", "--json")
        elif surface == "obligations":
            code, out = call(tmp_path, capsys, "automation", "obligations", "--json")
        elif surface == "status":
            code, out = call(tmp_path, capsys, "status", "--no-pipeline-check", "--json")
        else:
            with patch.object(
                mocks,
                "load_step_defaults",
                return_value={
                    f"preflight.{sid}": {"outcome": "done", "note": "synthetic handler"}
                    for sid in ("breaking", "cg", "oneauth_access", "cron")
                },
            ):
                code, out = call(tmp_path, capsys, surface, "--json", "--as-of", "2026-12-02")
    payload = json.loads(out)
    if surface == "obligations":
        assert code == 0
        assert payload["required"] == []
        assert "release-drainer" in [item["slug"] for item in payload["optional"]]
    elif surface == "init":
        assert code == 0 and payload["initialized"]
        assert payload["next_action"] == "readiness"
        assert payload["drainer_setup"]["slug"] == "release-drainer"
    elif surface == "checklist":
        assert code == 2
        assert all(item.get("kind") != "automation" for item in payload.get("pending_work", []))
    else:
        assert code == 0
        assert all(
            item.get("kind") != "automation"
            for item in payload["work_dispatch"].get("pending_work", [])
        )


def test_status_is_strictly_local_and_never_touches_automation_registry(tmp_path, capsys, monkeypatch):
    release(tmp_path)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    monkeypatch.setattr(C, "refresh_conflict", lambda *a: pytest.fail("Status probed a provider"))
    monkeypatch.setattr(AutomationRegistry, "list", lambda *a, **k: pytest.fail("Status read automation registry"))
    code, out = call(tmp_path, capsys, "status", "--json", "--as-of", "2026-12-02")
    report = json.loads(out)
    assert code == 0 and report["work_dispatch"]["pending_work"]
    assert path.read_bytes() == before
    assert not (path.parent / "events.jsonl").exists()


def test_scheduled_and_interactive_share_same_state_machine(tmp_path, capsys):
    """`--scheduled` runs against the unified state machine without pausing on an execution_mode split."""
    from orchestrator import environment as mocks

    state = release(tmp_path)
    with patch.object(
        mocks,
        "load_step_defaults",
        return_value={
            f"preflight.{sid}": {"outcome": "done", "note": "synthetic handler"}
            for sid in ("breaking", "cg", "oneauth_access", "cron")
        },
    ):
        code = cli.main(
            [
                "--scheduled",
                "--runs-root",
                str(tmp_path),
                "next",
                "--release",
                RID,
                "--pending-only",
                "--json",
                "--as-of",
                "2026-12-02",
            ]
        )
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    saved = C.load_state(str(tmp_path), RID)
    assert all(saved.is_done("preflight", sid) for sid in ("breaking", "cg", "oneauth_access", "cron"))
    assert saved.created_at == state.created_at
    assert out["done"] >= 4


def test_driver_adoption_and_create_acknowledgement_are_not_duplicate_creates(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "prepared")
    spec = worker["provider_spec"]
    result = registry.reconcile_create(
        entry["key"],
        observed({"id": "already-live", "spec": spec}),
        spec=spec,
        claim=True,
        executor="offline-worker",
    )
    assert result["adopted"] and not result["permission_to_create"]
    assert has_creation_receipt(registry.get(key=entry["key"]))
    again = registry.reconcile_create(
        entry["key"],
        observed({"id": "already-live", "spec": spec}),
        spec=spec,
        claim=True,
        executor="another-worker",
    )
    assert not again["permission_to_create"]
    assert registry.get(key=entry["key"])["attempts"] == []


@pytest.mark.parametrize("terminal", ["cancelled", "complete", "halted"])
def test_global_and_terminal_release_states_never_queue_phase_work(tmp_path, capsys, terminal):
    state = release(tmp_path)
    if terminal == "complete":
        orch = fresh_orchestrator(CONFIG, state, step_defaults={})
        for phase in orch.config["phases"]:
            for step in phase["steps"]:
                state.set_step(phase["id"], step["id"], StepState(status="done"))
                if step["kind"] == "approval_gate":
                    state.gate_decisions.append(
                        {
                            "step": f"{phase['id']}.{step['id']}",
                            "decision": "approved",
                            "at": "2026-10-21T00:00:00Z",
                            "by": "fixture",
                            "comment": "fixture",
                        }
                    )
    else:
        setattr(
            state,
            "cancellation" if terminal == "cancelled" else "halt",
            {"reason": "owner stopped", "at": "2026-10-21T00:00:00Z"},
        )
    C.save_state(state, str(tmp_path), RID)
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check")
    report = json.loads(out)
    assert code == 0 and not report["work_dispatch"]["pending_work"]
    assert report["work_dispatch"]["may_return_to_user"]


def test_duplicate_registry_entries_report_problem_only_on_obligations_not_status(tmp_path, capsys):
    state = release(tmp_path)
    _, _, entry = driver(tmp_path, state, "prepared")
    file = tmp_path / RID / "_automations.json"
    document = json.loads(file.read_text(encoding="utf-8"))
    document["entries"].append(deepcopy(entry))
    file.write_text(json.dumps(document), encoding="utf-8")
    # Workflow `status` stays green because automations are non-blocking accelerators.
    code, _ = call(tmp_path, capsys, "status", "--json", "--no-pipeline-check")
    assert code == 0
    # Inspecting `automation obligations` surfaces the duplicate registry warning cleanly.
    code, out = call(tmp_path, capsys, "automation", "obligations", "--json")
    result = json.loads(out)
    assert code == 1 and result["required"] == []
    assert any("duplicate" in problem for problem in result["problems"])


def test_explicit_halt_cannot_claim_driver_creation(tmp_path, capsys):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "prepared")
    state.halt = {"reason": "owner paused release", "at": "2026-10-21T00:00:00Z"}
    C.save_state(state, str(tmp_path), RID)
    code, out = call(
        tmp_path,
        capsys,
        "automation",
        "reconcile-create",
        "--slug",
        "release-drainer",
        "--spec-json",
        json.dumps(worker["provider_spec"]),
        "--observed-json",
        json.dumps(observed()),
        "--claim",
        "--executor",
        "offline-provider",
        "--json",
    )
    result = json.loads(out)
    assert code == 1 and not result["permission_to_create"]
    assert "explicit hold" in result["error"]
    assert registry.get(key=entry["key"]) == entry


@pytest.mark.real_revision
def test_concurrent_morning_drain_and_interactive_session_never_collide_or_double_send(
    tmp_path, monkeypatch
):
    """Critical hybrid constraint: simultaneous 7:00 AM `--scheduled` drain and interactive
    session share `.state.lock` + durable pre-IO claims and NEVER double-send or clobber state."""
    from datetime import datetime, timezone
    from orchestrator import environment as mocks, schedule

    # Set live clock inside the Phase 0 window (`2026-12-03T07:00:00Z`, after `CCD-7 = 2026-12-02`)
    # so `next --deliver` executes with the trusted clock (no `--as-of`).
    monkeypatch.setattr(
        schedule,
        "now_local",
        lambda _tz=None: datetime(2026, 12, 3, 7, 0, tzinfo=timezone.utc),
    )
    release(tmp_path, ready=True)

    sent_calls: list[tuple[str, str]] = []
    calls_lock = threading.Lock()

    def slow_mcp_caller(server: str, tool_name: str, arguments: dict) -> dict:
        time.sleep(0.05)  # widen overlap window across contenders
        with calls_lock:
            sent_calls.append((server, tool_name))
        return {"id": f"msg-{len(sent_calls)}", "status": "ok"}

    monkeypatch.setattr(deliver_cmd, "call_mcp_tool", slow_mcp_caller)
    monkeypatch.setattr(
        mocks,
        "load_step_defaults",
        lambda *_a, **_kw: {
            "preflight.notice": {"send_to": ["owner@example.test"]},
            "preflight.flight_reminder": {"send_to": "me"},
            **{
                f"preflight.{sid}": {"outcome": "done", "note": "synthetic handler"}
                for sid in ("breaking", "cg", "oneauth_access", "cron")
            },
        },
    )

    barrier = threading.Barrier(2)
    exit_codes: list[int] = []

    def background_morning_drain():
        barrier.wait()
        rc = cli.main(
            [
                "--scheduled",
                "--runs-root",
                str(tmp_path),
                "next",
                "--release",
                RID,
                "--deliver",
                "--json",
            ]
        )
        exit_codes.append(rc)

    def interactive_session():
        barrier.wait()
        rc = cli.main(
            [
                "--runs-root",
                str(tmp_path),
                "next",
                "--release",
                RID,
                "--deliver",
                "--json",
            ]
        )
        exit_codes.append(rc)

    t1 = threading.Thread(target=background_morning_drain)
    t2 = threading.Thread(target=interactive_session)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert exit_codes == [0, 0]
    # Exactly 1 email (`preflight.notice`) + 1 self-Teams post (`preflight.flight_reminder`) — ZERO duplicates!
    assert sorted(sent_calls) == [
        ("mail", "SendEmailWithAttachments"),
        ("teams", "SendMessageToSelf"),
    ]
    final_state = C.load_state(str(tmp_path), RID)
    for sid in ("notice", "flight_reminder", "breaking", "cg", "oneauth_access", "cron"):
        assert final_state.is_done("preflight", sid), sid


@pytest.mark.real_revision
def test_ambiguous_post_dispatch_timeout_fences_against_duplicate_replay(tmp_path, monkeypatch):
    """If `tools/call` times out after dispatch during the 7:00 AM drain, the notification is
    recorded `uncertain` so a subsequent interactive session never double-sends."""
    from datetime import datetime, timezone
    from orchestrator import environment as mocks, schedule

    monkeypatch.setattr(
        schedule,
        "now_local",
        lambda _tz=None: datetime(2026, 12, 3, 7, 0, tzinfo=timezone.utc),
    )
    release(tmp_path, ready=True)
    monkeypatch.setattr(
        mocks,
        "load_step_defaults",
        lambda *_a, **_kw: {"preflight.notice": {"send_to": ["owner@example.test"]}},
    )

    attempts = {"count": 0}

    def ambiguous_caller(server: str, tool_name: str, arguments: dict) -> dict:
        attempts["count"] += 1
        raise AgencyAmbiguousDeliveryError("Timed out waiting for SendEmailWithAttachments response")

    with C.state_lock(str(tmp_path), RID):
        first = deliver_cmd.deliver_one_step(
            str(tmp_path),
            RID,
            CONFIG,
            "preflight",
            "notice",
            executor="morning-step-drain",
            mcp_caller=ambiguous_caller,
        )
    assert first["status"] == "uncertain"
    assert attempts["count"] == 1

    saved = C.load_state(str(tmp_path), RID)
    ledger_record = saved.notification_deliveries[first["notification_id"]]
    assert ledger_record["status"] == "uncertain"

    # Subsequent interactive `deliver_one_step` is fenced by step ownership (`status == "blocked"`)
    # and never invokes `mcp_caller` a second time!
    with C.state_lock(str(tmp_path), RID):
        second = deliver_cmd.deliver_one_step(
            str(tmp_path),
            RID,
            CONFIG,
            "preflight",
            "notice",
            executor="interactive-session",
            mcp_caller=lambda *a, **k: pytest.fail("Must never replay uncertain delivery"),
        )
    assert second["status"] == "blocked"
    assert attempts["count"] == 1


def test_entry_gate_surfaces_one_time_drainer_setup_and_never_repeats(tmp_path, capsys):
    """Starting at the Entry Gate (`checklist --json` and `init --json`), `drainer_setup` surfaces
    once; after `automation record-setup` (created or skipped), it never prompts or recreates again."""
    release(tmp_path, ready=False)
    code, out = call(tmp_path, capsys, "checklist", "--attest-prompt", "--json")
    payload = json.loads(out)
    assert code == 0
    hint = payload["drainer_setup"]
    assert hint["slug"] == "release-drainer"
    assert hint["schedule"].startswith("cron:")
    assert hint["provider_spec"]["oneShot"] is False

    # Record setup once via `automation record-setup --id ...`
    code, rec_out = call(
        tmp_path,
        capsys,
        "automation",
        "record-setup",
        "--slug",
        "release-drainer",
        "--id",
        "scout-drainer-2h",
        "--json",
    )
    rec = json.loads(rec_out)
    assert code == 0 and rec["recorded"] is True and rec["entry"]["status"] == "active"

    # Once recorded, `drainer_setup` is no longer surfaced (even if user disables/deletes in UI).
    code, out2 = call(tmp_path, capsys, "checklist", "--attest-prompt", "--json")
    assert code == 0 and "drainer_setup" not in json.loads(out2)


