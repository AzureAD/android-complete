"""Locked, hash-only provider protocol: every provider operation is simulated."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator import cli, cli_common as C
from orchestrator.registry import AutomationRegistry, REGISTRY_SCHEMA_VERSION, STATUSES
from orchestrator.state import ReleaseState
from tests._automation import spec, observed


def prepare(reg, slug="worker", *, release="2026-09", kind="step-driving", cleanup="steps_done"):
    provider = spec(f"{release} · Scope — {slug}")
    entry = reg.prepare(
        provider["name"], release=release, slug=slug,
        steps=["ccd.localization"] if kind == "step-driving" else [],
        kind=kind, schedule=provider["schedule"], cleanup_when=cleanup, spec=provider,
    )
    return entry, provider


def reconcile(reg, entry, provider, *rows, claim=True):
    return reg.reconcile_create(
        entry["key"], observed(*rows), spec=provider, claim=claim, executor="test-owner",
    )


def active(reg, slug="worker", **kwargs):
    entry, provider = prepare(reg, slug, **kwargs)
    result = reconcile(reg, entry, provider, {"id": slug, "spec": provider})
    return result["entry"], provider


def stage(reg, status, slug="worker", **kwargs):
    entry, provider = prepare(reg, slug, **kwargs)
    if status in ("creating", "uncertain"):
        claim = reconcile(reg, entry, provider)
        if status == "uncertain":
            reg.create_result(entry["key"], claim["attempt_id"], "uncertain",
                              "receipt", spec=provider)
    elif status in ("active", "missing", "deleting", "delete_uncertain"):
        reconcile(reg, entry, provider, {"id": slug, "spec": provider})
        if status == "missing":
            reconcile(reg, entry, provider)
        elif status in ("deleting", "delete_uncertain"):
            deletion = reg.claim_delete(slug, "delete-owner")
            if status == "delete_uncertain":
                reg.delete_result(slug, deletion["attempt_id"], "uncertain", "receipt")
    elif status == "blocked":
        reconcile(reg, entry, provider, {"id": "one", "spec": provider},
                  {"id": "two", "spec": provider})
    return reg.get(key=entry["key"]), provider


@pytest.mark.parametrize("status", sorted(STATUSES))
def test_identical_prepare_preserves_entire_entry_and_changed_intent_rejects(tmp_path, status):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = stage(reg, status)
    before = (tmp_path / "2026-09" / "_automations.json").read_bytes()
    repeated, _ = prepare(reg)
    assert repeated == entry
    assert before == (tmp_path / "2026-09" / "_automations.json").read_bytes()
    with pytest.raises(ValueError, match="Cannot change"):
        reg.prepare(provider["name"], release="2026-09", slug="worker",
                    steps=["ccd.localization"], schedule=provider["schedule"],
                    cleanup_when="steps_done", spec={**provider, "prompt": "Changed"})
    assert reg.get(key=entry["key"]) == entry


@pytest.mark.parametrize("status", ["creating", "uncertain", "deleting", "delete_uncertain"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_read_reconcile_never_steals_unresolved_owner(tmp_path, status, count):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = stage(reg, status)
    rows = [{"id": "worker" if i == 0 else "duplicate", "spec": provider} for i in range(count)]
    result = reconcile(reg, entry, provider, *rows)
    assert not result["permission_to_create"]
    assert result["status"] == status
    assert reg.get(key=entry["key"]) == entry
    if status in ("creating", "uncertain"):
        result = reg.create_result(
            entry["key"], entry["attempts"][-1]["id"], "created",
            "exact authorized invocation returned worker", automation_id="worker", spec=provider,
        )
        assert result["status"] == "active"
    if status in ("deleting", "delete_uncertain"):
        assert reg.delete_result(
            "worker", entry["attempts"][-1]["id"], "deleted", "owning late receipt",
        )["status"] == "deleted"


def test_exact_claim_args_hash_binding_and_no_second_create(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    assert reconcile(reg, entry, provider, claim=False)["create_available"]
    first = reconcile(reg, entry, provider)
    assert first["permission_to_create"] and first["spec"] == provider
    assert not reconcile(reg, entry, provider)["permission_to_create"]
    with pytest.raises(ValueError, match="owning create"):
        reg.create_result(entry["key"], "wrong", "created", "receipt",
                          automation_id="worker", spec=provider)
    with pytest.raises(ValueError, match="intent hash"):
        reg.create_result(entry["key"], first["attempt_id"], "created", "receipt",
                          automation_id="worker", spec={**provider, "oneShot": True})
    reg.create_result(entry["key"], first["attempt_id"], "created", json.dumps(provider),
                      automation_id="worker", spec=provider)
    deletion = reg.claim_delete("worker", "cleaner")
    reg.delete_result("worker", deletion["attempt_id"], "uncertain", json.dumps(provider))
    stored = (tmp_path / "2026-09" / "_automations.json").read_text(encoding="utf-8")
    assert provider["prompt"] not in stored
    assert '"provider_spec"' not in stored and '"prompt"' not in stored
    assert '"model"' not in stored and '"browserHeadless"' not in stored
    assert all(a["evidence"].startswith("sha256:") for a in reg.get(key=entry["key"])["attempts"])


@pytest.mark.parametrize("change", [
    {"prompt": "Different"}, {"oneShot": True}, {"enabled": False},
    {"model": "other"}, {"teamsNotify": "always"}, {"browserHeadless": False},
    {"description": "Different"}, {"conditionCheckInterval": 30},
    {"steps": [{"label": "Step", "prompt": "Different"}]},
    {"schedule": "every 3 hours"},
])
def test_every_provider_setting_bound_and_mismatch_cannot_adopt(tmp_path, change):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    with pytest.raises(ValueError, match="intent hash"):
        reconcile(reg, entry, {**provider, **change})
    result = reconcile(reg, entry, provider, {"id": "drift", "spec": {**provider, **change}})
    assert result["status"] == "blocked" and not result["permission_to_create"]


@pytest.mark.parametrize("observations", [
    [], ["id"], {"complete": True, "automations": []},
    {"complete": False, "automations": [], "observed_at": "2026-01-01T00:00:00Z"},
])
def test_missing_or_incomplete_observations_reject(tmp_path, observations):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    with pytest.raises(ValueError):
        reg.reconcile_create(entry["key"], observations, spec=provider, claim=True, executor="x")
    assert reg.get(key=entry["key"])["status"] == "prepared"


def test_stale_and_ids_only_observation_reject(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    stale = observed()
    stale["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    for value in (stale, observed({"id": "x", "name": provider["name"]})):
        with pytest.raises(ValueError):
            reg.reconcile_create(entry["key"], value, spec=provider)


def test_read_before_latest_adoption_is_not_fresh_absence(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    old_zero = observed()
    reconcile(reg, entry, provider, {"id": "worker", "spec": provider})
    with pytest.raises(ValueError, match="stale"):
        reg.reconcile_create(entry["key"], old_zero, spec=provider)


def test_recorded_id_prevents_renamed_provider_absence(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = active(reg)
    renamed = {"id": "worker", "spec": {**provider, "name": "Renamed"}}
    result = reconcile(reg, entry, provider, renamed)
    assert result["status"] == "blocked"
    with pytest.raises(ValueError, match="matching"):
        reg.confirm_absent(entry["key"], observed(renamed), "owner evidence",
                           owner_confirmed=True, no_inflight=True)


def test_duplicates_and_missing_do_not_authorize_creation(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = active(reg)
    missing = reconcile(reg, entry, provider)
    assert missing["status"] == "missing" and not missing["permission_to_create"]
    entry2, provider2 = prepare(reg, "other")
    duplicate = reconcile(reg, entry2, provider2, {"id": "one", "spec": provider2},
                          {"id": "two", "spec": provider2})
    assert duplicate["status"] == "blocked" and not duplicate["permission_to_create"]


@pytest.mark.parametrize("status", ["missing", "blocked"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_nonowned_missing_blocked_reconcile_never_grants_creation(tmp_path, status, count):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = stage(reg, status)
    rows = [{"id": "worker" if status == "missing" else "one", "spec": provider}]
    if count == 2:
        rows.append({"id": "two", "spec": provider})
    result = reconcile(reg, entry, provider, *rows[:count])
    assert not result["permission_to_create"]
    if count == 1:
        assert result["status"] == "active"  # Exact full observation recovers a non-owned entry.
    else:
        assert result["status"] in ("missing", "blocked")


@pytest.mark.parametrize("status", sorted(STATUSES))
def test_parent_delete_barrier_counts_all_child_intents(tmp_path, status):
    reg = AutomationRegistry(str(tmp_path))
    parent, _ = active(reg, "push-reminders", kind="release-level", cleanup="release_done")
    stage(reg, status, slug="child")
    denied = reg.claim_delete(parent["id"], "cleaner")
    assert not denied["permission_to_delete"] and "barrier" in denied["reason"]
    assert reg.get(key=parent["key"])["status"] == "active"


def test_parent_waits_for_helpers_and_unresolved_sibling_operations(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    active(reg, "push-reminders", kind="release-level", cleanup="release_done")
    active(reg, "helper", kind="release-level", cleanup="release_done")
    assert not reg.claim_delete("push-reminders", "cleaner")["permission_to_delete"]
    claim = reg.claim_delete("helper", "cleaner")
    assert claim["permission_to_delete"]
    assert not reg.claim_delete("push-reminders", "cleaner")["permission_to_delete"]
    reg.delete_result("helper", claim["attempt_id"], "deleted", "receipt")
    assert reg.claim_delete("push-reminders", "cleaner")["permission_to_delete"]


@pytest.mark.parametrize("status", ["deleting", "delete_uncertain"])
def test_reverse_cleanup_barrier_blocks_prepare_and_claim_but_not_receipts(tmp_path, status):
    reg = AutomationRegistry(str(tmp_path))
    parent, provider = stage(reg, status, slug="parent", kind="release-level", cleanup="release_done")
    with pytest.raises(ValueError, match="deletion outstanding"):
        prepare(reg, "child")
    with pytest.raises(ValueError, match="deletion outstanding"):
        prepare(reg, "helper", kind="release-level", cleanup="release_done")
    # An explicit manual/shared worker is unrelated to automatic retirement.
    manual, _ = active(reg, "manual", kind="release-level", cleanup="manual")
    assert not reg.claim_delete(manual["id"], "cleaner")["permission_to_delete"]
    assert reconcile(reg, parent, provider)["status"] == status
    reg.delete_result(parent["id"], parent["attempts"][-1]["id"], "deleted", "receipt")
    assert prepare(reg, "child")[0]["status"] == "prepared"


def test_racing_parent_delete_and_child_prepare_is_serialized(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    active(reg, "parent", kind="release-level", cleanup="release_done")
    def child():
        try:
            prepare(reg, "child")
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(child)
        b = pool.submit(reg.claim_delete, "parent", "cleaner")
    assert a.result() != b.result()["permission_to_delete"]


def test_shared_manual_exempt_and_own_delete_ack_not_blocked(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    active(reg, "manual", kind="release-level", cleanup="manual")
    parent, _ = active(reg, "parent", kind="release-level", cleanup="release_done")
    claim = reg.claim_delete("parent", "cleaner")
    assert claim["permission_to_delete"]
    assert reg.delete_result(parent["id"], claim["attempt_id"], "deleted", "receipt")["status"] == "deleted"


def test_owner_absence_recovery_requires_quiescence_and_fresh_zero_read(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = stage(reg, "creating")
    with pytest.raises(ValueError, match="settle owning live"):
        reg.confirm_absent(entry["key"], observed(), "checked", owner_confirmed=True, no_inflight=True)
    reg.create_result(entry["key"], entry["attempts"][-1]["id"], "uncertain", "timeout", spec=provider)
    with pytest.raises(ValueError, match="Owner confirmation"):
        reg.confirm_absent(entry["key"], observed(), "checked")
    recovered = reg.confirm_absent(
        entry["key"], observed(), provider["prompt"], owner_confirmed=True, no_inflight=True)
    assert recovered["status"] == "prepared" and recovered["id"] is None
    assert recovered["attempts"][-1]["evidence"].startswith("sha256:")
    assert reconcile(reg, entry, provider)["permission_to_create"]


def test_absent_uncertain_delete_can_finish_without_reactivation(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, _ = stage(reg, "delete_uncertain")
    result = reg.confirm_absent(entry["key"], observed(), "owner verified operation finished",
                                owner_confirmed=True, no_inflight=True)
    assert result["status"] == "deleted" and reg.get(key=entry["key"]) is None


def test_abandon_is_verified_terminal_idless_prepared_only(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    entry, provider = prepare(reg)
    args = {"owner_confirmed": True, "terminal_check": lambda release: release == "2026-09"}
    with pytest.raises(ValueError, match="owner"):
        reg.abandon_prepared(entry["key"], observed(), "reason")
    with pytest.raises(ValueError, match="terminal"):
        reg.abandon_prepared(entry["key"], observed(), "reason", owner_confirmed=True)
    with pytest.raises(ValueError, match="matching"):
        reg.abandon_prepared(entry["key"], observed({"id": "live", "spec": provider}), "reason", **args)
    assert reg.abandon_prepared(entry["key"], observed(), "verified", **args)["status"] == "abandoned"
    assert reg.list() == []


@pytest.mark.parametrize("status", sorted(STATUSES - {"prepared"}))
def test_abandon_cannot_erase_unresolved_or_live_intent(tmp_path, status):
    reg = AutomationRegistry(str(tmp_path))
    entry, _ = stage(reg, status)
    with pytest.raises(ValueError, match="Only ID-less"):
        reg.abandon_prepared(entry["key"], observed(), "owner checked",
                             owner_confirmed=True, terminal_check=lambda _: True)


def test_verified_absent_intent_abandon_releases_cleanup_barrier(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    active(reg, "parent", kind="release-level", cleanup="release_done")
    entry, _ = stage(reg, "uncertain", slug="child")
    reg.confirm_absent(entry["key"], observed(), "provider completed without creating",
                       owner_confirmed=True, no_inflight=True)
    reg.abandon_prepared(entry["key"], observed(), "terminal release confirmed",
                         owner_confirmed=True, terminal_check=lambda _: True)
    assert reg.claim_delete("parent", "cleaner")["permission_to_delete"]


@pytest.mark.parametrize("old", [[], {"schema_version": 1, "entries": []},
                                    {"schema_version": 2, "entries": []}])
def test_old_schema_rejected_without_silent_migration(tmp_path, old):
    path = tmp_path / "_automations.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported automation registry schema"):
        AutomationRegistry(str(tmp_path)).list()
    assert json.loads(path.read_text()) == old
    assert REGISTRY_SCHEMA_VERSION == 3


def test_parallel_registry_updates_and_disabled_unsafe_entrypoints(tmp_path):
    def worker(index):
        reg = AutomationRegistry(str(tmp_path))
        active(reg, f"worker-{index}", release=f"2026-{index:02d}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(1, 5)))
    reg = AutomationRegistry(str(tmp_path))
    assert len(reg.list()) == 4
    with pytest.raises(ValueError, match="disabled"):
        reg.register("x", "y")
    with pytest.raises(ValueError, match="disabled"):
        reg.deregister("worker-1")


def test_cli_full_spec_required_and_exact_invocation_returned(tmp_path, capsys):
    from tests._context import fresh_orchestrator
    state = ReleaseState(release_id="2026-09")
    fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    C.save_state(state, str(tmp_path), state.release_id)
    provider = spec()
    base = ["--runs-root", str(tmp_path), "automation"]
    common = ["--release", "2026-09", "--slug", "custom", "--json"]
    metadata = ["--name", "Worker", "--schedule", "every 1 hour", "--cleanup-when", "release_done"]
    assert cli.main(base + ["prepare"] + common + metadata) == 1
    capsys.readouterr()
    explicit = ["--spec-json", json.dumps(provider)]
    assert cli.main(base + ["prepare"] + common + metadata + explicit) == 0
    capsys.readouterr()
    claim = base + ["reconcile-create"] + common + explicit + ["--claim", "--executor", "owner"]
    assert cli.main(claim) == 1
    assert not json.loads(capsys.readouterr().out)["permission_to_create"]
    assert cli.main(claim + ["--observed-json", json.dumps(observed())]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["permission_to_create"] and result["spec"] == provider


def test_unknown_and_incomplete_specs_fail_before_writing(tmp_path):
    reg = AutomationRegistry(str(tmp_path))
    for provider in ({**spec(), "slug": "not-a-provider-arg"}, {"name": "Worker"}):
        with pytest.raises(ValueError, match="unknown settings|missing"):
            reg.prepare("Worker", release="2026-09", slug="custom", schedule="every 1 hour",
                        cleanup_when="release_done", spec=provider)
    assert reg.list() == []


def test_cli_abandon_requires_actual_terminal_state(tmp_path, capsys):
    from tests._context import fresh_orchestrator
    state = ReleaseState(release_id="2026-09")
    fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    C.save_state(state, str(tmp_path), state.release_id)
    reg = AutomationRegistry(str(tmp_path))
    entry, _ = prepare(reg)
    base = ["--runs-root", str(tmp_path), "automation", "abandon-prepared",
            "--release", state.release_id, "--slug", "worker", "--json",
            "--confirm-absent", "--reason", "owner verified never created"]
    assert cli.main(base + ["--observed-json", json.dumps(observed())]) == 1
    assert "terminal" in capsys.readouterr().out
    assert reg.get(key=entry["key"]) == entry
    state.cancellation = {"reason": "owner cancelled", "at": datetime.now(timezone.utc).isoformat()}
    C.save_state(state, str(tmp_path), state.release_id)
    assert cli.main(base + ["--observed-json", json.dumps(observed())]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "abandoned"
    assert reg.list() == []


def test_cli_spec_file_is_read_only_and_not_copied(tmp_path, capsys):
    from tests._context import fresh_orchestrator
    state = ReleaseState(release_id="2026-09")
    fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    C.save_state(state, str(tmp_path), state.release_id)
    reviewed = tmp_path / "owner-reviewed.json"
    reviewed.write_text(json.dumps(spec()), encoding="utf-8")
    original = reviewed.read_bytes()
    base = ["--runs-root", str(tmp_path), "automation", "prepare",
            "--release", "2026-09", "--slug", "custom", "--json",
            "--name", "Worker", "--schedule", "every 1 hour", "--cleanup-when", "release_done",
            "--spec-file", str(reviewed)]
    assert cli.main(base) == 0
    capsys.readouterr()
    assert reviewed.read_bytes() == original
    assert spec()["prompt"] not in (tmp_path / "2026-09" / "_automations.json").read_text()
