"""The new version rejects old runs explicitly, without rewriting or hiding them."""
import json

import pytest

from orchestrator import cli_common as C, discovery
from orchestrator.cli import build_parser, main
from orchestrator.state import ReleaseState, SCHEMA_VERSION
from tests._context import fresh_orchestrator


@pytest.mark.parametrize("schema", [None, 2, 3, 4, 5.0, True])
def test_old_or_invalid_schema_is_rejected_without_rewrite(tmp_path, schema):
    path = tmp_path / "release-state.json"
    path.write_text(json.dumps({"schema_version": schema, "release_id": "old"}), encoding="utf-8")
    before = path.read_bytes()
    assert SCHEMA_VERSION == 5
    with pytest.raises(ValueError, match="not supported or automatically migrated"):
        ReleaseState.load(str(path))
    assert path.read_bytes() == before


def test_old_run_remains_visible_as_blocked_not_missing(tmp_path):
    path = tmp_path / "old" / "release-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 3, "release_id": "old"}), encoding="utf-8")
    before = path.read_bytes()
    result = discovery.resolve(str(tmp_path), "old", C.DEFAULT_CONFIG)
    assert result["resolution"] == "explicit"
    assert result["release"]["status"] == "blocked"
    assert "schema 5" in result["release"]["error"]
    assert path.read_bytes() == before


def test_cli_reports_old_version_without_traceback_or_provider_calls(tmp_path, capsys):
    path = tmp_path / "old" / "release-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 3, "release_id": "old"}), encoding="utf-8")
    before = path.read_bytes()
    assert main(["--runs-root", str(tmp_path), "status", "--release", "old", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "schema 5" in result["error"]
    assert result["permission_to_execute"] is False
    assert path.read_bytes() == before
    assert not (path.parent / "events.jsonl").exists()


def test_cancel_write_reservation_is_not_a_new_version_command():
    choices = build_parser()._subparsers._group_actions[0].choices
    assert "cancel-write-reservation" not in choices


def test_inspect_state_reads_unsupported_metadata_without_migrating(tmp_path, capsys, monkeypatch):
    path = tmp_path / "old" / "release-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 3, "release_id": "old",
                               "workflow_revision": {"execution_contract": 2},
                               "steps": {}}), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setattr(ReleaseState, "load", lambda *a: pytest.fail("Inspection must not load executable state"))
    assert main(["--runs-root", str(tmp_path), "inspect-state", "--release", "old"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "needs_input" and not result["supported"]
    assert not result["permission_to_execute"]
    assert path.read_bytes() == original
    assert not (path.parent / "events.jsonl").exists()


def test_manual_only_task_is_not_automatic_success_and_can_record_actual_owner_work(tmp_path, capsys):
    import yaml
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "manual", "name": "Manual", "steps": [{
            "id": "work", "name": "Externally performed work", "kind": "auto",
            "implementation": "dummy"}]}]}), encoding="utf-8")
    state = ReleaseState(release_id="manual-run", owner_email="owner@example.test")
    fresh_orchestrator(str(config), state, mocks={})
    state.save(str(tmp_path / state.release_id / "release-state.json"))
    prefix = ["--config", str(config), "--runs-root", str(tmp_path)]
    assert main([*prefix, "task", "--release", state.release_id,
                 "--phase", "manual", "--step", "work"]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == "needs_input"
    assert not C.load_state(str(tmp_path), state.release_id).is_done("manual", "work")
    assert main([*prefix, "record-fact", "--release", state.release_id,
                 "--phase", "manual", "--step", "work", "--note",
                 "Owner confirms the external work is complete", "--json"]) == 0
    outcome = json.loads(capsys.readouterr().out)
    completed = C.load_state(str(tmp_path), state.release_id).get_step("manual", "work")
    assert outcome["verification"] == "owner_attested"
    assert completed.status == "done" and completed.by == "human"


def test_owner_handoff_revalidates_readiness_without_erasing_completed_work(tmp_path, capsys):
    from orchestrator.state import StepState
    state = ReleaseState(release_id="handoff", owner_email="old@example.test")
    fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    state.readiness_items = {
        "oncall_now": {"status": "pass"}, "yubikey": {"status": "attested"}}
    state.set_step("preflight", "cg", StepState(status="done", note="Verified source", by="agent"))
    state.save(str(tmp_path / state.release_id / "release-state.json"))
    assert main(["--runs-root", str(tmp_path), "set-owner", "--release", state.release_id,
                 "--owner-email", "new@example.test"]) == 0
    capsys.readouterr()
    saved = C.load_state(str(tmp_path), state.release_id)
    assert saved.owner_email == "new@example.test" and not saved.readiness_items
    assert saved.is_done("preflight", "cg")
