"""Setup must observe M365 tools before init, without claiming read/write access."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from orchestrator import cli, cli_common as C, infra, m365_capabilities as M


ROOT = Path(__file__).resolve().parents[1]


def inventory():
    return {
        "version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "available_tools": {tool: tool for tool in M.REQUIRED_TOOLS},
    }


@pytest.fixture
def setup_manifest(tmp_path, monkeypatch):
    monkeypatch.delenv("RELEASE_AGENT_SCOUT_ROOT", raising=False)
    monkeypatch.delenv("RELEASE_AGENT_AGENCY_PATH", raising=False)
    home = tmp_path / "home"
    scout = home / ".scout"
    scout.mkdir(parents=True)
    config = scout / "m-mcp-servers.json"
    config.write_text(json.dumps({"servers": {
        "teams": {"tools": ["GetChat", "ListChatMembers"]},
    }}), encoding="utf-8")
    original_expand = infra.os.path.expanduser
    monkeypatch.setattr(infra.os.path, "expanduser",
                        lambda path: str(home) if path == "~" else original_expand(path))
    monkeypatch.setattr(infra, "scout_mcp_config_path", lambda: str(config))
    req = {
        "requirements": [],
        "mcp_servers": [{"id": "teams", "tools": ["GetChat", "ListChatMembers"]}],
        "m365_connectors": {"hosted_endpoint": "https://workiq.svc.cloud.microsoft/mcp"},
    }
    path = tmp_path / "requirements.yaml"
    path.write_text(yaml.safe_dump(req), encoding="utf-8")
    monkeypatch.setattr(C, "REQUIREMENTS_CONFIG", str(path))
    monkeypatch.setattr(C, "DEFAULT_RUNS_ROOT", str(tmp_path / "runs"))
    return path, config, req


def test_declared_connectors_without_inventory_fail_before_init(setup_manifest):
    path, config, _ = setup_manifest
    before = config.read_bytes()
    report = infra.run(str(path), register=False)
    assert report["infrastructure_ok"] and not report["ok"]
    assert report["mcp_servers"][0]["status"] == "present"
    checks = report["m365_connectors"]
    assert checks["status"] == "inventory_required"
    assert set(checks["required_tools"]) == set(M.REQUIRED_TOOLS)
    assert "before release initialization" in checks["detail"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("replace_fetch", [False, True])
def test_native_wrappers_do_not_replace_hosted_fetch(setup_manifest, replace_fetch):
    path, config, _ = setup_manifest
    before = config.read_bytes()
    tools = inventory()
    tools["available_tools"].pop("workiq.fetch")
    tools["available_tools"]["workiq_search_people"] = "workiq_search_people"
    if replace_fetch:
        tools["available_tools"]["workiq.fetch"] = "workiq_search_people"
    report = infra.run(str(path), register=False, tool_inventory=tools)
    assert report["infrastructure_ok"] and not report["ok"]
    checks = report["m365_connectors"]
    assert checks["status"] == "missing_tools"
    assert checks["missing_tools"] == ["workiq.fetch"]
    assert checks["hosted_endpoint"] == "https://workiq.svc.cloud.microsoft/mcp"
    assert "Configure and sign in" in checks["detail"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("alias", [
    "workiq.fetch", "workiq-fetch", "workiq:fetch", "mcp__workiq__fetch",
])
def test_fresh_tools_satisfy_setup_but_never_authorize_access(setup_manifest, alias):
    path, config, _ = setup_manifest
    before = config.read_bytes()
    tools = inventory()
    tools["available_tools"]["workiq.fetch"] = alias
    report = infra.run(str(path), register=False, tool_inventory=tools)
    assert report["ok"]
    checks = report["m365_connectors"]
    assert checks["status"] == "tools_available" and not checks["missing_tools"]
    assert checks["read_probes_required"]
    assert not checks["permission_to_send"] and not checks["permission_to_upload"]
    assert "not authenticated access proof" in checks["detail"]
    assert config.read_bytes() == before


@pytest.mark.parametrize("change", [
    "version", "missing_time", "naive", "stale", "future",
    "missing_tools", "non_mapping", "invalid_name",
])
def test_invalid_inventory_cannot_register_servers(setup_manifest, monkeypatch, change):
    path, _, _ = setup_manifest
    tools = inventory()
    if change == "version":
        tools["version"] = True
    elif change == "missing_time":
        tools.pop("observed_at")
    elif change == "naive":
        tools["observed_at"] = "2026-10-20T12:00:00"
    elif change in ("stale", "future"):
        delta = timedelta(minutes=-16 if change == "stale" else 1)
        tools["observed_at"] = (datetime.now(timezone.utc) + delta).isoformat()
    elif change == "missing_tools":
        tools.pop("available_tools")
    elif change == "non_mapping":
        tools["available_tools"] = list(M.REQUIRED_TOOLS)
    elif change == "invalid_name":
        tools["available_tools"]["workiq.fetch"] = {}
    monkeypatch.setattr(infra, "ensure_mcp_servers",
                        lambda *a: pytest.fail("invalid input reached registration"))
    with pytest.raises(ValueError):
        infra.run(str(path), register=True, tool_inventory=tools)


def test_legacy_manifest_without_m365_section_keeps_existing_contract(setup_manifest):
    path, _, req = setup_manifest
    req.pop("m365_connectors")
    path.write_text(yaml.safe_dump(req), encoding="utf-8")
    report = infra.run(str(path), register=False)
    assert report["ok"] and report["m365_connectors"] is None


@pytest.mark.parametrize("section", [None, {}, {"hosted_endpoint": "http://example.test/mcp"}])
def test_malformed_connector_declaration_is_not_ignored(setup_manifest, section):
    _, _, req = setup_manifest
    req["m365_connectors"] = section
    with pytest.raises(ValueError):
        infra.check_m365_connectors(req)


def test_infra_cli_reports_hold_and_accepts_fresh_file(setup_manifest, tmp_path, capsys):
    path, config, _ = setup_manifest
    before = config.read_bytes()
    assert cli.main(["infra", "--no-register", "--json"]) == 1
    held = json.loads(capsys.readouterr().out)
    assert held["infrastructure_ok"] and not held["ok"]
    assert held["m365_connectors"]["status"] == "inventory_required"
    tools = tmp_path / "loaded tools.json"
    tools.write_text(json.dumps(inventory()), encoding="utf-8-sig")
    assert cli.main(["infra", "--no-register", "--tools-file", str(tools), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["m365_connectors"]["read_probes_required"]
    assert config.read_bytes() == before
    assert set(tmp_path.iterdir()) == {path, config.parent.parent, tools}


@pytest.mark.parametrize("content", [None, "{bad json", "null", "[]"])
def test_infra_cli_rejects_bad_inventory_files(setup_manifest, tmp_path, capsys, content):
    path = tmp_path / "bad.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert cli.main(["infra", "--no-register", "--tools-file", str(path), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"] and result["error"]


def test_infra_human_output_does_not_announce_success_when_unverified(setup_manifest, capsys):
    assert cli.main(["infra", "--no-register"]) == 1
    output = capsys.readouterr().out
    assert "M365 connectors (loaded Scout tools)" in output
    assert "INVENTORY_REQUIRED" in output and "Setup incomplete" in output
    assert "Infrastructure OK" not in output and "Setup prerequisites OK" not in output


def test_machine_setup_does_not_discover_lock_or_adopt_an_existing_release(
        setup_manifest, monkeypatch, capsys):
    monkeypatch.setattr(C, "effective_release",
                        lambda *a: pytest.fail("setup bound to an existing release"))
    monkeypatch.setattr(C, "load_orch",
                        lambda *a: pytest.fail("setup loaded a release's pinned workflow"))
    assert cli.main(["infra", "--no-register", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["m365_connectors"]["status"] == "inventory_required"


def test_repo_manifest_declares_enforced_connector_contract():
    req = infra.load_requirements(str(ROOT / "config" / "requirements.yaml"))
    checks = infra.check_m365_connectors(req)
    assert checks["status"] == "inventory_required"
    assert "workiq.fetch" in checks["required_tools"]


def bootstrap_case(tmp_path, infra_exit=0, *, install_skill=False, missing=None):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 is required to execute the Windows bootstrap")
    agent = tmp_path / "agent with spaces"
    profile = tmp_path / "custom Scout profile"
    profile.mkdir()
    (agent / "setup").mkdir(parents=True)
    (agent / "config").mkdir()
    (agent / "orchestrator").mkdir()
    script = agent / "setup" / "bootstrap.ps1"
    shutil.copyfile(ROOT / "setup" / "bootstrap.ps1", script)
    for filename in ("requirements.yaml", "phases.yaml"):
        (agent / "config" / filename).write_text("{}", encoding="utf-8")
    (agent / "skill" / "reference").mkdir(parents=True)
    (agent / "skill" / "SKILL.md").write_text("# Test skill\n", encoding="utf-8")
    (agent / "skill" / "reference" / "commands.txt").write_text("reference content", encoding="utf-8")
    (agent / "orchestrator" / "__init__.py").write_text("", encoding="utf-8")
    (agent / "orchestrator" / "cli.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['BOOTSTRAP_CAPTURE']).write_text(json.dumps(sys.argv[1:]))\n"
        "print('M365 connector prerequisite check')\n"
        "sys.exit(int(os.environ['BOOTSTRAP_INFRA_EXIT']))\n", encoding="utf-8")
    capture = tmp_path / "captured.json"
    tools = tmp_path / "loaded tools.json"
    tools.write_text(json.dumps(inventory()), encoding="utf-8")
    flags = [
        pwsh, "-NoProfile", "-File", str(script), "-NonInteractive",
        "-NoAutoInstallPython", "-NoRestartScout", "-M365ToolsFile", str(tools),
        "-ScoutRoot", str(tmp_path / "missing profile" if missing == "profile" else profile),
        "-AgencyPath", str(tmp_path / "missing agency.exe" if missing == "agency" else sys.executable),
        "-PythonPath", str(tmp_path / "missing python.exe" if missing == "python" else sys.executable),
    ]
    if not install_skill:
        flags.append("-SkipSkillInstall")
    result = subprocess.run(flags, capture_output=True, text=True, timeout=60, env={
        **os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(agent), "BOOTSTRAP_CAPTURE": str(capture),
        "BOOTSTRAP_INFRA_EXIT": str(infra_exit), "PIP_NO_INDEX": "1",
    })
    return result, capture, tools, profile, agent


@pytest.mark.parametrize("infra_exit,install_skill", [(0, False), (1, False), (0, True)])
def test_bootstrap_forwards_inventory_and_propagates_incomplete_setup(tmp_path, infra_exit, install_skill):
    result, capture, tools, profile, agent = bootstrap_case(
        tmp_path, infra_exit, install_skill=install_skill)
    assert json.loads(capture.read_text()) == [
        "infra", "--scout-root", str(profile), "--agency-path", sys.executable, "--tools-file", str(tools)]
    assert result.returncode == infra_exit, result.stdout + result.stderr
    if infra_exit:
        assert "Setup is INCOMPLETE" in result.stdout
        assert "* Open Scout and run:" not in result.stdout
    else:
        assert "Setup prerequisites are ready" in result.stdout
    if install_skill:
        installed = profile / "m-skills" / "release-agent"
        for source in (agent / "skill").rglob("*"):
            if source.is_file():
                assert (installed / source.relative_to(agent / "skill")).read_bytes() == source.read_bytes()


@pytest.mark.parametrize("missing", ["profile", "agency", "python"])
def test_bootstrap_requests_missing_path_without_guessing_or_console_hang(tmp_path, missing):
    result, capture, _, _, _ = bootstrap_case(tmp_path, missing=missing)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[PATH REQUIRED]" in result.stdout
    assert "ask the user for this path" in result.stdout
    assert not capture.exists()
    assert not (tmp_path / "missing profile").exists()


@pytest.mark.parametrize("source", ["explicit", "environment", "path", "configured", "default"])
def test_agency_launcher_discovery_accepts_user_specific_locations(tmp_path, monkeypatch, source):
    launcher = tmp_path / "tools in a different folder" / "agency.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(b"test launcher metadata")
    monkeypatch.delenv("RELEASE_AGENT_AGENCY_PATH", raising=False)
    monkeypatch.setattr(infra.shutil, "which", lambda _: str(launcher) if source == "path" else None)
    if source == "environment":
        monkeypatch.setenv("RELEASE_AGENT_AGENCY_PATH", str(launcher))
    existing = {"icm": {"config": {"command": str(launcher)}}} if source == "configured" else {}
    req = {"mcp_servers": [{"launcher": "agency", "command": str(launcher)}]} if source == "default" else {}
    assert infra.resolve_agency_path(
        req, existing, str(launcher) if source == "explicit" else None) == str(launcher)


def test_explicit_bad_agency_path_does_not_fall_back_to_another_install(tmp_path, monkeypatch):
    launcher = tmp_path / "agency.exe"
    launcher.write_bytes(b"test")
    monkeypatch.setattr(infra.shutil, "which", lambda _: str(launcher))
    assert infra.resolve_agency_path({}, preferred=str(tmp_path / "missing.exe")) is None


def test_custom_profile_and_launcher_drive_requirements_and_registration(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "another profile"
    profile.mkdir()
    config = profile / "m-mcp-servers.json"
    existing = {"servers": {"unrelated": {"builtin": True}}}
    config.write_text(json.dumps(existing), encoding="utf-8")
    launcher = tmp_path / "different install" / "agency.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(b"test launcher metadata")
    monkeypatch.setattr(infra, "_shell_ok", lambda cmd: cmd == [str(launcher), "--version"])
    req = {
        "requirements": [{"id": "scout"}, {"id": "agency-cli"}],
        "mcp_servers": [{"id": "teams", "launcher": "agency",
                         "command": "missing-default.exe", "args": ["mcp", "teams"],
                         "tools": ["GetChat"]}],
    }
    path = tmp_path / "requirements.yaml"
    path.write_text(yaml.safe_dump(req), encoding="utf-8")
    report = infra.run(str(path), scout_root=str(profile), agency_path=str(launcher))
    assert report["ok"] and not report["path_requests"]
    assert report["scout_mcp_config"] == str(config)
    after = json.loads(config.read_text(encoding="utf-8"))
    assert after["servers"]["unrelated"] == existing["servers"]["unrelated"]
    assert after["servers"]["teams"]["config"]["command"] == str(launcher)
    assert list(profile.glob("m-mcp-servers.json.bak-*"))
    monkeypatch.setattr(C, "REQUIREMENTS_CONFIG", str(path))
    before = config.read_bytes()
    assert cli.main([
        "infra", "--no-register", "--scout-root", str(profile),
        "--agency-path", str(launcher), "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["scout_mcp_config"] == str(config)
    assert config.read_bytes() == before


def test_missing_profile_requests_path_without_creating_it(tmp_path):
    profile = tmp_path / "not installed here"
    path = tmp_path / "requirements.yaml"
    path.write_text("requirements: []\nmcp_servers: []\n", encoding="utf-8")
    report = infra.run(str(path), scout_root=str(profile))
    assert not report["ok"] and report["path_requests"][0]["flag"] == "--scout-root"
    assert not profile.exists()


def test_environment_profile_is_shared_with_readiness_helpers(tmp_path, monkeypatch):
    profile = tmp_path / "shared custom profile"
    profile.mkdir()
    config = profile / "m-mcp-servers.json"
    config.write_text('{"servers":{"teams":{"tools":["GetChat"]}}}', encoding="utf-8")
    monkeypatch.setenv("RELEASE_AGENT_SCOUT_ROOT", str(profile))
    assert infra.scout_mcp_config_path() == str(config)
    report = infra.ensure_mcp_servers({"mcp_servers": [{"id": "teams", "tools": ["GetChat"]}]}, False)
    assert report[0]["status"] == "present"


def test_missing_launcher_is_reported_as_a_path_request(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.delenv("RELEASE_AGENT_AGENCY_PATH", raising=False)
    monkeypatch.setattr(infra.shutil, "which", lambda _: None)
    path = tmp_path / "requirements.yaml"
    path.write_text(yaml.safe_dump({
        "mcp_servers": [{"id": "teams", "launcher": "agency", "tools": ["GetChat"]}],
    }), encoding="utf-8")
    report = infra.run(str(path), scout_root=str(profile))
    assert not report["ok"] and report["path_requests"][0]["flag"] == "--agency-path"
    assert report["mcp_servers"][0]["status"] == "launcher_missing"
    assert not (profile / "m-mcp-servers.json").exists()


def test_existing_custom_mcp_is_preserved_and_broken_launcher_is_not_replaced(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    launcher = tmp_path / "agency.exe"
    launcher.write_bytes(b"test")
    monkeypatch.setattr(infra.shutil, "which",
                        lambda command: str(launcher) if command == "agency.exe" else None)
    config = profile / "m-mcp-servers.json"
    req = {"mcp_servers": [{"id": "teams", "launcher": "agency", "tools": ["GetChat"]}]}
    for configured in (str(launcher), "agency.exe", str(tmp_path / "gone.exe")):
        config.write_text(json.dumps({"servers": {"teams": {
            "config": {"command": configured}, "tools": ["GetChat"],
        }}}), encoding="utf-8")
        before = config.read_bytes()
        result = infra.ensure_mcp_servers(
            req, True, scout_root=str(profile), agency_path=str(launcher))
        assert result[0]["status"] == (
            "present" if configured in (str(launcher), "agency.exe") else "launcher_missing")
        assert config.read_bytes() == before


def test_existing_file_that_is_not_a_working_agency_launcher_is_not_registered(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    launcher = tmp_path / "not agency.exe"
    launcher.write_bytes(b"not executable")
    monkeypatch.setattr(infra, "_shell_ok", lambda *a: False)
    result = infra.ensure_mcp_servers(
        {"mcp_servers": [{"id": "teams", "launcher": "agency", "tools": ["GetChat"]}]},
        True, scout_root=str(profile), agency_path=str(launcher))
    assert result[0]["status"] == "provider_missing"
    assert not (profile / "m-mcp-servers.json").exists()
