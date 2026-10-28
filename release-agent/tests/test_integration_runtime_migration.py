"""Ownership-preserving runtime migration is not provider-write authorization."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from orchestrator import cli_common as C, integration_recovery as R, integration_runtime_migration as M, revision
from orchestrator import delivery
from orchestrator.commands import workflow_revision as CMD
from orchestrator.outcomes import Blocked
from orchestrator.state import ReleaseState, StepState
from test_integration_recovery import recovery, integration, checkout, execute


_LOAD_ORCH = C.load_orch


@pytest.mark.parametrize("count", [1, 2, 3])
def test_closed_write_review_preserves_first_migration_source_revision(count):
    from orchestrator.transitions import TransitionKernel

    execution = {
        "id": "original-execution", "owner": "original-owner",
        "started_at": "2026-10-21T00:00:00Z",
        "write_review": {"hash": "sha256:" + "1" * 64, "approved_by": "original-reviewer"},
    }
    history = [
        {"plan": {"execution": deepcopy(execution), "source_revision": "sha256:" + str(i + 2) * 64}}
        for i in range(count)
    ]
    data = {M.DATA_KEY: history[0] if count == 1 else {"version": 2, "receipts": history}}
    before = deepcopy(data[M.DATA_KEY])
    kernel = TransitionKernel(ReleaseState(), None, lambda: None, lambda: "unused")

    kernel._close_write_review(data, execution)

    assert data["last_write_review"] == {
        "execution_id": execution["id"], **execution["write_review"],
        "approved_at": execution["started_at"],
        "workflow_revision": history[0]["plan"]["source_revision"],
    }
    assert data[M.DATA_KEY] == before


@pytest.fixture
def migration(recovery):
    s = recovery
    binding = deepcopy(s.orch.state.workflow_revision)
    binding["runtime_hash"] = "sha256:" + "1" * 64
    s.orch.state.workflow_revision = binding
    original = json.loads(open(s.args.original_preview, encoding="utf-8").read())
    original["plan"]["workflow_revision"] = revision.revision_id(binding)
    original["review_hash"] = revision.digest(original["plan"])
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = original["review_hash"]
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(original, stream)
    with C.state_lock(s.args.runs_root, s.args.release):
        C.save_state(s.orch.state, s.args.runs_root, s.args.release)
    s.args.source_runtime_hash = None
    s.args.target_runtime_hash = None
    s.args.approve_hash = None
    s.args.by = "migration-reviewer"
    s.args.reason = "Permit receipt-backed continuation without replacing the original owner"
    s.source = deepcopy(binding)
    s.artifact_bytes = open(s.args.original_preview, "rb").read()
    return s


def approve(s, **kwargs):
    result = M.preview(s.orch, s.args, **kwargs)
    s.args.approve_hash = result["migration_hash"]
    s.args.source_runtime_hash = result["plan"]["source_runtime_hash"]
    s.args.target_runtime_hash = result["plan"]["target_runtime_hash"]
    return result


def apply(s, **kwargs):
    with C.state_lock(s.args.runs_root, s.args.release):
        return M.apply(s.orch, s.args, **kwargs)


def test_preview_reads_no_providers_and_changes_no_state(migration, monkeypatch):
    s = migration
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    def forbidden(*a, **kw):
        pytest.fail("Runtime migration must not call provider ports")
    for name in ("observe",):
        monkeypatch.setattr(R, name, forbidden)
    result = M.preview(s.orch, s.args)
    assert result["plan"]["source_runtime_hash"] == s.source["runtime_hash"]
    assert result["plan"]["source_revision"] != result["plan"]["target_revision"]
    assert result["plan"]["impact"]["provider_operations"] == []
    assert result["permission_to_execute"] is False
    assert result["permission_to_migrate"] is False
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk and not s.calls


def test_apply_preserves_all_owned_state_and_only_changes_binding_and_audit(migration):
    s = migration
    reviewed = approve(s)
    before = deepcopy(asdict(s.orch.state))
    result = apply(s)
    after = asdict(ReleaseState.load(str(s.path)))
    assert result["migrated"] and not result["permission_to_execute"] and not s.calls
    receipt = after["steps"]["finalize.integ_prs"]["data"].pop(M.DATA_KEY)
    assert receipt["hash"] == reviewed["migration_hash"]
    assert receipt["plan"]["source_binding"] == before["workflow_revision"]
    assert receipt["plan"]["original_review"] == before["steps"]["finalize.integ_prs"]["execution"]["write_review"]
    assert receipt["by"] == "migration-reviewer"
    assert before["workflow_revision"] != after["workflow_revision"]
    for field in before:
        if field not in ("workflow_revision", "updated_at"):
            assert after[field] == before[field], field
    assert open(s.args.original_preview, "rb").read() == s.artifact_bytes
    assert R.load_original(s.orch, s.args)["workflow_revision"] == revision.revision_id(s.source)
    # General adoption still rejects the same owner even after this migration.
    assert "steps.finalize.integ_prs: owned execution" in revision.adoption_preview(s.orch)["blockers"]


def test_migration_never_substitutes_for_fresh_recovery_review(migration):
    s = migration
    old_recovery_hash = R.preview(s.orch, s.args)["recovery_hash"]
    approve(s)
    apply(s)
    s.args.recovery_hash = old_recovery_hash
    with pytest.raises(ValueError, match="stale"):
        execute(s)
    fresh = R.preview(s.orch, s.args)
    assert fresh["recovery_hash"] != old_recovery_hash
    assert fresh["plan"]["runtime_migration_hash"] == s.args.approve_hash
    assert not s.calls
    s.args.recovery_hash = fresh["recovery_hash"]
    execute(s)
    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert record.status == "done"
    assert record.data["last_write_review"]["workflow_revision"] == revision.revision_id(s.source)
    assert record.data["last_write_review"]["hash"] == record.data[M.DATA_KEY]["plan"]["original_review"]["hash"]
    assert [call[0] for call in s.calls] == ["push", "create"]


@pytest.mark.parametrize("field,value", [
    ("approve_hash", "sha256:" + "0" * 64), ("by", ""), ("reason", ""),
    ("source_runtime_hash", None), ("target_runtime_hash", None),
    ("source_runtime_hash", "sha256:" + "2" * 64),
    ("target_runtime_hash", "sha256:" + "2" * 64),
    ("confirm_no_inflight", False), ("executor", "another"), ("execution_id", "another"),
])
def test_apply_rejects_wrong_or_missing_explicit_authority(migration, field, value):
    s = migration
    approve(s)
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    setattr(s.args, field, value)
    with pytest.raises(ValueError):
        apply(s)
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk and not s.calls


def test_apply_requires_existing_release_lock(migration):
    s = migration
    approve(s)
    with pytest.raises(ValueError, match="transaction lock"):
        M.apply(s.orch, s.args)
    assert not s.calls


@pytest.mark.parametrize("change", ["state", "disk", "artifact", "registry", "source", "target", "phases"])
def test_reviewed_migration_rejects_snapshot_source_target_or_artifact_drift(migration, monkeypatch, change):
    s = migration
    approve(s)
    if change == "state":
        s.orch.state.versions["common"] = "changed"
    elif change == "disk":
        other = ReleaseState.load(str(s.path))
        other.versions["common"] = "changed"
        other.save(str(s.path))
    elif change == "artifact":
        value = json.loads(open(s.args.original_preview, encoding="utf-8").read())
        value["plan"]["operations"][1]["content"]["title"] += " edited"
        with open(s.args.original_preview, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
    elif change == "source":
        s.orch.state.workflow_revision["runtime_hash"] = "sha256:" + "3" * 64
    elif change in ("target", "phases"):
        current, _ = revision._current_revision(s.orch)
        if change == "target":
            current["runtime_hash"] = "sha256:" + "4" * 64
        else:
            current["phases"][0]["definition_hash"] = "sha256:" + "5" * 64
        monkeypatch.setattr(revision, "_current_revision", lambda orch: (current, True))
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    kwargs = {"registry_entries": [{"scope": "shared", "status": "active", "key": "new"}]} if change == "registry" else {}
    with pytest.raises(ValueError):
        apply(s, **kwargs)
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk and not s.calls


@pytest.mark.parametrize("kind", ["other-owner", "resource", "automation", "notification"])
def test_other_unresolved_owners_are_never_migrated(migration, monkeypatch, kind):
    s = migration
    kwargs = {}
    if kind == "other-owner":
        # Even an unrelated raw owner is a hard blocker; no owner is cleared.
        s.orch.state.set_step("other", "owned", StepState(
            status="running", execution={"id": "other-id", "owner": "other", "started_at": "2026-10-21T00:00:00Z"}))
    elif kind == "resource":
        s.orch.state.resources["external"] = {"status": "uncertain"}
    elif kind == "automation":
        kwargs["registry_entries"] = [{"release": s.args.release, "key": "worker", "status": "creating"}]
    else:
        item = delivery.descriptor(s.orch.state, "digest", {"kind": "release"}, "workiq_send_email",
                                   {"to": ["owner@example.test"], "subject": "Old", "body": "Old"})
        s.orch.state.notification_deliveries[item["id"]] = {
            "descriptor": item, "status": "claimed", "attempts": [
                {"id": "notification-owner", "owner": "sender", "status": "claimed", "hash": item["hash"]}]}
    with pytest.raises(ValueError, match="blocked"):
        M.preview(s.orch, s.args, **kwargs)
    assert not s.calls


@pytest.mark.parametrize("failure", [OSError, KeyboardInterrupt])
def test_checkpoint_failure_rolls_back_entire_state_and_grants_no_permission(migration, monkeypatch, failure):
    s = migration
    approve(s)
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    def fail():
        raise failure("checkpoint interrupted")
    monkeypatch.setattr(s.orch.state, "_checkpoint", fail)
    with pytest.raises(failure):
        apply(s)
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk and not s.calls
    assert not s.orch._transition_kernel()._outcome_permits


@pytest.mark.parametrize("field", ["hash", "source", "target", "owner", "approval", "audit"])
def test_tampered_migration_receipt_cannot_authenticate_old_review(migration, field):
    s = migration
    approve(s)
    apply(s)
    receipt = s.orch.state.steps["finalize.integ_prs"]["data"][M.DATA_KEY]
    if field == "hash":
        receipt["hash"] = "sha256:" + "0" * 64
    elif field == "source":
        receipt["plan"]["source_runtime_hash"] = "sha256:" + "2" * 64
    elif field == "target":
        receipt["plan"]["target_runtime_hash"] = "sha256:" + "2" * 64
    elif field == "owner":
        receipt["plan"]["execution"]["owner"] = "other"
    elif field == "approval":
        receipt["plan"]["original_review"]["approved_by"] = "other"
    else:
        s.orch.state.workflow_revision["last_adoption"]["reason"] = "edited"
    if field not in ("hash", "audit"):
        receipt["hash"] = revision.digest(receipt["plan"])
    with pytest.raises(ValueError, match="frozen preview"):
        R.load_original(s.orch, s.args)
    assert not s.calls


def test_receipt_is_engine_owned_and_migration_is_not_repeatable(migration):
    s = migration
    approve(s)
    apply(s)
    kernel = s.orch._transition_kernel()
    assert not kernel.annotate_step("finalize", "integ_prs", data={M.DATA_KEY: {}}).changed
    assert not kernel.settle_execution("finalize", "integ_prs", s.args.execution_id,
                                       Blocked("edit migration"), data={M.DATA_KEY: {}}).changed
    with pytest.raises(ValueError, match="already current"):
        M.preview(s.orch, s.args)
    assert not s.calls


def test_recovery_completes_after_runtime_migration_chain(migration, monkeypatch):
    s = migration
    approve(s)
    apply(s)
    target = deepcopy(s.orch.state.workflow_revision)
    target["runtime_hash"] = "sha256:" + "6" * 64
    monkeypatch.setattr(revision, "_current_revision", lambda orch: (deepcopy(target), True))
    s.orch._loaded_runtime_hash = target["runtime_hash"]
    s.args.source_runtime_hash = s.args.target_runtime_hash = None
    approve(s)
    apply(s)
    history = deepcopy(M.receipts(s.orch.state.get_step("finalize", "integ_prs")))

    s.approve()
    execute(s)

    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert record.status == "done" and record.execution is None
    assert record.data["last_write_review"]["workflow_revision"] == revision.revision_id(s.source)
    assert record.data["last_write_review"]["hash"] == history[0]["plan"]["original_review"]["hash"]
    assert M.receipts(record) == history
    assert all(receipt["status"] == "applied" for receipt in record.data[R.DATA_KEY]["receipts"])


def test_second_migration_preserves_legacy_receipt_recovery_and_original(migration, monkeypatch):
    s = migration
    approve(s)
    apply(s)
    prior = deepcopy(s.orch.state.get_step("finalize", "integ_prs").data[M.DATA_KEY])
    prior_binding = deepcopy(s.orch.state.workflow_revision)
    old_recovery = R.preview(s.orch, s.args)
    record = s.orch.state.get_step("finalize", "integ_prs")
    record.data[R.DATA_KEY] = {
        "original_hash": record.execution["write_review"]["hash"],
        "execution_id": record.execution["id"],
        "receipts": old_recovery["plan"]["receipts"],
        "approvals": [{"hash": old_recovery["recovery_hash"], "approved_by": "first", "at": "old"}]}
    s.orch.state.set_step("finalize", "integ_prs", record)
    with C.state_lock(s.args.runs_root, s.args.release):
        C.save_state(s.orch.state, s.args.runs_root, s.args.release)
    before = deepcopy(asdict(s.orch.state))
    target = deepcopy(prior_binding)
    target["runtime_hash"] = "sha256:" + "6" * 64
    monkeypatch.setattr(revision, "_current_revision", lambda orch: (deepcopy(target), True))
    s.orch._loaded_runtime_hash = target["runtime_hash"]
    s.args.source_runtime_hash = s.args.target_runtime_hash = None
    reviewed = approve(s)
    assert reviewed["plan"]["version"] == 2
    assert reviewed["plan"]["previous_receipts_hash"] == revision.digest([prior])
    apply(s)
    after = asdict(s.orch.state)
    chain = after["steps"]["finalize.integ_prs"]["data"].pop(M.DATA_KEY)
    assert chain["receipts"][0] == prior
    assert len(chain["receipts"]) == 2
    before["steps"]["finalize.integ_prs"]["data"].pop(M.DATA_KEY)
    for key in before:
        if key not in ("workflow_revision", "updated_at"):
            assert after[key] == before[key]
    assert R.load_original(s.orch, s.args)["workflow_revision"] == revision.revision_id(s.source)
    assert open(s.args.original_preview, "rb").read() == s.artifact_bytes
    assert not s.calls
    s.orch.state.steps["finalize.integ_prs"]["data"][M.DATA_KEY]["receipts"][0]["reason"] += " tamper"
    with pytest.raises(ValueError, match="frozen preview"):
        R.load_original(s.orch, s.args)


@pytest.mark.parametrize("journal", ["integration_recovery", "integration_write_journal"])
def test_migration_forbids_active_or_uncertain_operation(migration, journal):
    s = migration
    record = s.orch.state.get_step("finalize", "integ_prs")
    record.data[journal] = ({
        "original_hash": record.execution["write_review"]["hash"],
        "execution_id": record.execution["id"], "receipts": [{"status": "in_flight"}]}
        if journal == R.DATA_KEY else {
            "execution_id": record.execution["id"], "original_hash": record.execution["write_review"]["hash"],
            "operations": {"0": {"status": "in_flight"}}})
    s.orch.state.set_step("finalize", "integ_prs", record)
    with pytest.raises(ValueError, match="uncertain"):
        M.preview(s.orch, s.args)
    assert not s.calls


@pytest.mark.parametrize("race", ["state", "runtime"])
def test_race_during_apply_second_review_is_rejected(migration, monkeypatch, race):
    s = migration
    approve(s)
    actual = M.preview
    count = 0
    def raced(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            if race == "state":
                s.orch.state.versions["common"] = "concurrent update"
            else:
                monkeypatch.setattr(revision, "_current_revision",
                                    lambda orch: (deepcopy(s.source), False))
        return actual(*args, **kwargs)
    monkeypatch.setattr(M, "preview", raced)
    disk = s.path.read_bytes()
    with pytest.raises(ValueError):
        apply(s)
    assert s.path.read_bytes() == disk and s.orch.state.workflow_revision == s.source
    assert M.DATA_KEY not in s.orch.state.get_step("finalize", "integ_prs").data
    assert not s.calls


def test_cli_read_only_preview_is_available_on_old_bound_runtime(migration, monkeypatch, capsys):
    from orchestrator.cli import main
    s = migration
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    def no_provider(*a, **kw):
        pytest.fail("Migration preview cannot reconcile or call providers")
    monkeypatch.setattr(R, "observe", no_provider)
    assert main(["--runs-root", s.args.runs_root, "--config", s.args.config,
                 "migrate-integration-recovery-runtime", "--release", s.args.release,
                 "--execution-id", s.args.execution_id, "--executor", s.args.executor,
                 "--original-preview", s.args.original_preview, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["migration_hash"] and not result["permission_to_execute"]
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk


def test_restart_on_reviewed_target_is_required(migration, monkeypatch):
    s = migration
    actual, _ = revision._current_revision(s.orch)
    monkeypatch.setattr(revision, "_current_revision", lambda orch: (actual, False))
    with pytest.raises(ValueError, match="restart"):
        M.preview(s.orch, s.args)
    assert not s.calls


def test_completed_gate_decisions_and_unclaimed_notification_offers_are_preserved(migration):
    s = migration
    s.orch.config["phases"][0]["steps"].append(
        {"id": "historical_gate", "name": "Historical gate", "kind": "approval_gate"})
    s.orch.state.workflow_revision["phases"] = revision.current_revision(s.orch)["phases"]
    s.source = deepcopy(s.orch.state.workflow_revision)
    original = json.loads(open(s.args.original_preview, encoding="utf-8").read())
    original["plan"]["workflow_revision"] = revision.revision_id(s.source)
    original["review_hash"] = revision.digest(original["plan"])
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = original["review_hash"]
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(original, stream)
    stamp = s.orch.step_execution("finalize", "integ_prs")["started_at"]
    s.orch.state.set_step("finalize", "historical_gate",
                          StepState(status="done", completed_at=stamp, by="human"))
    s.orch.state.gate_decisions.append(
        {"step": "finalize.historical_gate", "decision": "approved", "at": stamp,
         "by": "original-owner", "comment": "Historical manual decision"})
    item = delivery.descriptor(s.orch.state, "digest", {"kind": "release"}, "workiq_send_email",
                               {"to": ["owner@example.test"], "subject": "Old", "body": "Old"})
    s.orch.state.notification_deliveries[item["id"]] = {
        "descriptor": item, "status": "prepared", "attempts": []}
    with C.state_lock(s.args.runs_root, s.args.release):
        C.save_state(s.orch.state, s.args.runs_root, s.args.release)
    before = deepcopy(asdict(s.orch.state))
    approve(s)
    apply(s)
    after = asdict(ReleaseState.load(str(s.path)))
    assert after["gate_decisions"] == before["gate_decisions"]
    assert after["notification_deliveries"] == before["notification_deliveries"]
    assert after["steps"]["finalize.historical_gate"] == before["steps"]["finalize.historical_gate"]
    assert not s.calls


def test_cli_registration_has_no_force_or_provider_execution_argument():
    from orchestrator.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["migrate-integration-recovery-runtime", "--release", "r",
                              "--execution-id", "original", "--executor", "owner",
                              "--original-preview", "original.json"])
    assert args.approve_hash is None
    assert not hasattr(args, "execute") and not hasattr(args, "force")


@pytest.mark.parametrize("registry_race", [False, True])
def test_real_cli_transaction_checkpoint_and_registry_recheck(migration, monkeypatch, capsys, registry_race):
    from orchestrator.cli import main
    from orchestrator.registry import AutomationRegistry
    from tests._automation import spec, observed
    s = migration
    monkeypatch.setattr(C, "load_orch", _LOAD_ORCH)
    args = ["--runs-root", s.args.runs_root, "--config", s.args.config,
            "migrate-integration-recovery-runtime", "--release", s.args.release,
            "--execution-id", s.args.execution_id, "--executor", s.args.executor,
            "--original-preview", s.args.original_preview, "--json"]
    assert main(args) == 0
    reviewed = json.loads(capsys.readouterr().out)
    disk = s.path.read_bytes()
    if registry_race:
        registry = AutomationRegistry(s.args.runs_root, s.args.release)
        wanted = spec()
        entry = registry.prepare("Worker", slug="worker", spec=wanted,
                                 schedule=wanted["schedule"], cleanup_when="release_done")
        assert registry.reconcile_create(entry["key"], observed(), spec=wanted,
                                         executor="worker", claim=True)["permission_to_create"]
    assert main(args + [
        "--approve-hash", reviewed["migration_hash"],
        "--source-runtime-hash", reviewed["plan"]["source_runtime_hash"],
        "--target-runtime-hash", reviewed["plan"]["target_runtime_hash"],
        "--by", s.args.by, "--reason", s.args.reason, "--confirm-no-inflight"]) == int(registry_race)
    result = json.loads(capsys.readouterr().out)
    saved = ReleaseState.load(str(s.path))
    if registry_race:
        assert "blocked" in result["error"] or "Stale" in result["error"]
        assert s.path.read_bytes() == disk
        assert registry.get(key=entry["key"])["status"] == "creating"
    else:
        assert result["migrated"] and not result["permission_to_execute"]
        record = saved.get_step("finalize", "integ_prs")
        assert record.execution == s.orch.step_execution("finalize", "integ_prs")
        assert record.data[M.DATA_KEY]["hash"] == reviewed["migration_hash"]
    assert not s.calls
