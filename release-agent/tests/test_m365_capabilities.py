"""Connector capability evidence is scoped, fresh and distinct from write permission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from orchestrator import cli, cli_common as C, m365, m365_capabilities as M
from orchestrator.state import ReleaseState
from tests._context import fresh_orchestrator
from tests._harness import CONFIG


def observation(tool, arguments, result, owner="owner@example.test"):
    return {
        "version": 1, "tool": tool, "arguments": arguments, "account": owner,
        "observed_at": datetime.now(timezone.utc).isoformat(), "result": result,
    }


def evidence(state):
    expected = M.plan(state)
    target = M.targets()
    results = {
        "profile": {"id": "owner-guid", "userPrincipalName": state.owner_email},
        "calendar": {"events": []},
        "chat": {"id": target["representative_chat"], "chatType": "meeting"},
        "members": [{"userId": "owner-guid", "displayName": "Owner"}],
        "sharepoint": {"id": "site-guid", "webUrl": target["report_site"]},
    }
    probes = {
        key: observation(item["tool"], item["arguments"], results[key], state.owner_email)
        for key, item in expected["requests"].items() if key != "roster"
    }
    from tests.test_distribution_live import roster_bundle
    probes["roster"] = roster_bundle(
        [{"name": "Member", "upn": "member@example.test"}],
        group_mail=target["roster_group"], owner=state.owner_email)["observations"]
    probes["report_library"] = observation("workiq.fetch", {"entityUrls": ["/sites/site-guid/drives"]},
                                         {"value": [{"id": "drive-guid", "name": target["report_library"]}]},
                                         state.owner_email)
    return {
        "binding": expected["binding"], "available_tools": {tool: tool for tool in M.REQUIRED_TOOLS},
        "observations": probes,
    }


def state():
    return ReleaseState(release_id="2026-10", ccd="2026-10-21", owner_email="owner@example.test")


def test_structured_observation_rejects_wrong_account_tool_args_and_time():
    now = datetime.now(timezone.utc)
    good = observation("workiq_get_my_profile", {}, {"id": "one"})
    good["observed_at"] = now.isoformat()
    assert m365.validate_observation(good, tool=good["tool"], arguments={}, owner=good["account"], now=now) == {"id": "one"}
    assert m365.profile_matches({"id": "one", "userPrincipalName": "short@example.test",
                                 "mail": "long.name@example.test"}, "short@example.test")
    assert m365.profile_matches({"id": "one", "userPrincipalName": "short@example.test",
                                 "mail": "long.name@example.test"}, "long.name@example.test")
    for key, value in (
        ("account", "other@example.test"), ("tool", "ask"), ("arguments", {"different": True}),
        ("version", True), ("observed_at", (now - timedelta(minutes=16)).isoformat()),
        ("observed_at", (now + timedelta(minutes=1)).isoformat()),
        ("observed_at", "2026-10-17T00:00:00"), ("result", {"success": False}),
    ):
        changed = {**good, key: value}
        with pytest.raises(ValueError):
            m365.validate_observation(changed, tool=good["tool"], arguments={}, owner=good["account"], now=now)


def test_capability_plan_is_read_only_and_uses_correct_tools(tmp_path, capsys):
    st = state()
    C.save_state(st, str(tmp_path), st.release_id)
    path = tmp_path / st.release_id / "release-state.json"
    before = path.read_bytes()
    assert cli.main(["--runs-root", str(tmp_path), "m365-capabilities", "--release", st.release_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert not out["permission_to_send"] and not out["permission_to_upload"]
    assert "workiq.fetch" in out["required_tools"]
    assert set(out["requests"]) == {"profile", "calendar", "chat", "members", "roster", "sharepoint"}
    assert "unavailable_checks" not in out
    assert out["requests"]["members"]["tool"] == "microsoft_teams-ListChatMembers"
    assert "Azure" not in json.dumps(out["requests"])
    assert path.read_bytes() == before


def test_readiness_rejects_bare_capability_pass_and_preserves_state():
    st = state()
    orch = fresh_orchestrator(CONFIG, st, mocks={})
    before = deepcopy(st.readiness_items)
    assert "error" in orch.gate.record_check("m365_access", "pass", "tools look present")
    assert st.readiness_items == before
    assert "error" in orch.gate.record_check("m365_access", "degraded", "ignore missing connector")
    assert "error" in orch.gate.record_check("m365_access", "pass", evidence={
        "binding": M.binding(st), "available_tools": {tool: tool for tool in M.REQUIRED_TOOLS}})


def test_fresh_capabilities_store_read_proof_without_blanket_write_attestation():
    st = state()
    orch = fresh_orchestrator(CONFIG, st, mocks={})
    proof = evidence(st)
    result = orch.gate.record_check("m365_access", "pass", "actual read probes", evidence=proof)
    assert "error" not in result
    stored = st.readiness_items["m365_access"]
    assert stored["capability_binding"] == M.binding(st)
    assert "observations" not in stored and "owner-guid" not in json.dumps(stored)
    items = {item["id"]: item for item in result["items"]}
    assert items["m365_access"]["satisfied"]
    assert "m365_write_access" not in items
    assert {item["id"] for item in result["attest_items"]} == {
        "play_console_access", "oncall_window", "saw_ame", "yubikey"}
    assert "do not prove permission" in stored["limitations"]
    assert "at each owning write step" in stored["limitations"]
    assert not M.plan(st)["permission_to_send"] and not M.plan(st)["permission_to_upload"]
    assert {check["name"] for check in stored["checks"]} == {
        "owner/calendar", "Teams chat/members", "distribution roster", "SharePoint library"}
    assert stored["structured_fetch"] == {"kind": "mcp", "tool": "workiq.fetch"}


def test_capabilities_reject_missing_tools_partial_wrong_targets_and_unreadable_results():
    st = state()
    good = evidence(st)
    changes = (
        ("missing_tool", None), ("wrong_alias", None), ("wrong_account", None),
        ("partial_members", None), ("wrong_member", None), ("wrong_chat", None),
        ("failed_read", None),
        ("missing_roster", None), ("wrong_group", None), ("missing_library", None), ("wrong_site", None),
    )
    for change, _ in changes:
        value = deepcopy(good)
        probes = value["observations"]
        if change == "missing_tool":
            value["available_tools"].pop("workiq_get_my_profile")
        elif change == "wrong_alias":
            value["available_tools"]["workiq_get_my_profile"] = "workiq_search_people"
        elif change == "wrong_account":
            probes["profile"]["account"] = "other@example.test"
        elif change == "partial_members":
            probes["members"]["result"] = {"members": [], "hasMoreResults": True}
        elif change == "wrong_member":
            probes["members"]["result"] = [{"userId": "another-guid"}]
        elif change == "wrong_chat":
            probes["chat"]["result"]["id"] = "other-chat"
        elif change == "missing_roster":
            probes.pop("roster")
        elif change == "wrong_group":
            probes["roster"][0]["result"]["value"][0]["mail"] = "other@example.test"
        elif change == "missing_library":
            probes["report_library"]["result"]["value"] = []
        elif change == "wrong_site":
            probes["sharepoint"]["result"]["webUrl"] = "https://example.test/other"
        else:
            probes["calendar"]["result"] = {"error": "403 Forbidden"}
        with pytest.raises(ValueError):
            M.validate(st, value)


def test_owner_ccd_and_target_changes_invalidate_capability_reads(monkeypatch):
    st = state()
    orch = fresh_orchestrator(CONFIG, st, mocks={})
    assert "error" not in orch.gate.record_check("m365_access", "pass", evidence=evidence(st))
    for field, value in (("owner_email", "other@example.test"), ("ccd", "2026-10-22")):
        old = getattr(st, field)
        setattr(st, field, value)
        items = {item["id"]: item for item in orch.gate.checklist()["items"]}
        assert not items["m365_access"]["satisfied"]
        setattr(st, field, old)
    old = M.targets()
    monkeypatch.setattr(M, "targets", lambda: {**old, "report_folder": "changed-folder"})
    assert not next(item for item in orch.gate.checklist()["items"] if item["id"] == "m365_access")["satisfied"]


def test_registered_but_disabled_or_toolless_mcp_is_not_ready(tmp_path, monkeypatch):
    from orchestrator import infra
    path = tmp_path / "m-mcp-servers.json"
    monkeypatch.setattr(infra, "scout_mcp_config_path", lambda: str(path))
    req = {"mcp_servers": [{"id": "teams", "tools": ["GetChat", "ListChatMembers"]}]}
    for entry in ({"tools": []}, {"tools": ["GetChat"]}, {"tools": [{}]},
                  {"enabled": False, "tools": ["GetChat", "ListChatMembers"]}):
        path.write_text(json.dumps({"servers": {"teams": entry}}), encoding="utf-8")
        assert infra.ensure_mcp_servers(req, register=False)[0]["status"] == "capabilities_missing"
