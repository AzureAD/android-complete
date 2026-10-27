"""Mandatory driver startup through the real local registry; no Scout/provider calls."""
from copy import deepcopy
import json

import pytest

from orchestrator import automations as A, cli, cli_common as C
from orchestrator.registry import AutomationRegistry, has_creation_receipt
from orchestrator.state import ReleaseState, StepState
from tests._automation import observed, seed_driver
from tests._harness import CONFIG, _active_phase
from tests._context import fresh_orchestrator


RID = "2026-12"


def release(root, *, ready=True):
    state = ReleaseState(release_id=RID, ccd="2026-12-09", timezone="UTC",
                         owner_email="owner@example.test")
    if ready:
        _active_phase(state, "preflight")
    else:
        fresh_orchestrator(CONFIG, state, mocks={})
    C.save_state(state, str(root), RID)
    return state


def call(root, capsys, *args):
    code = cli.main(["--runs-root", str(root), *args, "--release", RID])
    return code, capsys.readouterr().out


def driver(root, state, status):
    worker = next(item for item in A.plan(
        CONFIG, RID, state.ccd, owner_timezone=state.timezone)["automations"]
        if item["required_on_start"])
    spec, registry = worker["provider_spec"], AutomationRegistry(str(root), RID)
    entry = registry.prepare(**worker["registration"], spec=spec)
    if status == "prepared":
        return registry, worker, entry
    if status == "blocked":
        registry.reconcile_create(entry["key"], observed(
            {"id": "one", "spec": spec}, {"id": "two", "spec": spec}), spec=spec)
    else:
        claim = registry.reconcile_create(
            entry["key"], observed(), spec=spec, claim=True, executor="offline-provider")
        if status != "creating":
            registry.create_result(
                entry["key"], claim["attempt_id"], "uncertain" if status == "uncertain" else "created",
                "synthetic provider result", automation_id="driver-id", spec=spec)
        if status == "missing":
            registry.reconcile_create(entry["key"], observed(), spec=spec)
        elif status == "disabled":
            registry.reconcile_create(entry["key"], observed(
                {"id": "driver-id", "spec": {**spec, "enabled": False}}), spec=spec)
        elif status in ("deleting", "delete_uncertain"):
            deletion = registry.claim_delete("driver-id", "offline-cleanup")
            assert deletion["permission_to_delete"]
            if status == "delete_uncertain":
                registry.delete_result("driver-id", deletion["attempt_id"], "uncertain", "no provider confirmation")
    return registry, worker, registry.get(key=entry["key"])


@pytest.mark.parametrize("status", [
    "absent", "prepared", "creating", "uncertain", "blocked", "missing",
    "disabled", "deleting", "delete_uncertain", "active",
])
def test_only_acknowledged_active_driver_satisfies_startup(tmp_path, capsys, status):
    state = release(tmp_path)
    if status != "absent":
        registry, _, entry = driver(tmp_path, state, status)
        before_registry = (tmp_path / RID / "_automations.json").read_bytes()
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json",
                     "--no-pipeline-check", "--as-of", "2026-12-02")
    report = json.loads(out)
    startup = report["startup_automation"]
    assert startup["complete"] == (status == "active")
    assert not startup["permission_to_create"]
    assert path.read_bytes() == before
    if status != "absent":
        assert (tmp_path / RID / "_automations.json").read_bytes() == before_registry
    if status in ("absent", "prepared", "missing"):
        assert code == 2
        assert [item["key"] for item in report["work_dispatch"]["pending_work"]] == ["automation:push-reminders"]
        assert not report["work_dispatch"]["may_return_to_user"]
    elif status != "active":
        assert code == 0 and report["work_dispatch"]["next_action"] == "recover_startup_automation"
        assert not report["work_dispatch"]["pending_work"]
        assert startup["recoveries"] and not startup["active"]
    else:
        assert code == 2  # The driver is ready; phase work still must drain.
        assert startup["active"] == ["push-reminders"]
        assert any(item["kind"] == "engine" for item in report["work_dispatch"]["pending_work"])
    if status != "active":
        assert report["scout_pending"] == []
        _, runtime = C.load_orch(str(tmp_path), RID, CONFIG)
        selection = runtime.scheduling()
        assert selection.suspension == "automation" and not selection.runnable

    _, orch = C.load_orch(str(tmp_path), RID, CONFIG)
    before_steps = deepcopy(orch.state.steps)
    if status != "active":
        assert orch.run_until_gate()[0].kind == "automation"
        assert orch.state.steps == before_steps
        if status != "absent":
            assert registry.get(key=entry["key"]) == entry


@pytest.mark.parametrize("surface", ["init", "checklist", "status", "next", "tick", "obligations"])
def test_every_startup_surface_exposes_missing_driver(tmp_path, monkeypatch, capsys, surface):
    from tools import checks
    monkeypatch.setattr(checks, "read_pipeline_variable", lambda *args: (True, None, "offline"))
    if surface == "init":
        code, out = call(tmp_path, capsys, "init", "--json",
                         "--owner-email", "owner@example.test", "--timezone", "UTC")
    else:
        release(tmp_path, ready=surface != "checklist")
        if surface == "checklist":
            code, out = call(tmp_path, capsys, "checklist", "--attest-prompt", "--handoff", "--json")
        elif surface == "obligations":
            code, out = call(tmp_path, capsys, "automation", "obligations", "--json")
        elif surface == "status":
            code, out = call(tmp_path, capsys, "status", "--no-pipeline-check", "--json")
        else:
            code, out = call(tmp_path, capsys, surface, "--json", "--as-of", "2026-12-02")
    payload = json.loads(out)
    if surface == "obligations":
        assert code == 0
        assert [item["slug"] for item in payload["required"]] == ["push-reminders"]
        assert payload["required"][0]["required_on_start"]
    else:
        plan = payload["work_dispatch"] if surface != "checklist" else payload
        assert not plan["may_return_to_user"]
        assert [item["key"] for item in plan["pending_work"]] == ["automation:push-reminders"]
        assert "permission_to_create:true" in plan["pending_work"][0]["instruction"]
        assert code == (2 if surface == "checklist" else 0)
        if surface == "init":
            assert payload["initialized"] and not payload["startup_complete"]
            assert payload["next_action"] == "provision_startup_automation"
    saved = C.load_state(str(tmp_path), RID)
    assert not saved.steps and not saved.notification_deliveries


def test_signed_readiness_does_not_offer_started_success_without_driver(tmp_path, capsys):
    release(tmp_path)
    code, out = call(tmp_path, capsys, "checklist", "--attest-prompt", "--handoff", "--json")
    payload = json.loads(out)
    assert code == 2 and not payload["ready"]
    assert "Startup incomplete" in payload["display_markdown"]
    assert "answers" not in payload


def test_driver_adoption_and_create_acknowledgement_are_not_duplicate_creates(tmp_path, capsys):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "prepared")
    spec = worker["provider_spec"]
    result = registry.reconcile_create(
        entry["key"], observed({"id": "already-live", "spec": spec}),
        spec=spec, claim=True, executor="offline-worker")
    assert result["adopted"] and not result["permission_to_create"]
    assert has_creation_receipt(registry.get(key=entry["key"]))
    code, out = call(tmp_path, capsys, "status", "--json", "--no-pipeline-check")
    assert code == 0 and json.loads(out)["startup_automation"]["complete"]
    again = registry.reconcile_create(
        entry["key"], observed({"id": "already-live", "spec": spec}),
        spec=spec, claim=True, executor="another-worker")
    assert not again["permission_to_create"]
    assert registry.get(key=entry["key"])["attempts"] == []


def test_active_id_without_acknowledgement_is_not_driver_proof(tmp_path, capsys):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "prepared")
    file = tmp_path / RID / "_automations.json"
    document = json.loads(file.read_text(encoding="utf-8"))
    document["entries"][0].update(status="active", id="unverified-id", registered_at=entry["prepared_at"])
    file.write_text(json.dumps(document), encoding="utf-8")
    code, out = call(tmp_path, capsys, "status", "--json", "--no-pipeline-check")
    assert code == 0 and not json.loads(out)["startup_automation"]["complete"]
    assert not has_creation_receipt(registry.get(key=entry["key"]))


@pytest.mark.parametrize("terminal", ["cancelled", "complete", "halted"])
def test_global_and_terminal_release_states_never_create_new_driver(tmp_path, capsys, terminal):
    state = release(tmp_path)
    if terminal == "complete":
        # Seed explicit fixture approvals; never infer a real user's gate decision.
        orch = fresh_orchestrator(CONFIG, state, mocks={})
        for phase in orch.config["phases"]:
            for step in phase["steps"]:
                state.set_step(phase["id"], step["id"], StepState(status="done"))
                if step["kind"] == "approval_gate":
                    state.gate_decisions.append({
                        "step": f"{phase['id']}.{step['id']}", "decision": "approved",
                        "at": "2026-10-21T00:00:00Z", "by": "fixture", "comment": "fixture"})
    else:
        setattr(state, "cancellation" if terminal == "cancelled" else "halt",
                {"reason": "owner stopped", "at": "2026-10-21T00:00:00Z"})
    C.save_state(state, str(tmp_path), RID)
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check")
    report = json.loads(out)
    assert code == 0 and not report["work_dispatch"]["pending_work"]
    assert report["work_dispatch"]["may_return_to_user"]
    assert report["startup_automation"]["complete"] == (terminal != "halted")


def test_actual_startup_input_hold_never_claims_driver_ready(tmp_path, capsys):
    state = release(tmp_path)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check",
                     "--hold-step", "automation:push-reminders=Actual host creation consent required")
    report = json.loads(out)
    assert code == 0 and report["work_dispatch"]["deferred_work"]
    assert not report["startup_automation"]["complete"]
    assert not report["work_dispatch"]["permission_to_execute"]
    assert not report["work_dispatch"]["permission_to_send"]
    assert path.read_bytes() == before and not state.steps


def test_foreign_release_driver_cannot_satisfy_local_startup(tmp_path, capsys):
    release(tmp_path)
    foreign = ReleaseState(release_id="2026-11", ccd="2026-11-11", timezone="UTC")
    seed_driver(tmp_path, foreign)
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check")
    assert code == 2 and not json.loads(out)["startup_automation"]["complete"]


def test_active_receipt_releases_runtime_phase_execution_without_reinitializing(tmp_path, capsys):
    from orchestrator import mocks
    from unittest.mock import patch
    state = release(tmp_path)
    code, out = call(tmp_path, capsys, "next", "--json", "--as-of", "2026-12-02")
    assert code == 0 and not C.load_state(str(tmp_path), RID).steps
    seed_driver(tmp_path, state)
    # Keep provider effects offline while exercising the actual CLI drain.
    with patch.object(mocks, "load_mocks", return_value={
            f"preflight.{sid}": {"outcome": "done", "note": "synthetic handler"}
            for sid in ("breaking", "cg", "oneauth_access", "cron")}):
        code, out = call(tmp_path, capsys, "next", "--pending-only", "--json", "--as-of", "2026-12-02")
    assert code == 0 and json.loads(out)["startup_automation"]["complete"]
    saved = C.load_state(str(tmp_path), RID)
    assert all(saved.is_done("preflight", sid) for sid in ("breaking", "cg", "oneauth_access", "cron"))
    assert saved.created_at == state.created_at and not saved.notification_deliveries


def test_declared_disabled_driver_is_a_configuration_problem_not_startup_success(tmp_path):
    import yaml
    from pathlib import Path
    from orchestrator.engine import Orchestrator
    from orchestrator.revision import bind_initial
    config = Path(CONFIG)
    custom = tmp_path / "config"
    custom.mkdir()
    (custom / "phases.yaml").write_bytes(config.read_bytes())
    document = yaml.safe_load((config.parent / "automations.yaml").read_text(encoding="utf-8"))
    document["provider_defaults"]["enabled"] = False
    (custom / "automations.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    state = ReleaseState(release_id=RID, timezone="UTC")
    orch = Orchestrator(str(custom / "phases.yaml"), state, mocks={}, automation_entries=lambda: [])
    bind_initial(orch)
    result = orch.status_report()
    assert not result["startup_automation"]["complete"]
    assert any("disabled" in message for message in result["startup_automation"]["problems"])
    assert any("mandatory" in message for message in A.validate(str(custom / "phases.yaml")))
    assert not result["work_dispatch"]["permission_to_execute"]


def test_corrupt_registry_fails_closed_without_creation_or_state_replacement(tmp_path, capsys):
    state = release(tmp_path)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    (tmp_path / RID / "_automations.json").write_text('{"invalid":true}', encoding="utf-8")
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check")
    result = json.loads(out)
    assert code == 1 and result["permission_to_execute"] is False
    assert "registry" in result["error"]
    assert path.read_bytes() == before and not state.steps


def test_duplicate_registry_entries_never_choose_a_driver_or_grant_create(tmp_path, capsys):
    state = release(tmp_path)
    _, _, entry = driver(tmp_path, state, "prepared")
    file = tmp_path / RID / "_automations.json"
    document = json.loads(file.read_text(encoding="utf-8"))
    document["entries"].append(deepcopy(entry))
    file.write_text(json.dumps(document), encoding="utf-8")
    code, out = call(tmp_path, capsys, "status", "--handoff", "--json", "--no-pipeline-check")
    result = json.loads(out)
    assert code == 0 and not result["startup_automation"]["complete"]
    assert any("duplicate" in problem for problem in result["startup_automation"]["problems"])
    code, out = call(tmp_path, capsys, "automation", "obligations", "--json")
    result = json.loads(out)
    assert code == 1 and not result["required"]
    assert any("duplicate" in problem for problem in result["problems"])


def test_explicit_halt_cannot_claim_driver_creation(tmp_path, capsys):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "prepared")
    state.halt = {"reason": "owner paused release", "at": "2026-10-21T00:00:00Z"}
    C.save_state(state, str(tmp_path), RID)
    code, out = call(
        tmp_path, capsys, "automation", "reconcile-create", "--slug", "push-reminders",
        "--spec-json", json.dumps(worker["provider_spec"]), "--observed-json", json.dumps(observed()),
        "--claim", "--executor", "offline-provider", "--json")
    result = json.loads(out)
    assert code == 1 and not result["permission_to_create"]
    assert "explicit hold" in result["error"]
    assert registry.get(key=entry["key"]) == entry
