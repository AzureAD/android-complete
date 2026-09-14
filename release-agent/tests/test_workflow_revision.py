"""Offline revision identity, stale-review and ownership regressions."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json
import os

import pytest
import yaml

from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState, StepState
from orchestrator import delivery
from orchestrator.revision import (
    adopt, adoption_preview, assert_current, bind_initial, current_revision,
    digest, phase_manifest, revision_id, runtime_hash,
)


def _orch(tmp_path, *, bind=True):
    config = {
        "version": 1,
        "phases": [
            {"id": phase, "name": phase.upper(), "steps": [
                {"id": "work", "name": "Work", "kind": "human_action"},
                {"id": "approve", "name": "Approve", "kind": "approval_gate"},
            ]}
            for phase in ("first", "second", "third")
        ],
    }
    path = tmp_path / "phases.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    state = ReleaseState(release_id="2026-07", ccd="2026-07-08",
                         owner_email="owner@example.test", timezone="UTC")
    orch = Orchestrator(str(path), state, mocks={}, as_of=date(2026, 7, 8))
    if bind:
        bind_initial(orch)
    return state, orch


def _complete(state):
    for phase in ("first", "second", "third"):
        for step in ("work", "approve"):
            state.set_step(phase, step, StepState(
                status="done", completed_at="2026-07-01T00:00:00+00:00",
                data={"evidence": "retained"}, links=[{"name": "proof", "url": "https://example.test"}]))
        state.gate_decisions.append({
            "step": phase + ".approve", "decision": "approved",
            "at": "2026-07-01T00:00:00+00:00", "by": "owner", "comment": None,
        })


def _save(state, tmp_path):
    path = tmp_path / "release-state.json"
    state.save(str(path))
    return path


def test_unbound_is_diagnostic_only_and_never_silently_bound(tmp_path):
    state, orch = _orch(tmp_path, bind=False)
    _complete(state)
    assert state.schema_version == 3
    assert orch.scheduling().status == "blocked"
    assert not orch.scheduling().runnable
    assert orch.status_report()["done"] == 0
    assert orch.step_once().kind == "blocked"
    assert orch.approve_gate().kind == "idle"
    with pytest.raises(ValueError, match="unbound"):
        assert_current(orch)
    path = _save(state, tmp_path)
    loaded = ReleaseState.load(str(path))
    loaded_orch = Orchestrator(orch.config_path, loaded, mocks={})
    assert loaded.workflow_revision is None
    with pytest.raises(ValueError, match="never-loaded"):
        bind_initial(loaded_orch)


def test_schema3_exact_binding_and_review_shapes(tmp_path):
    state, orch = _orch(tmp_path)
    review = {"execution_id": "exec", "hash": digest("input"), "approved_by": "owner",
              "approved_at": "2026-07-01T00:00:00Z", "workflow_revision": revision_id(state.workflow_revision)}
    state.set_step("first", "work", StepState(data={"last_write_review": review}))
    path = _save(state, tmp_path)
    assert ReleaseState.load(str(path)).workflow_revision == state.workflow_revision
    pristine = json.loads(path.read_text(encoding="utf-8"))
    changes = [
        lambda value: value.update(schema_version=2),
        lambda value: value.update(schema_version=3.0),
        lambda value: value["workflow_revision"].update(id=digest("redundant")),
        lambda value: value["workflow_revision"]["phases"][0].update(step_keys=["wrong.step"]),
        lambda value: value["workflow_revision"].update(runtime_hash="sha256:no"),
        lambda value: value["steps"]["first.work"]["data"]["last_write_review"].update(extra=True),
        lambda value: value["steps"]["first.work"]["data"]["last_write_review"].update(approved_by=" "),
    ]
    for change in changes:
        value = deepcopy(pristine)
        change(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError) as error:
            ReleaseState.load(str(path))
        assert "reinitialize" not in str(error.value).lower()
        assert "--force" not in str(error.value)
    value = deepcopy(pristine)
    value["steps"]["first.work"].update(
        status="running", execution={"id": "owned", "owner": "owner",
                                    "started_at": "now", "write_review": {"hash": digest(1), "approved_by": "owner"}})
    path.write_text(json.dumps(value), encoding="utf-8")
    ReleaseState.load(str(path))
    value["steps"]["first.work"]["execution"]["write_review"]["payload"] = {}
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="execution write review"):
        ReleaseState.load(str(path))


def test_semantic_phase_identity_ignores_labels_and_resolves_defaults(tmp_path):
    state, orch = _orch(tmp_path)
    before = revision_id(state.workflow_revision)
    orch.config["phases"][0]["name"] = "Display-only rename"
    orch.config["phases"][0]["steps"][0]["name"] = "Another label"
    orch.config["phases"][0]["execution"] = "sequential"
    assert revision_id(current_revision(orch)) == before
    assert_current(orch)
    orch.config["phases"][1]["execution"] = "parallel"
    assert revision_id(current_revision(orch)) != before
    with pytest.raises(ValueError, match="mismatch"):
        assert_current(orch)


def test_manifest_captures_bound_parameter_evidence_write_and_timing_contracts(tmp_path):
    from dataclasses import dataclass, replace
    from orchestrator.authority import EvidenceAuthority, OwnStepData, WriteCapabilities, WriteOperation
    from orchestrator.handlers import HandlerCatalog
    from orchestrator.handler_contracts import HookRole
    from orchestrator.parameters import ParameterSchema
    _, orch = _orch(tmp_path)
    initial = phase_manifest(orch.workflow, orch.handlers)
    handler = orch.handlers.get("first", "work")
    @dataclass(frozen=True)
    class Inputs:
        selection: str = "explicit-default"
    variants = [
        replace(handler, evidence=EvidenceAuthority((OwnStepData(),))),
        replace(handler, writes=WriteCapabilities((WriteOperation.SET_ASSIGNED_TO,))),
        replace(handler, fire_at_local="11:45"),
        replace(handler, parameters={HookRole.BUILD: ParameterSchema.compile(Inputs, "display-label")}),
    ]
    for variant in variants:
        mapping = dict(orch.handlers.handler_by_key)
        mapping["first.work"] = variant
        manifest = phase_manifest(orch.workflow, HandlerCatalog(mapping))
        assert manifest[0]["definition_hash"] != initial[0]["definition_hash"]
        assert manifest[1:] == initial[1:]


def test_runtime_content_identity_detects_same_size_mtime_and_excludes_nonexecution(tmp_path):
    (tmp_path / "orchestrator").mkdir()
    source = tmp_path / "orchestrator" / "runner.py"
    source.write_text("value=1", encoding="utf-8")
    (tmp_path / "config").mkdir()
    phases = tmp_path / "config" / "phases.yaml"
    phases.write_text("display: one", encoding="utf-8")
    before = runtime_hash(tmp_path)
    phases.write_text("display: two", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_case.py").write_text("ignored", encoding="utf-8")
    (tmp_path / "mocks.local.yaml").write_text("ignored", encoding="utf-8")
    secrets = tmp_path / "config" / "secrets"
    secrets.mkdir()
    (secrets / "provider.yaml").write_text("not-an-identity-input", encoding="utf-8")
    (tmp_path / "config" / "credentials.yaml").write_text("also-excluded", encoding="utf-8")
    assert runtime_hash(tmp_path) == before
    times = (source.stat().st_atime, source.stat().st_mtime)
    source.write_text("value=2", encoding="utf-8")
    os.utime(source, times)
    assert runtime_hash(tmp_path) != before


def test_disk_phase_change_stops_dispatch_without_restart_or_stale_cache(tmp_path):
    state, orch = _orch(tmp_path)
    config = deepcopy(orch.config)
    config["phases"][0]["execution"] = "parallel"
    with open(orch.config_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream)
    assert orch.step_once().kind == "blocked"
    assert not orch.scheduling().runnable
    assert orch.status_report()["workflow_revision_problem"]


def test_new_orchestrator_cannot_adopt_code_imported_before_a_runtime_edit(tmp_path, monkeypatch):
    state, orch = _orch(tmp_path)
    monkeypatch.setattr("orchestrator.revision._IMPORTED_SOURCE_HASH", digest("previous source"))
    current = Orchestrator(orch.config_path, state, mocks={})
    with pytest.raises(ValueError, match="restart"):
        assert_current(current)
    current.config["phases"][0]["execution"] = "parallel"
    plan = adoption_preview(current)
    assert any("restart" in blocker for blocker in plan["blockers"])
    with pytest.raises(ValueError, match="restart"):
        adopt(current, plan["hash"], by="owner", reason="reviewed")


def test_adoption_invalidates_earliest_change_and_later_preserving_evidence(tmp_path):
    state, orch = _orch(tmp_path)
    _complete(state)
    state.resources = {"known": {"status": "ready", "id": 123}}
    last_review = {"execution_id": "exec", "hash": digest("input"), "approved_by": "owner",
                   "approved_at": "then", "workflow_revision": revision_id(state.workflow_revision)}
    record = state.get_step("second", "work")
    record.data["last_write_review"] = last_review
    state.set_step("second", "work", record)
    old = revision_id(state.workflow_revision)
    orch.config["phases"][1]["execution"] = "parallel"
    before = deepcopy(asdict(state))
    plan = adoption_preview(orch)
    assert asdict(state) == before
    assert plan["invalidation"]["step_keys"] == [
        "second.approve", "second.work", "third.approve", "third.work"]
    assert plan["invalidation"]["completed_step_keys"] == [
        "second.approve", "second.work", "third.approve", "third.work"]
    assert plan["invalidation"]["blocked_step_keys"] == []
    assert plan["invalidation"]["gate_decision_records"] == [
        {
            "index": 1,
            "step": "second.approve",
            "decision": "approved",
            "at": "2026-07-01T00:00:00+00:00",
            "by": "owner",
        },
        {
            "index": 2,
            "step": "third.approve",
            "decision": "approved",
            "at": "2026-07-01T00:00:00+00:00",
            "by": "owner",
        },
    ]
    assert plan["invalidation"]["summary"] == {
        "affected": 4,
        "completed_reset": 4,
        "blocked_reset": 0,
        "gate_decisions_removed": 2,
        "notification_offers_removed": 0,
    }
    saved = []
    state._checkpoint = lambda: saved.append(deepcopy(asdict(state)))
    adopt(orch, plan["hash"], by="owner", reason="Reviewed executable contract")
    assert len(saved) == 1
    assert state.get_step("first", "work").status == "done"
    assert state.get_step("second", "work").status == "pending"
    assert state.get_step("second", "work").data["last_write_review"] == last_review
    assert state.get_step("second", "work").links
    assert state.resources["known"]["id"] == 123
    assert [item["step"] for item in state.gate_decisions] == ["first.approve"]
    assert state.workflow_revision["last_adoption"]["from_revision"] == old
    assert_current(orch)
    identity = revision_id(state.workflow_revision)
    state.workflow_revision["last_adoption"]["reason"] = "Different audit wording"
    assert revision_id(state.workflow_revision) == identity


def test_runtime_change_invalidates_all_including_removed_history(tmp_path):
    state, orch = _orch(tmp_path)
    _complete(state)
    state.set_step("removed", "old", StepState(status="done", data={"proof": 12}))
    state.workflow_revision["runtime_hash"] = digest("old runtime")
    plan = adoption_preview(orch)
    assert plan["runtime_changed"]
    assert "removed.old" in plan["invalidation"]["step_keys"]
    state._checkpoint = lambda: None
    adopt(orch, plan["hash"], by="owner", reason="Reviewed runtime")
    assert all(raw["status"] == "pending" for raw in state.steps.values())
    assert state.get_step("removed", "old").data == {"proof": 12}


def test_reordering_and_removed_steps_have_conservative_invalidations(tmp_path):
    state, orch = _orch(tmp_path)
    _complete(state)
    orch.config["phases"][1:] = reversed(orch.config["phases"][1:])
    plan = adoption_preview(orch)
    assert plan["invalidation"]["step_keys"] == [
        "second.approve", "second.work", "third.approve", "third.work"]
    orch.config["phases"][1]["steps"].pop(0)
    orch.config["phases"][1]["steps"].append({"id": "new", "name": "New", "kind": "human_action"})
    plan = adoption_preview(orch)
    assert plan["invalidation"]["new_step_keys"] == ["third.new"]
    assert plan["invalidation"]["removed_step_keys"] == ["third.work"]
    state._checkpoint = lambda: None
    adopt(orch, plan["hash"], by="owner", reason="Reviewed reordering")
    assert state.get_step("third", "new").status == "pending"
    assert state.get_step("third", "work").data == {"evidence": "retained"}


@pytest.mark.parametrize("blocker", ["removed_execution", "resource", "automation_create", "automation_delete"])
def test_adoption_refuses_ownership_outside_current_manifest(tmp_path, blocker):
    state, orch = _orch(tmp_path)
    orch.config["phases"][0]["execution"] = "parallel"
    entries = []
    if blocker == "removed_execution":
        state.set_step("removed", "old", StepState(
            status="running", execution={"id": "id", "owner": "owner", "started_at": "then"}))
    elif blocker == "resource":
        state.resources = {"nested": {"status": "creating"}}
    else:
        entries = [{"key": "worker", "scope": "release", "release": state.release_id,
                    "status": "creating" if blocker == "automation_create" else "delete_uncertain"}]
    plan = adoption_preview(orch, registry_entries=entries)
    assert plan["blockers"]
    before = deepcopy(asdict(state))
    with pytest.raises(ValueError, match="blocked"):
        adopt(orch, plan["hash"], by="owner", reason="reviewed", registry_entries=entries)
    assert asdict(state) == before


def test_resolved_resource_history_is_retained_not_mistaken_for_active_ownership(tmp_path):
    state, orch = _orch(tmp_path)
    state.resources = {"broker_test_plan": {
        "status": "ready", "plan_id": 123,
        "attempt_history": [{"status": "creating", "old_evidence": "retained"}],
    }}
    before = deepcopy(state.resources)
    orch.config["phases"][0]["execution"] = "parallel"
    plan = adoption_preview(orch)
    assert not plan["blockers"]
    state._checkpoint = lambda: None
    adopt(orch, plan["hash"], by="owner", reason="reviewed")
    assert state.resources == before


def test_adoption_fences_release_scoped_unclaimed_offers_without_false_receipts(tmp_path):
    state, orch = _orch(tmp_path)
    item = delivery.descriptor(state, "digest", {"kind": "release"}, "workiq_send_email",
                               {"to": ["owner@example.test"], "subject": "Old", "body": "Old"})
    delivery.offer(orch, item)
    orch.config["phases"][0]["execution"] = "parallel"
    plan = adoption_preview(orch)
    assert plan["invalidation"]["notification_offers"] == [item["id"]]
    state._checkpoint = lambda: None
    adopt(orch, plan["hash"], by="owner", reason="reviewed")
    assert item["id"] not in state.notification_deliveries
    with pytest.raises(ValueError, match="preparation"):
        delivery.claim(orch, item["id"], item["hash"], "worker")
    delivery.offer(orch, item)
    with pytest.raises(ValueError, match="checkpoint changed"):
        delivery.claim(orch, item["id"], item["hash"], "worker")


@pytest.mark.parametrize("status", ["claimed", "uncertain", "sent"])
def test_active_delivery_and_unfinished_completion_block_adoption(tmp_path, status):
    state, orch = _orch(tmp_path)
    item = delivery.descriptor(state, "digest", {"kind": "release"}, "workiq_send_email",
                               {"to": ["owner@example.test"], "subject": "Old", "body": "Old"})
    state.notification_deliveries[item["id"]] = {
        "descriptor": item, "status": status, "attempts": [
            {"id": "exec", "owner": "owner", "status": status, "hash": item["hash"]}]}
    orch.config["phases"][0]["execution"] = "parallel"
    assert adoption_preview(orch)["blockers"]


def test_stale_review_and_atomic_save_failure_preserve_every_fact(tmp_path):
    state, orch = _orch(tmp_path)
    orch.config["phases"][0]["execution"] = "parallel"
    plan = adoption_preview(orch)
    orch.config["phases"][1]["execution"] = "parallel"
    before = deepcopy(asdict(state))
    with pytest.raises(ValueError, match="Stale"):
        adopt(orch, plan["hash"], by="owner", reason="reviewed")
    assert asdict(state) == before
    plan = adoption_preview(orch)
    path = _save(state, tmp_path)
    before = deepcopy(asdict(state))
    disk = path.read_bytes()
    def fail():
        raise OSError("atomic replace failed")
    state._checkpoint = fail
    with pytest.raises(OSError, match="atomic replace"):
        adopt(orch, plan["hash"], by="owner", reason="reviewed")
    assert asdict(state) == before
    assert path.read_bytes() == disk


def test_cli_confirmation_runs_under_release_lock_and_persists_once(tmp_path, capsys):
    from orchestrator.cli import main
    state, orch = _orch(tmp_path)
    root = tmp_path / "runs"
    state.save(str(root / state.release_id / "release-state.json"))
    config = deepcopy(orch.config)
    config["phases"][1]["execution"] = "parallel"
    with open(orch.config_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream)
    args = ["--config", orch.config_path, "--runs-root", str(root), "workflow-adopt",
            "--release", state.release_id, "--json"]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert main(args + ["--approve-hash", plan["hash"], "--by", "owner", "--reason", "reviewed"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["adopted"]
    loaded = ReleaseState.load(str(root / state.release_id / "release-state.json"))
    assert revision_id(loaded.workflow_revision) == plan["new_revision"]
    assert loaded.workflow_revision["last_adoption"]["reason"] == "reviewed"
    from orchestrator.eventlog import EventLog
    events = EventLog(str(root), state.release_id).read()
    event = next(item for item in events if item["event"] == "workflow_adopted")
    assert event["old_revision"] == plan["old_revision"]
    assert event["new_revision"] == plan["new_revision"]
    assert event["reviewer"] == "owner" and event["reason"] == "reviewed"
    assert event["affected_step_keys"] == plan["invalidation"]["step_keys"]
    assert event["gate_decisions_removed"] == plan["invalidation"]["gate_decision_records"]


def test_status_refresh_is_state_read_only(tmp_path, monkeypatch, capsys):
    from argparse import Namespace
    from orchestrator import cli_common as C
    from orchestrator.commands import release

    state, orch = _orch(tmp_path)
    root = tmp_path / "runs"
    path = root / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()

    def refresh(loaded):
        loaded.ccd_conflict = "2026-07-09"
        return True

    monkeypatch.setattr(C, "refresh_conflict", refresh)
    args = Namespace(
        runs_root=str(root), release=state.release_id, config=orch.config_path,
        as_of=None, no_pipeline_check=False, json=True,
    )
    assert release.cmd_status(args) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["ccd_conflict"] == "2026-07-09"
    assert path.read_bytes() == before


def test_real_atomic_replace_failure_rolls_back_and_cleans_partial_file(tmp_path, monkeypatch):
    from orchestrator import cli_common as C
    state, orch = _orch(tmp_path)
    root = str(tmp_path / "runs")
    state.save(C.state_path(root, state.release_id))
    with C.state_lock(root, state.release_id):
        loaded, current = C.load_orch(root, state.release_id, orch.config_path)
        current.config["phases"][0]["execution"] = "parallel"
        plan = adoption_preview(current)
        before = deepcopy(asdict(loaded))
        disk = (tmp_path / "runs" / state.release_id / "release-state.json").read_bytes()
        def fail_replace(*args):
            raise OSError("replace failed")
        monkeypatch.setattr("orchestrator.state.os.replace", fail_replace)
        with pytest.raises(OSError, match="replace failed"):
            adopt(current, plan["hash"], by="owner", reason="reviewed")
        assert asdict(loaded) == before
        path = tmp_path / "runs" / state.release_id / "release-state.json"
        assert path.read_bytes() == disk
        assert not path.with_suffix(".json.tmp").exists()


def test_cli_stale_confirmation_rechecks_registry_claim_under_lock(tmp_path, capsys):
    from orchestrator.cli import main
    from orchestrator.registry import AutomationRegistry
    from tests._automation import spec, observed
    state, orch = _orch(tmp_path)
    root = tmp_path / "runs"
    state.save(str(root / state.release_id / "release-state.json"))
    config = deepcopy(orch.config)
    config["phases"][1]["execution"] = "parallel"
    with open(orch.config_path, "w", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream)
    args = ["--config", orch.config_path, "--runs-root", str(root), "workflow-adopt",
            "--release", state.release_id, "--json"]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    registry = AutomationRegistry(str(root), state.release_id)
    wanted = spec()
    entry = registry.prepare("Worker", slug="worker", spec=wanted,
                             schedule=wanted["schedule"], cleanup_when="release_done")
    claimed = registry.reconcile_create(entry["key"], observed(), spec=wanted, executor="worker", claim=True)
    assert claimed["permission_to_create"]
    assert main(args + ["--approve-hash", plan["hash"], "--by", "owner", "--reason", "reviewed"]) == 1
    assert "Stale" in json.loads(capsys.readouterr().out)["error"]
    assert registry.get(key=entry["key"])["status"] == "creating"
    assert revision_id(ReleaseState.load(str(root / state.release_id / "release-state.json")).workflow_revision) == plan["old_revision"]


@pytest.mark.parametrize("verb", [
    ["next"], ["verify"], ["sign", "--item", "anything"],
    ["checklist", "--verify"], ["init", "--force"],
])
def test_cli_unbound_mutations_fail_before_any_provider_or_overwrite(tmp_path, capsys, verb):
    from orchestrator.cli import main
    state, orch = _orch(tmp_path, bind=False)
    root = tmp_path / "runs"
    path = root / state.release_id / "release-state.json"
    state.save(str(path))
    before = path.read_bytes()
    assert main(["--config", orch.config_path, "--runs-root", str(root),
                 *verb, "--release", state.release_id]) == 1
    assert "unbound" in capsys.readouterr().out
    assert path.read_bytes() == before


@pytest.mark.parametrize("missing_state", [False, True])
def test_automation_delete_uses_stored_release_even_with_shared_flag(
        tmp_path, monkeypatch, capsys, missing_state):
    from orchestrator.cli import main
    from orchestrator.registry import AutomationRegistry
    state, orch = _orch(tmp_path, bind=False)
    root = tmp_path / "runs"
    if not missing_state:
        state.save(str(root / state.release_id / "release-state.json"))
    monkeypatch.setattr(AutomationRegistry, "get", lambda self, **kwargs: {
        "release": state.release_id, "scope": "release", "id": "worker",
    })
    def no_claim(*args, **kwargs):
        pytest.fail("Unbound/missing release cannot authorize an automation deletion")
    monkeypatch.setattr(AutomationRegistry, "claim_delete", no_claim)
    assert main([
        "--config", orch.config_path, "--runs-root", str(root),
        "automation", "claim-delete", "--id", "worker", "--executor", "runner", "--shared", "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["permission_to_execute"] is False
