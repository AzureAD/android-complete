"""Idempotent desired-worker reconciliation; every provider result is an offline fixture."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from orchestrator import automations as A, cli, cli_common as C
from orchestrator.registry import AutomationRegistry, stable_key, provider_check_fresh
from orchestrator.state import StepState
from tests._automation import observed
from tests._harness import CONFIG, _active_phase
from tests.test_startup_driver import RID, release, driver


def ensure_cli(root, capsys, worker, observations, *, claim=True):
    args = [
        "--runs-root", str(root), "automation", "reconcile-create", "--release", RID,
        "--slug", worker["slug"], "--ensure", "--json", "--executor", "offline-controller",
        "--spec-json", json.dumps(worker["provider_spec"]), "--observed-json", json.dumps(observations),
    ]
    if worker["on_demand"]:
        args += ["--on-demand", worker["slug"]]
    if claim:
        args.append("--claim")
    code = cli.main(args)
    return code, json.loads(capsys.readouterr().out)


def ensure_reg(registry, worker, observations, *, claim=True, executor="offline-controller", spec=None):
    return registry.reconcile_create(
        stable_key(RID, worker["slug"]), observations, spec=spec or worker["provider_spec"],
        registration=worker["registration"], ensure=True, claim=claim, executor=executor)


def current_worker(state, slug="push-reminders"):
    return next(item for item in A.plan(
        CONFIG, RID, state.ccd, owner_timezone=state.timezone)["automations"] if item["slug"] == slug)


def test_first_ensure_prepares_and_claims_once_without_a_separate_prepare(tmp_path, capsys):
    state = release(tmp_path)
    worker = current_worker(state)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 0 and result["permission_to_create"] and result["spec"] == worker["provider_spec"]
    registry = AutomationRegistry(str(tmp_path), RID)
    entry = registry.get(key=stable_key(RID, worker["slug"]))
    assert entry["status"] == "creating" and len(entry["attempts"]) == 1
    code, repeated = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 0 and not repeated["permission_to_create"] and repeated["status"] == "creating"
    assert registry.get(key=entry["key"]) == entry and path.read_bytes() == before


@pytest.mark.parametrize("initial", ["active", "missing"])
def test_deleted_driver_is_recreated_without_owner_reset_and_retains_history(tmp_path, capsys, initial):
    state = release(tmp_path)
    registry, worker, original = driver(tmp_path, state, initial)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 0 and result["permission_to_create"] and result["replacement"]
    entry = registry.get(key=original["key"])
    assert entry["attempts"][:len(original["attempts"])] == original["attempts"]
    absence = entry["attempts"][-2]
    assert absence["status"] == "provider_absent" and absence["provider_id"] == "driver-id"
    assert absence["intent_hash"] == original["intent_hash"] and absence["evidence"].startswith("sha256:")
    assert entry["id"] is None and entry["status"] == "creating"
    registry.create_result(entry["key"], result["attempt_id"], "created", "synthetic new provider id",
                           automation_id="replacement-id", spec=worker["provider_spec"])
    code, healthy = ensure_cli(tmp_path, capsys, worker, observed(
        {"id": "replacement-id", "spec": worker["provider_spec"]}))
    assert code == 0 and healthy["status"] == "active" and not healthy["permission_to_create"]
    _, runtime = C.load_orch(str(tmp_path), RID, CONFIG)
    assert runtime.status_report()["startup_automation"]["complete"]
    assert path.read_bytes() == before
    raw = (tmp_path / RID / "_automations.json").read_text(encoding="utf-8")
    assert worker["provider_spec"]["prompt"] not in raw


def test_live_driver_is_adopted_repeatedly_never_created_again(tmp_path, capsys):
    state = release(tmp_path)
    worker = current_worker(state)
    for _ in range(3):
        code, result = ensure_cli(tmp_path, capsys, worker, observed(
            {"id": "already-live", "spec": worker["provider_spec"]}))
        assert code == 0 and result["adopted"] and not result["permission_to_create"]
    entry = AutomationRegistry(str(tmp_path), RID).get(key=stable_key(RID, worker["slug"]))
    assert entry["id"] == "already-live" and entry["attempts"] == []


@pytest.mark.parametrize("status", ["creating", "uncertain", "deleting", "delete_uncertain"])
def test_unknown_owned_operations_are_not_recreated_even_when_not_listed(tmp_path, capsys, status):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, status)
    before = (tmp_path / RID / "_automations.json").read_bytes()
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 0 and not result["permission_to_create"] and result["status"] == status
    assert registry.get(key=entry["key"]) == entry
    assert (tmp_path / RID / "_automations.json").read_bytes() == before


@pytest.mark.parametrize("change", ["partial", "stale", "future", "bad-row"])
def test_absence_requires_fresh_complete_valid_provider_observations(tmp_path, capsys, change):
    state = release(tmp_path)
    _, worker, _ = driver(tmp_path, state, "active")
    proof = observed()
    if change == "partial":
        proof["complete"] = False
    elif change == "stale":
        proof["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    elif change == "future":
        proof["observed_at"] = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    else:
        proof["automations"] = [{"id": "driver-id"}]
    path = tmp_path / RID / "_automations.json"
    before = path.read_bytes()
    code, result = ensure_cli(tmp_path, capsys, worker, proof)
    assert code == 1 and not result["permission_to_create"]
    assert path.read_bytes() == before


def test_concurrent_absence_ensures_grant_only_one_replacement(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")

    def run(index):
        try:
            return ensure_reg(registry, worker, observed(), executor=f"runner-{index}")
        except ValueError as exc:
            assert "stale" in str(exc)
            return {"permission_to_create": False}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert sum(result["permission_to_create"] for result in results) == 1
    current = registry.get(key=entry["key"])
    assert current["status"] == "creating" and len(current["attempts"]) == 3
    assert [attempt["status"] for attempt in current["attempts"]] == ["created", "provider_absent", "claimed"]


def test_old_absence_snapshot_cannot_replace_a_newly_acknowledged_generation(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    result = ensure_reg(registry, worker, observed())
    old_snapshot = observed()
    registry.create_result(entry["key"], result["attempt_id"], "created", "synthetic acknowledgement",
                           automation_id="new-id", spec=worker["provider_spec"])
    before = registry.get(key=entry["key"])
    with pytest.raises(ValueError, match="stale"):
        ensure_reg(registry, worker, old_snapshot)
    assert registry.get(key=entry["key"]) == before


def test_source_drift_can_replace_absent_worker_but_does_not_rewrite_live_worker(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    original_spec = worker["provider_spec"]
    updated = {**original_spec, "prompt": original_spec["prompt"] + "\nUpdated implementation guidance."}
    live = ensure_reg(registry, worker, observed({"id": "driver-id", "spec": original_spec}), spec=updated)
    assert live["desired_spec_drift"] and not live["permission_to_create"]
    assert registry.get(key=entry["key"])["intent_hash"] == entry["intent_hash"]
    changed = ensure_reg(registry, worker, observed(), spec=updated)
    assert changed["permission_to_create"] and changed["spec"] == updated
    replaced = registry.get(key=entry["key"])
    assert replaced["intent_hash"] != entry["intent_hash"]
    assert replaced["attempts"][-2]["intent_hash"] == entry["intent_hash"]


@pytest.mark.parametrize("change", ["disabled", "duplicate", "renamed"])
def test_live_conflicts_require_review_not_an_additional_worker(tmp_path, capsys, change):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    actual = worker["provider_spec"]
    rows = [{"id": "driver-id", "spec": actual}]
    if change == "disabled":
        rows[0]["spec"] = {**actual, "enabled": False}
    elif change == "renamed":
        rows[0]["spec"] = {**actual, "name": "Deliberately renamed"}
    else:
        rows.append({"id": "extra", "spec": actual})
    code, result = ensure_cli(tmp_path, capsys, worker, observed(*rows))
    assert code == 1 and not result["permission_to_create"] and result["status"] == "blocked"
    assert registry.get(key=entry["key"])["attempts"] == entry["attempts"]


def test_retired_provider_identity_reappearing_cannot_be_ignored(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    created = ensure_reg(registry, worker, observed())
    registry.create_result(entry["key"], created["attempt_id"], "created", "synthetic acknowledgement",
                           automation_id="new-id", spec=worker["provider_spec"])
    result = ensure_reg(registry, worker, observed(
        {"id": "new-id", "spec": worker["provider_spec"]},
        {"id": "driver-id", "spec": {**worker["provider_spec"], "name": "Late old generation"}}))
    assert result["status"] == "blocked" and not result["permission_to_create"]


def test_historical_receipt_requires_live_reconciliation_after_freshness_window(tmp_path, capsys):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    path = tmp_path / RID / "_automations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    document["entries"][0]["registered_at"] = old
    path.write_text(json.dumps(document), encoding="utf-8")
    assert not provider_check_fresh(registry.get(key=entry["key"]))
    _, runtime = C.load_orch(str(tmp_path), RID, CONFIG)
    assert runtime.status_report()["work_dispatch"]["pending_work"][0]["kind"] == "automation"
    code, result = ensure_cli(tmp_path, capsys, worker, observed(
        {"id": "driver-id", "spec": worker["provider_spec"]}))
    assert code == 0 and not result["permission_to_create"]
    assert provider_check_fresh(registry.get(key=entry["key"]))


@pytest.mark.parametrize("state_kind", ["halted", "cancelled", "objective_complete"])
def test_ensure_obeys_release_and_worker_lifecycle(tmp_path, capsys, state_kind):
    state = release(tmp_path)
    worker = current_worker(state)
    if state_kind == "objective_complete":
        _active_phase(state, "rollout_start")
        worker = current_worker(state, "daily-status-email")
    else:
        setattr(state, "halt" if state_kind == "halted" else "cancellation",
                {"reason": "owner stopped", "at": datetime.now(timezone.utc).isoformat()})
    C.save_state(state, str(tmp_path), RID)
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 1 and not result["permission_to_create"]
    assert not (tmp_path / RID / "_automations.json").exists()


def test_on_demand_worker_is_ensured_only_after_its_trigger_and_before_settlement(tmp_path, capsys):
    state = release(tmp_path)
    worker = current_worker(state, "ccd-localization-poller")
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 1 and not result["permission_to_create"]
    state.set_step("ccd", "localization", StepState(status="in_flight",
                   data={"started_at": datetime.now(timezone.utc).isoformat(), "build_id": "42"}))
    C.save_state(state, str(tmp_path), RID)
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 0 and result["permission_to_create"]
    registry = AutomationRegistry(str(tmp_path), RID)
    registry.create_result(stable_key(RID, worker["slug"]), result["attempt_id"], "created",
                           "synthetic poller", automation_id="poller-id", spec=worker["provider_spec"])
    state.set_step("ccd", "localization", StepState(status="done"))
    C.save_state(state, str(tmp_path), RID)
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 1 and not result["permission_to_create"]
    assert registry.get(auto_id="poller-id")["status"] == "active"  # Cleanup owns retirement.


def test_two_deleted_generations_recover_independently_with_append_only_history(tmp_path):
    state = release(tmp_path)
    registry, worker, entry = driver(tmp_path, state, "active")
    for identity in ("replacement-one", "replacement-two"):
        result = ensure_reg(registry, worker, observed())
        assert result["permission_to_create"]
        registry.create_result(entry["key"], result["attempt_id"], "created", "synthetic created",
                               automation_id=identity, spec=worker["provider_spec"])
        ensure_reg(registry, worker, observed({"id": identity, "spec": worker["provider_spec"]}))
    current = registry.get(key=entry["key"])
    assert [attempt["provider_id"] for attempt in current["attempts"]
            if attempt["status"] == "provider_absent"] == ["driver-id", "replacement-one"]
    assert current["attempts"][:len(entry["attempts"])] == entry["attempts"]


@pytest.mark.parametrize("shared", [False, True])
def test_ensure_never_takes_over_manual_or_shared_registry_identity(tmp_path, capsys, shared):
    state = release(tmp_path)
    worker = current_worker(state)
    registry = AutomationRegistry(str(tmp_path), RID)
    registration = {**worker["registration"], "cleanup_when": "manual"}
    if shared:
        registration["release"] = None
    entry = registry.prepare(**registration, shared=shared, spec=worker["provider_spec"])
    registry.reconcile_create(entry["key"], observed(
        {"id": "manual-id", "spec": worker["provider_spec"]}), spec=worker["provider_spec"])
    before = registry.list()
    code, result = ensure_cli(tmp_path, capsys, worker, observed())
    assert code == 1 and not result["permission_to_create"]
    assert registry.list() == before


def test_obligations_revalidate_registered_driver_not_only_missing_entries(tmp_path, capsys):
    state = release(tmp_path)
    driver(tmp_path, state, "active")
    assert cli.main(["--runs-root", str(tmp_path), "automation", "obligations",
                     "--release", RID, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["required"] == []
    assert [worker["slug"] for worker in result["ensure"]] == ["push-reminders"]


@pytest.mark.parametrize("change", ["none", "unapplied", "reopened", "new-generation", "different-owner"])
def test_notification_trigger_requires_current_applied_producer(change):
    from orchestrator.state import ReleaseState
    state = ReleaseState(release_id=RID, owner_email="owner@example.test", ccd="2026-12-09")
    worker = {"slug": "poller", "steps": ["phase.producer"]}
    state.set_step("phase", "producer", StepState(status="done"))
    state.notification_deliveries["receipt"] = {
        "status": "sent", "completion": {"status": "applied"},
        "descriptor": {
            "scope": {"phase": "phase", "step": "producer", "generation": "initial",
                      "release_matches": {"owner_email": state.owner_email, "ccd": state.ccd}},
            "completion": {"automation": {"on_demand": "poller"}},
        },
    }
    if change == "unapplied":
        state.notification_deliveries["receipt"]["completion"] = {}
    elif change == "reopened":
        state.set_step("phase", "producer", StepState(status="pending"))
    elif change == "new-generation":
        state.set_step("phase", "producer", StepState(status="done", invalidated_at="new"))
    elif change == "different-owner":
        state.owner_email = "another-owner@example.test"
    assert A._producer_applied(state, worker) == (change == "none")
