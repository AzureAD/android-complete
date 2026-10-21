"""Complete setup-only progression with fake observations; no live host or release writes."""
from datetime import datetime, timedelta, timezone
import json

import pytest
import yaml

from orchestrator import cli, cli_common as C, infra, m365_capabilities as M, onboarding as O
from tests.test_m365_capabilities import evidence as release_evidence, state


NOW = datetime(2026, 10, 20, 23, 0, tzinfo=timezone.utc)


def inventory():
    return {
        "version": 1, "observed_at": NOW.isoformat(),
        "available_tools": {tool: tool for tool in (*M.REQUIRED_TOOLS, *O.HEALTH_TOOLS)},
    }


def report():
    return {
        "infrastructure_ok": True, "path_requests": [], "requirements": [], "mcp_servers": [],
        "m365_connectors": {},
    }


def readiness():
    return {"items": [
        {"id": "oncall_now", "team_id": 123},
        {"id": "adx_access", "cluster_uri": "https://test.kusto.windows.net", "database": "test"},
    ]}


def envelope(tool, arguments, result, account="owner@example.test"):
    return {"version": 1, "tool": tool, "arguments": arguments, "result": result,
            "account": account, "observed_at": NOW.isoformat()}


def complete():
    evidence = inventory()
    observations = release_evidence(state())["observations"]
    for key, value in observations.items():
        for row in value if key == "roster" else [value]:
            row["observed_at"] = NOW.isoformat()
    for key, request in O._health_requests(readiness(), "owner@example.test").items():
        observations[key] = envelope(
            request["tool"], request["arguments"],
            {"data": {"rows": [[1]]}} if key == "adx" else {"shiftCurrentOnCalls": []})
    evidence["observations"] = observations
    return evidence


def run(evidence=None, machine=None):
    return O.plan(machine or report(), evidence, readiness(), now=NOW)


def test_full_onboarding_without_hosted_workiq_registers_supported_mcps_and_reads():
    observed = inventory()
    machine = report()
    machine["infrastructure_ok"] = False
    machine["mcp_servers"] = [{
        "id": "icm", "transport": "command", "status": "would_register"}]
    registration = run(machine=machine)
    assert registration["action"]["kind"] == "register_mcp"
    assert registration["action"]["command"] == ["infra"]
    assert "question" not in registration["action"]
    assert run()["action"]["kind"] == "observe_tools"
    first_read = run(observed)
    assert list(first_read["action"]["requests"]) == ["profile"]
    good = complete()
    observed["observations"] = {"profile": good["observations"]["profile"]}
    next_read = run(observed)
    assert next_read["action"]["kind"] == "read"
    assert {"calendar", "chat", "members", "roster", "sharepoint", "icm", "adx"} == set(next_read["action"]["requests"])
    observed["observations"].update(good["observations"])
    done = run(observed)
    assert done["status"] == "ready" and done["action"]["kind"] == "complete"
    assert done["proof_hash"]
    assert not done["permission_to_start_release"] and not done["permission_to_write"]
    assert "No release was started or changed" in done["message"]
    assert done["routing"]["structured_fetch"] == {"kind": "mcp", "tool": "workiq.fetch"}
    assert "unavailable" not in done["message"]


def test_setup_uses_current_fetch_capability_not_stale_custom_host_failure():
    evidence = complete()
    evidence["host"] = {"connection": "unsupported", "observed_at": NOW.isoformat()}
    evidence["completed_actions"] = ["Hosted WorkIQ:reload"]
    result = run(evidence)
    assert result["status"] == "ready"
    assert "workiq.fetch" in run()["action"]["required_tools"]
    evidence["observations"] = {"profile": evidence["observations"]["profile"]}
    assert run(evidence)["action"]["requests"]["roster"]["tool"] == "workiq.fetch"


@pytest.mark.parametrize("tool,completed,kind", [
    ("workiq_get_my_profile", [], "sign_in"),
    ("workiq_get_my_profile", ["Microsoft 365:sign_in"], "support"),
    ("microsoft_teams-GetChat", [], "reconnect"),
    ("microsoft_teams-GetChat", ["Microsoft Teams:reconnect"], "support"),
    ("playwright-browser_snapshot", [], "reconnect"),
    ("icm-get_on_call_schedule_by_team_id", [], "reconnect"),
    ("kusto_-_adx-kusto_query", [], "reconnect"),
])
def test_connector_recovery_is_bounded(tool, completed, kind):
    evidence = inventory()
    evidence["available_tools"].pop(tool)
    evidence["completed_actions"] = completed
    result = run(evidence)
    assert result["action"]["kind"] == kind
    assert not result["permission_to_start_release"] and not result["permission_to_write"]
    if kind == "support":
        assert result["status"] == "blocked"
        assert "question" not in result["action"]


@pytest.mark.parametrize("key,kind,integration", [
    ("profile", "authentication", "Microsoft 365"),
    ("icm", "session", "ICM"),
    ("adx", "access_denied", "Kusto / ADX"),
    ("chat", "access_denied", "Microsoft Teams"),
    ("calendar", "unsupported", "Microsoft 365"),
])
def test_read_failures_are_actionable_and_never_marked_passed(key, kind, integration):
    evidence = complete()
    probe = evidence["observations"].pop(key)
    issue = {k: v for k, v in probe.items() if k not in ("version", "result")}
    issue["kind"] = kind
    evidence["issues"] = {key: issue}
    result = run(evidence)
    assert result["action"]["integration"] == integration
    assert result["status"] != "ready"
    if result["action"]["kind"] != "support":
        evidence["completed_actions"] = [result["action"]["action_id"]]
        assert run(evidence)["action"]["kind"] == "support"


def test_registered_connected_tools_without_reads_are_not_onboarding_success():
    evidence = inventory()
    evidence["host"] = {"remote_http": True, "connection": "connected", "observed_at": NOW.isoformat()}
    result = run(evidence)
    assert result["status"] == "checking"
    assert result["action"]["requests"]["profile"]["tool"] == "workiq_get_my_profile"


def test_real_installation_and_path_failures_precede_connector_handoff():
    machine = report()
    machine["path_requests"] = [{"flag": "--agency-path", "question": "Where is Agency?"}]
    assert run(machine=machine)["action"]["kind"] == "path"
    machine["path_requests"] = []
    machine["infrastructure_ok"] = False
    machine["requirements"] = [{"ok": False, "id": "python", "install": "Install Python"}]
    result = run(machine=machine)
    assert result["action"]["kind"] == "repair_installation"
    assert result["action"]["problems"] == machine["requirements"]
    evidence = inventory()
    evidence["completed_actions"] = ["installation:repair"]
    assert run(evidence, machine)["action"]["kind"] == "support"


@pytest.mark.parametrize("change", [
    "version", "stale", "future", "completed",
    "wrong_account", "wrong_args", "wrong_request", "wrong_adx", "empty_icm",
])
def test_forged_or_invalid_observations_do_not_complete_setup(change):
    evidence = complete()
    if change == "version":
        evidence["version"] = True
    elif change in ("stale", "future"):
        evidence["observed_at"] = (NOW + timedelta(minutes=-16 if change == "stale" else 1)).isoformat()
    elif change == "completed":
        evidence["completed_actions"] = "done"
    elif change == "wrong_account":
        evidence["observations"]["calendar"]["account"] = "other@example.test"
    elif change == "wrong_args":
        evidence["observations"]["members"]["arguments"] = {"chatId": "other"}
    elif change == "wrong_request":
        evidence["observations"]["random_read"] = {}
    elif change == "wrong_adx":
        evidence["observations"]["adx"]["result"]["data"]["rows"] = [[2]]
    else:
        evidence["observations"]["icm"]["result"] = {}
    with pytest.raises(ValueError):
        run(evidence)


def test_failure_request_identity_is_exact_and_never_trusted_from_a_summary():
    evidence = complete()
    evidence["observations"].pop("icm")
    evidence["issues"] = {"icm": {
        "kind": "session", "tool": O.HEALTH_TOOLS[0], "arguments": {"teamIds": [999]},
        "account": "owner@example.test", "observed_at": NOW.isoformat(),
    }}
    with pytest.raises(ValueError, match="exact onboarding"):
        run(evidence)


def test_onboarding_and_paths_cli_never_load_lock_discover_or_create_a_release(tmp_path, monkeypatch, capsys):
    profile = tmp_path / "custom profile"
    profile.mkdir()
    requirements = tmp_path / "requirements.yaml"
    requirements.write_text(yaml.safe_dump({
        "requirements": [], "mcp_servers": [],
        "m365_connectors": {"calendar_mail_files": "native WorkIQ", "teams": "Agency Teams"},
    }), encoding="utf-8")
    (tmp_path / "readiness.yaml").write_text(yaml.safe_dump(readiness()), encoding="utf-8")
    phases = tmp_path / "phases.yaml"
    phases.write_text("phases: []", encoding="utf-8")
    monkeypatch.setattr(C, "REQUIREMENTS_CONFIG", str(requirements))
    monkeypatch.setattr(C, "effective_release", lambda *a: pytest.fail("release discovery"))
    monkeypatch.setattr(C, "load_orch", lambda *a: pytest.fail("release load"))
    monkeypatch.setattr(C, "save_state", lambda *a: pytest.fail("release save"))
    monkeypatch.setattr(infra, "ensure_mcp_servers", lambda *a, **kw: (
        pytest.fail("host registration") if kw.get("register") or len(a) > 1 and a[1] else []))
    root = tmp_path / "runs must not exist"
    args = ["--config", str(phases), "--runs-root", str(root)]
    assert cli.main([*args, "onboarding", "--scout-root", str(profile), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["action"]["kind"] == "observe_tools"
    assert cli.main([*args, "paths", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    # Exercise successful CLI completion too, with no diagnostic exemption leaking writes.
    proof = complete()
    actual = datetime.now(timezone.utc).isoformat()
    proof["observed_at"] = actual
    for key, value in proof["observations"].items():
        for row in value if key == "roster" else [value]:
            row["observed_at"] = actual
    file = tmp_path / "private setup evidence.json"
    file.write_text(json.dumps(proof), encoding="utf-8")
    command = [*args, "onboarding", "--scout-root", str(profile), "--observation-file", str(file)]
    assert cli.main([*command, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready" and result["account"] == "owner@example.test"
    assert cli.main(command) == 0
    text = capsys.readouterr().out
    assert "Onboarding complete" in text and "No release was started" in text
    assert not root.exists() and not (profile / "m-mcp-servers.json").exists()


@pytest.mark.parametrize("payload", ["null", "{bad", "[]"])
def test_cli_bad_observation_has_explicit_hold(tmp_path, capsys, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    assert cli.main(["onboarding", "--observation-file", str(path), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked" and result["action"]["kind"] == "recollect"
    assert not result["permission_to_write"] and not result["permission_to_start_release"]
