"""Portable native fetch discovery and exact read handoffs, without live writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import cli, cli_common as C, infra, m365_capabilities as M, structured_fetch as F
from tests.test_m365_capabilities import evidence, state
from tests.test_onboarding import complete, readiness, report, NOW


def process(stdout="", code=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=code)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    profile = tmp_path / "another user's Scout profile"
    binary = profile / "bin" / "workiq.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"test executable")
    monkeypatch.setattr(F.shutil, "which", lambda name: None)
    monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: process("fetch --urls"))
    return profile, binary


def test_discovery_is_user_profile_scoped_and_does_not_touch_connections(launcher):
    profile, binary = launcher
    result = F.discover_cli(str(profile))
    assert result["command"] == [str(binary)]
    assert result["scout_root"] == str(profile)
    assert not (profile / "m-mcp-servers.json").exists()


def test_explicit_missing_path_does_not_silently_fallback(launcher):
    profile, _ = launcher
    assert F.discover_cli(str(profile), str(profile / "missing.exe"))["status"] == "missing"


def test_ask_only_or_failed_help_is_not_structured_fetch(launcher, monkeypatch):
    profile, _ = launcher
    for output in (process("ask -q"), process("fetch --urls", 1)):
        monkeypatch.setattr(F.subprocess, "run", lambda *a, **k: output)
        result = F.discover_cli(str(profile))
        assert result["status"] == "unsupported"
        assert F.route({}, result) is None


def test_missing_cli_and_mcp_requires_discovery_not_a_global_impossibility(launcher):
    profile, binary = launcher
    binary.unlink()
    result = F.discover_cli(str(profile))
    assert result["status"] == "missing"
    assert "do not infer" in result["detail"]
    from orchestrator import onboarding
    proof = complete()
    proof["available_tools"].pop(F.FETCH_TOOL)
    machine = report()
    machine["m365_connectors"]["structured_fetch"] = result
    held = onboarding.plan(machine, proof, readiness(), now=NOW)
    assert held["status"] == "blocked"
    assert "WorkIQ structured fetch" == held["action"]["integration"]


@pytest.mark.parametrize("alias", sorted(F.FETCH_ALIASES))
def test_actual_mcp_alias_needs_no_cli_or_custom_registration(alias):
    assert F.route({F.FETCH_TOOL: alias}) == {"kind": "mcp", "tool": alias}


def test_discovery_preserves_scout_wrapper_and_never_uses_cmd_shell(launcher, monkeypatch):
    profile, binary = launcher
    components = [profile / "app with spaces" / name for name in
                  ("node-runner.exe", "node.exe", "workiq-wrapper.cjs")]
    for part in components:
        part.parent.mkdir(exist_ok=True)
        part.write_bytes(b"test component")
    wrapper = profile / "bin" / "workiq.cmd"
    parts = [*components, binary]
    wrapper.write_text('@echo off\nset "NODE_OPTIONS="\n' +
                       " ".join(f'"{part}"' for part in parts) + " %*\n", encoding="utf-8")
    calls = []
    def run(argv, **options):
        calls.append((argv, options))
        return process("fetch --urls") if "--help" in argv else process('{"value":[]}')
    monkeypatch.setattr(F.subprocess, "run", run)
    url = "/groups?$filter=mail%20eq%20%27o%27%27brien%40example.test%27&$top=100"
    result = F.read(str(profile), "owner@example.test", url)
    assert result["result"] == {"value": []}
    assert result["arguments"] == {"entityUrls": [url]}
    assert calls[-1][0] == [*(str(part) for part in parts), "fetch", "--urls", url,
                           "--account", "owner@example.test"]
    assert all(options["shell"] is False for _, options in calls)
    assert result["structured_fetch"]["command"] == [str(part) for part in parts]


def test_unknown_wrapper_is_not_bypassed_with_an_embedded_binary(launcher):
    profile, _ = launcher
    (profile / "bin" / "workiq.cmd").write_text('@echo off\ncustom-loader %*\n', encoding="utf-8")
    assert F.discover_cli(str(profile))["status"] == "unsupported"


@pytest.mark.parametrize("payload,code", [('{"error":"AccessDenied"}', 0), ("null", 0),
                                         ("a semantic summary", 0), ("", 1)])
def test_failed_or_unstructured_fetch_never_becomes_a_success_envelope(launcher, monkeypatch, payload, code):
    profile, _ = launcher
    def run(argv, **kwargs):
        return process("fetch --urls") if "--help" in argv else process(payload, code)
    monkeypatch.setattr(F.subprocess, "run", run)
    with pytest.raises(ValueError):
        F.read(str(profile), "owner@example.test", "/groups")


def test_native_route_replaces_only_fetch_capability_not_other_required_tools(launcher):
    profile, _ = launcher
    discovered = F.discover_cli(str(profile))
    proof = evidence(state())
    proof["available_tools"].pop(F.FETCH_TOOL)
    assert F.FETCH_TOOL in M.missing_tools(proof["available_tools"])
    proof["structured_fetch"] = discovered
    assert not M.missing_tools(proof["available_tools"], structured_fetch=discovered)
    assert M.validate(state(), proof)["structured_fetch"] == discovered
    proof["available_tools"].pop("workiq_list_events")
    assert M.missing_tools(proof["available_tools"], structured_fetch=discovered) == ["workiq_list_events"]
    changed = {**discovered, "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()}
    with pytest.raises(ValueError, match="stale"):
        M.missing_tools(proof["available_tools"], structured_fetch=changed)


def test_library_followups_follow_only_actual_same_site_pages():
    proof = evidence(state())["observations"]
    first = proof["report_library"]
    link = "/sites/site-guid/drives?$skiptoken=next"
    first["result"]["@odata.nextLink"] = "https://graph.microsoft.com/v1.0" + link
    result = M.read_plan("owner@example.test", proof)
    assert result["requests"]["report_library_next"]["arguments"] == {"entityUrls": [link]}
    tail = deepcopy(first)
    tail["arguments"] = {"entityUrls": [link]}
    tail["result"] = {"value": []}
    proof["report_library"] = [first, tail]
    assert len(M.validate_reads("owner@example.test", proof)["checks"]) == 4
    first["result"]["@odata.nextLink"] = "/sites/wrong/drives?$skiptoken=next"
    with pytest.raises(ValueError, match="changed site"):
        M.read_plan("owner@example.test", proof)


def test_roster_followup_uses_returned_group_id_and_never_changes_target():
    proof = evidence(state())["observations"]
    member = proof["roster"].pop()
    request = M.read_plan("owner@example.test", proof)["requests"]["roster_next"]
    assert request["arguments"] == member["arguments"]
    proof["roster"].append(member)
    assert "roster_next" not in M.read_plan("owner@example.test", proof)["requests"]


def test_read_cli_never_discovers_loads_or_changes_release(launcher, monkeypatch, capsys):
    profile, _ = launcher
    monkeypatch.setattr(C, "effective_release", lambda *a: pytest.fail("release discovery"))
    monkeypatch.setattr(C, "load_orch", lambda *a: pytest.fail("release loaded"))
    monkeypatch.setattr(F.subprocess, "run", lambda argv, **kw: (
        process("fetch --urls") if "--help" in argv else process('{"value":[]}')))
    root = profile / "no release runs"
    assert cli.main(["--runs-root", str(root), "workiq-read", "--scout-root", str(profile),
                     "--account", "owner@example.test", "--entity-url", "/groups"]) == 0
    assert json.loads(capsys.readouterr().out)["tool"] == F.FETCH_TOOL
    assert not root.exists()


def test_record_routing_requires_verified_setup_and_contains_no_roster(launcher, monkeypatch, capsys):
    profile, _ = launcher
    machine = report()
    machine["scout_root"] = str(profile)
    monkeypatch.setattr(infra, "run", lambda *a, **kw: machine)
    monkeypatch.setattr(infra, "load_requirements", lambda *a: readiness())
    proof = complete()
    actual = datetime.now(timezone.utc).isoformat()
    proof["observed_at"] = actual
    for key, value in proof["observations"].items():
        for row in value if key in ("roster",) else [value]:
            row["observed_at"] = actual
    path = profile / "evidence.json"
    path.write_text(json.dumps(proof), encoding="utf-8")
    args = ["onboarding", "--scout-root", str(profile), "--observation-file", str(path),
            "--record-routing", "--json"]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    saved = json.loads(Path(result["routing_file"]).read_text())
    assert saved["account"] == "owner@example.test"
    assert saved["canonical_tool"] == F.FETCH_TOOL
    assert "observations" not in saved and "member@example.test" not in json.dumps(saved)
    (profile / "release-agent-routing.json").unlink()
    proof["observations"].pop("roster")
    path.write_text(json.dumps(proof), encoding="utf-8")
    assert cli.main(args) == 1
    capsys.readouterr()
    assert not (profile / "release-agent-routing.json").exists()
