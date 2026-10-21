"""The new version rejects old runs explicitly, without rewriting or hiding them."""
import json

import pytest

from orchestrator import cli_common as C, discovery
from orchestrator.cli import build_parser, main
from orchestrator.state import ReleaseState, SCHEMA_VERSION


@pytest.mark.parametrize("schema", [None, 2, 3, 4.0, True])
def test_old_or_invalid_schema_is_rejected_without_rewrite(tmp_path, schema):
    path = tmp_path / "release-state.json"
    path.write_text(json.dumps({"schema_version": schema, "release_id": "old"}), encoding="utf-8")
    before = path.read_bytes()
    assert SCHEMA_VERSION == 4
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
    assert "schema 4" in result["release"]["error"]
    assert path.read_bytes() == before


def test_cli_reports_old_version_without_traceback_or_provider_calls(tmp_path, capsys):
    path = tmp_path / "old" / "release-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 3, "release_id": "old"}), encoding="utf-8")
    before = path.read_bytes()
    assert main(["--runs-root", str(tmp_path), "status", "--release", "old", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "schema 4" in result["error"]
    assert result["permission_to_execute"] is False
    assert path.read_bytes() == before
    assert not (path.parent / "events.jsonl").exists()


def test_cancel_write_reservation_is_not_a_new_version_command():
    choices = build_parser()._subparsers._group_actions[0].choices
    assert "cancel-write-reservation" not in choices
