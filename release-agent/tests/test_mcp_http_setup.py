"""Generic HTTP registration remains supported without requiring remote WorkIQ."""
from copy import deepcopy
import json

import pytest
import yaml

from orchestrator import cli, cli_common as C, infra, onboarding as O
from tests.test_onboarding import readiness


ENDPOINT = "https://connector.example.test/mcp"
HTTP_SERVER = {
    "id": "workiq", "name": "Example HTTP MCP", "scout_key": "workiq",
    "type": "http", "url": ENDPOINT, "server_name": "workiq", "tools": [],
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    profile = tmp_path / "custom user profile with spaces"
    profile.mkdir()
    config = profile / "m-mcp-servers.json"
    initial = {"servers": {"icm": {
        "builtin": False, "config": {"name": "ICM", "type": "command", "command": "custom.exe"},
        "tools": ["get_on_call_schedule_by_team_id"],
    }}, "user_metadata": {"keep": True}}
    config.write_text(json.dumps(initial), encoding="utf-8")
    manifest = {"requirements": [], "mcp_servers": [deepcopy(HTTP_SERVER)],
                "m365_connectors": {"calendar_mail_files": "native WorkIQ", "teams": "Agency Teams"}}
    path = tmp_path / "requirements.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(C, "REQUIREMENTS_CONFIG", str(path))
    monkeypatch.setattr(C, "DEFAULT_RUNS_ROOT", str(tmp_path / "runs"))
    return profile, config, path, manifest, initial


def test_missing_http_is_read_only_preview_then_automatic_registration(setup):
    profile, config, _, req, initial = setup
    before = config.read_bytes()
    preview = infra.ensure_mcp_servers(req, False, scout_root=str(profile))
    assert preview[0]["status"] == "would_register" and preview[0]["transport"] == "http"
    assert config.read_bytes() == before and not list(profile.glob("*.bak-*"))
    result = infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert result[0]["status"] == "registered"
    installed = json.loads(config.read_text(encoding="utf-8"))
    assert installed == {**initial, "servers": {**initial["servers"], "workiq": {
        "builtin": False, "config": {"name": "workiq", "type": "http", "url": ENDPOINT}, "tools": [],
    }}}
    backups = list(profile.glob("m-mcp-servers.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    after = config.read_bytes()
    assert infra.ensure_mcp_servers(req, True, scout_root=str(profile))[0]["status"] == "present"
    assert config.read_bytes() == after and list(profile.glob("m-mcp-servers.json.bak-*")) == backups


def test_registration_plan_never_asks_to_inspect_or_fill_the_host_form(setup):
    profile, _, path, _, _ = setup
    report = infra.run(str(path), register=False, scout_root=str(profile))
    plan = O.plan(report, None, readiness())
    assert plan["action"]["kind"] == "register_mcp" and plan["status"] == "checking"
    assert plan["action"]["command"] == ["infra"]
    assert "question" not in plan["action"]
    assert not plan["permission_to_start_release"]
    registered = infra.run(str(path), register=True, scout_root=str(profile))
    assert registered["restart_needed"] and registered["infrastructure_ok"]
    # Registration alone never supplies a successful tool/access observation.
    assert not registered["ok"]
    assert registered["m365_connectors"]["status"] == "inventory_required"


@pytest.mark.parametrize("existing_key", ["workiq", "renamed-by-owner"])
def test_existing_endpoint_preserves_name_tools_auth_and_metadata(setup, existing_key):
    profile, config, _, req, initial = setup
    entry = {"builtin": False, "config": {
        "name": "Existing WorkIQ", "type": "http", "url": ENDPOINT + "/",
        "headersEncrypted": "opaque-test-placeholder", "clientId": "existing-client",
    }, "tools": ["fetch", "ask"]}
    initial["servers"][existing_key] = entry
    config.write_text(json.dumps(initial), encoding="utf-8")
    before = config.read_bytes()
    result = infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert result[0]["status"] == "present" and result[0]["scout_key"] == existing_key
    assert config.read_bytes() == before
    assert not list(profile.glob("*.bak-*"))


@pytest.mark.parametrize("entry", [
    {"builtin": True},
    {"builtin": False, "config": {"name": "workiq", "type": "command", "command": "custom.exe"}},
    {"builtin": False, "config": {"name": "workiq", "type": "sse", "url": ENDPOINT}},
    {"builtin": False, "enabled": False, "config": {"name": "workiq", "type": "http", "url": ENDPOINT}},
    {"builtin": False, "config": {"name": "workiq", "type": "http", "url": "https://other.example/mcp"}},
    {"builtin": False, "config": {"name": "workiq", "type": "http", "url": None}},
])
def test_existing_conflicting_or_disabled_connection_is_not_overwritten(setup, entry):
    profile, config, _, req, initial = setup
    initial["servers"]["workiq"] = entry
    config.write_text(json.dumps(initial), encoding="utf-8")
    before = config.read_bytes()
    result = infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert result[0]["status"] == "configuration_conflict"
    assert config.read_bytes() == before and not list(profile.glob("*.bak-*"))


def test_duplicate_http_endpoints_are_a_visible_conflict_not_a_third_connection(setup):
    profile, config, _, req, initial = setup
    entry = {"builtin": False, "config": {"name": "workiq", "type": "http", "url": ENDPOINT}}
    initial["servers"].update(workiq=entry, another=entry)
    config.write_text(json.dumps(initial), encoding="utf-8")
    before = config.read_bytes()
    result = infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert result[0]["status"] == "configuration_conflict"
    assert config.read_bytes() == before


def test_concurrent_host_edit_is_not_overwritten_and_temporary_file_is_removed(setup, monkeypatch):
    profile, config, _, req, initial = setup
    backup = infra._backup
    updated = deepcopy(initial)
    updated["servers"]["user-added"] = {"builtin": False, "config": {
        "name": "user-added", "type": "http", "url": "https://user.example/mcp"}}

    def host_writes_after_backup(path):
        saved = backup(path)
        config.write_text(json.dumps(updated), encoding="utf-8")
        return saved

    monkeypatch.setattr(infra, "_backup", host_writes_after_backup)
    with pytest.raises(ValueError, match="changed during setup"):
        infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert json.loads(config.read_text()) == updated
    assert not list(profile.glob(".mcp-setup-*.tmp"))


@pytest.mark.parametrize("url", [
    "http://workiq.example/mcp", "https://user:secret@workiq.example/mcp",
    "https://workiq.example/mcp?access_token=secret", "https://workiq.example/mcp#fragment", None,
])
def test_invalid_http_manifest_cannot_write_or_include_authentication(setup, url):
    profile, config, _, req, _ = setup
    before = config.read_bytes()
    req["mcp_servers"][0]["url"] = url
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        infra.ensure_mcp_servers(req, True, scout_root=str(profile))
    assert config.read_bytes() == before


def test_infra_cli_registers_http_without_release_dispatch_or_a_form_question(setup, monkeypatch, capsys):
    profile, config, _, _, _ = setup
    monkeypatch.setattr(C, "effective_release", lambda *a: pytest.fail("release discovery"))
    monkeypatch.setattr(C, "load_orch", lambda *a: pytest.fail("release loaded"))
    result = cli.main(["infra", "--scout-root", str(profile), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert result == 1 and report["restart_needed"]
    assert report["mcp_servers"][0]["status"] == "registered"
    assert json.loads(config.read_text())["servers"]["workiq"]["config"]["type"] == "http"
    assert not (profile.parent / "runs").exists()


def test_repo_requires_fetch_capability_but_does_not_register_hosted_workiq():
    req = infra.load_requirements(C.REQUIREMENTS_CONFIG)
    assert not any(server["id"] == "workiq" for server in req["mcp_servers"])
    assert "hosted_endpoint" not in req["m365_connectors"]
    assert "workiq.fetch" in infra.check_m365_connectors(req)["required_tools"]
