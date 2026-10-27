"""Persisted CLI routing with fake providers; no live release or host actions."""
import json
import shlex

import pytest

from orchestrator import cli, cli_common as C, render
from orchestrator.readiness import dispatch_plan
from tests._harness import CONFIG, _orch, _pass_scout_checks, _stub_build_defs
from tests.test_m365_capabilities import evidence


RID = "2026-11"
SCOUT_IDS = {"ccd_confirmed", "m365_access", "silent_perms",
             "teams_notify", "adx_access", "oncall_now"}


def call(capsys, root, *args):
    code = cli.main(["--runs-root", str(root), *args])
    return code, capsys.readouterr().out


def start(capsys, root, monkeypatch):
    from tools import checks
    _stub_build_defs("pass")
    monkeypatch.setattr(checks, "read_pipeline_variable",
                        lambda *args, **kwargs: (True, None, "offline provider fixture"))
    code, out = call(capsys, root, "init", "--release", RID, "--json",
                     "--owner-email", "owner@example.test", "--timezone", "America/Los_Angeles")
    assert code == 0
    payload = json.loads(out)
    assert payload["readiness_dispatch"]["continue_automatically"]
    code, _ = call(capsys, root, "verify", "--release", RID, "--pending-only")
    assert code == 0
    state = C.load_state(str(root), RID)
    from tests._automation import seed_driver
    seed_driver(root, state)
    return state


@pytest.mark.parametrize("surface", [
    "checklist", "checklist-json", "checklist-verify", "attest", "attest-json",
    "status", "status-json", "next", "next-json", "resume", "verify",
])
def test_test5_python_only_state_cannot_hide_scout_queue(
        tmp_path, monkeypatch, capsys, surface):
    start(capsys, tmp_path, monkeypatch)
    command = surface.split("-")[0]
    args = [command, "--release", RID]
    if surface.endswith("-json"):
        args.append("--json")
    elif surface == "checklist-verify":
        args.append("--verify")
    if command == "attest":
        args = ["checklist", "--release", RID, "--attest-prompt"]
        if surface.endswith("-json"):
            args.append("--json")
    code, out = call(capsys, tmp_path, *args)
    assert code == 0
    if surface.endswith("-json"):
        payload = json.loads(out)
        plan = payload if command == "attest" else payload[
            "dispatch" if command == "checklist" else "readiness_dispatch"]
        assert {item["id"] for item in plan["pending_auto_checks"]} == SCOUT_IDS
        assert plan["continue_automatically"] and not plan["may_return_to_user"]
    else:
        assert "Scout must exhaust all executable readiness checks" in out
        assert "Scout queue (not owner tasks)" in out
        for iid in SCOUT_IDS:
            assert f"`{iid}`" in out
        if command != "verify":
            assert "| | Type | Item | Status |" in out
    saved = C.load_state(str(tmp_path), RID)
    assert set(saved.readiness_items) == {"build_access", "mcp_servers"}
    assert not saved.steps and not saved.notification_deliveries
    assert call(capsys, tmp_path, "checklist", "--release", RID,
                "--attest-prompt", "--handoff", "--json")[0] == 2


@pytest.mark.parametrize("outcome", ["pass", "needs_input", "fail"])
def test_persisted_host_dispatch_exhausts_every_probe_before_handoff(
        tmp_path, monkeypatch, capsys, outcome):
    start(capsys, tmp_path, monkeypatch)
    executed = []
    while True:
        code, out = call(capsys, tmp_path, "checklist", "--release", RID,
                         "--attest-prompt", "--handoff", "--json")
        payload = json.loads(out)
        if code == 0:
            break
        assert code == 2 and not payload["may_return_to_user"] and not payload["ready"]
        assert "answers" not in payload
        # Simulate the host receiving one real-shaped response at a time and
        # recording it through the actual persisted CLI, not hand-editing state.
        item = payload["pending_auto_checks"][0]
        iid, probe = item["id"], item["probe"]
        assert iid not in executed
        executed.append(iid)
        kwargs = []
        actual = outcome if iid == "ccd_confirmed" else "pass"
        if "command" in probe:
            rc, observed = call(capsys, tmp_path, *shlex.split(probe["command"]))
            assert rc == 0 and isinstance(json.loads(observed), dict)
        elif iid == "silent_perms":
            assert probe == {"tool": "m_get_settings", "arguments": {}}
            assert "workiq" in item["required_servers"]
        elif iid == "teams_notify":
            assert probe == {"tool": "m_relay_status", "arguments": {}}
        elif iid == "adx_access":
            assert probe["arguments"]["query"] == "print 1"
            assert probe["arguments"]["cluster_uri"].startswith("https://")
            assert probe["arguments"]["database"]
        elif iid == "oncall_now":
            assert probe["arguments"]["teamIds"] == [78848]
            assert item["identity_probe"]["tool"] == "icm-get_my_icm_context"
        else:
            pytest.fail(f"Unexecutable probe: {item}")
        if iid == "m365_access":
            path = tmp_path / "observations.json"
            path.write_text(json.dumps(evidence(C.load_state(str(tmp_path), RID))), encoding="utf-8")
            kwargs = ["--evidence-file", str(path)]
        code, _ = call(capsys, tmp_path, "record-check", "--release", RID,
                        "--item", iid, "--status", actual,
                        "--detail", "offline probe result; explicit date decision needed" if actual == "needs_input"
                        else "offline probe result", *kwargs)
        assert code == 0
    assert set(executed) == SCOUT_IDS and len(executed) == 6
    assert payload["may_return_to_user"] and not payload["continue_automatically"]
    assert payload["pending_auto_checks"] == []
    assert payload["next_action"] == {
        "pass": "attest", "needs_input": "resolve_auto_input", "fail": "resolve_auto_checks",
    }[outcome]
    assert payload["ready"] == (outcome == "pass")
    assert payload["input_required_auto_checks"] == (["ccd_confirmed"] if outcome == "needs_input" else [])
    saved = C.load_state(str(tmp_path), RID)
    assert not saved.steps and not saved.gate_decisions and not saved.notification_deliveries
    assert not C.load_orch(str(tmp_path), RID, CONFIG)[1].gate.signed
    assert len([line for line in payload["display_markdown"].splitlines() if line.startswith("|")]) == 14
    if outcome == "pass":
        assert set(payload["confirm_items"]) == {
            "play_console_access", "oncall_window", "saw_ame", "yubikey"}
    else:
        assert "answers" not in payload and "offline probe result" in payload["display_markdown"]


def test_input_hold_requires_evidence_and_never_satisfies_or_retries(tmp_path, monkeypatch, capsys):
    start(capsys, tmp_path, monkeypatch)
    path = tmp_path / RID / "release-state.json"
    before = path.read_bytes()
    code, out = call(capsys, tmp_path, "record-check", "--release", RID,
                    "--item", "silent_perms", "--status", "needs_input")
    assert code == 1 and "specific missing input or consent" in out
    assert path.read_bytes() == before
    code, _ = call(capsys, tmp_path, "record-check", "--release", RID,
                   "--item", "silent_perms", "--status", "needs_input",
                   "--detail", "Need consent to enable unattended approvals")
    assert code == 0
    st, orch = C.load_orch(str(tmp_path), RID, CONFIG)
    chk = orch.gate.checklist()
    assert not next(item for item in chk["items"] if item["id"] == "silent_perms")["satisfied"]
    assert "silent_perms" in chk["dispatch"]["input_required_auto_checks"]
    assert chk["dispatch"]["continue_automatically"] and not chk["dispatch"]["may_return_to_user"]
    assert "silent_perms" not in {item["id"] for item in chk["dispatch"]["pending_auto_checks"]}
    assert "Input needed" in render.readiness_table(chk, RID)
    assert not orch.gate.signed


def test_invalidated_capability_pass_is_executable_not_a_failed_probe():
    st, orch = _orch()
    st.owner_email = "changed-owner@example.test"
    chk = orch.gate.checklist()
    plan = chk["dispatch"]
    assert {item["id"] for item in plan["pending_auto_checks"]} == {"m365_access"}
    assert plan["failed_auto_checks"] == [] and not plan["may_return_to_user"]
    row = next(line for line in render.readiness_table(chk, st.release_id).splitlines()
               if "**M365 connector capabilities**" in line)
    assert "Outstanding" in row and "PASS" not in row and "prior evidence" in row


def test_verify_does_not_report_invalidated_read_proof_as_ok(tmp_path, capsys):
    st, _ = _orch()
    st.owner_email = "changed-owner@example.test"
    C.save_state(st, str(tmp_path), st.release_id)
    code, out = call(capsys, tmp_path, "verify", "--release", st.release_id, "--pending-only")
    assert code == 0 and "[PENDING] m365_access: pending" in out
    assert "[OK] m365_access" not in out


@pytest.mark.parametrize("hold", ["halt", "cancellation", "decline"])
def test_readiness_exhaustion_preserves_release_holds(hold):
    st, orch = _orch(signed=False)
    if hold == "decline":
        orch.gate.decline(["yubikey"])
    else:
        setattr(st, hold, {"reason": "owner stopped", "at": "2026-10-22T04:00:00Z"})
    report = orch.status_report()
    plan = report["readiness_dispatch"]
    assert report["readiness"]["dispatch"] == plan
    assert plan["continue_automatically"] == (hold == "decline")
    assert plan["may_return_to_user"] == (hold != "decline")
    if hold == "decline":
        assert "Scout must exhaust" in render.status_view(report)
    else:
        assert "Scout queue" not in render.status_view(report)


def test_pending_only_python_dispatch_does_not_replay_failed_verifier():
    st, orch = _orch(signed=False)
    st.readiness_items["build_access"] = {"status": "fail", "message": "access denied"}
    pending = orch.gate.checklist()["dispatch"]["pending_auto_checks"]
    assert all("--pending-only" in item["probe"]["command"]
               for item in pending if item["source"] == "python")
    orch.gate.verify(pending_only=True)
    assert st.readiness_items["build_access"] == {"status": "fail", "message": "access denied"}
    assert st.readiness_items["mcp_servers"]["status"] == "pass"


def test_no_readiness_configuration_has_no_artificial_probe_queue():
    assert dispatch_plan({"items": [], "signed": True}, RID)["may_return_to_user"]


def test_plain_handoff_refuses_pending_work_and_complete_decline_is_not_an_attestation(
        tmp_path, monkeypatch, capsys):
    start(capsys, tmp_path, monkeypatch)
    code, out = call(capsys, tmp_path, "checklist", "--release", RID, "--handoff")
    assert code == 2 and "Scout queue" in out
    st, orch = C.load_orch(str(tmp_path), RID, CONFIG)
    _pass_scout_checks(orch)
    orch.gate.decline(["yubikey"])
    C.save_state(st, str(tmp_path), RID)
    code, out = call(capsys, tmp_path, "checklist", "--release", RID,
                     "--attest-prompt", "--handoff")
    assert code == 0 and "| | Type | Item | Status |" in out
    assert "Your confirmation needed" not in out and "blocked" in out


def test_dispatch_uses_configured_coordinates_not_author_defaults():
    st, orch = _orch(signed=False)
    chk = orch.gate.checklist()
    for item in chk["items"]:
        if item["id"] == "adx_access":
            item["cluster_uri"], item["database"] = "https://other.kusto.windows.net", "custom-db"
        if item["id"] == "oncall_now":
            item["team_id"] = 123456
    plan = dispatch_plan(chk, "2099-12")
    tasks = {item["id"]: item for item in plan["pending_auto_checks"]}
    assert tasks["adx_access"]["probe"]["arguments"] == {
        "cluster_uri": "https://other.kusto.windows.net", "database": "custom-db", "query": "print 1"}
    assert tasks["oncall_now"]["probe"]["arguments"] == {"teamIds": [123456]}
    assert "2099-12" in tasks["ccd_confirmed"]["probe"]["command"]
    assert "2099-12" in tasks["m365_access"]["record_command"]


def test_workflow_review_hold_never_authorizes_readiness_dispatch(tmp_path, monkeypatch, capsys):
    from orchestrator import revision
    start(capsys, tmp_path, monkeypatch)
    monkeypatch.setattr(revision, "mismatch_reason", lambda *args: "workflow owner review required")
    code, out = call(capsys, tmp_path, "checklist", "--release", RID,
                     "--attest-prompt", "--handoff", "--json")
    payload = json.loads(out)
    assert code == 0 and payload["may_return_to_user"] and not payload["continue_automatically"]
    assert payload["next_action"] == "resolve_release_hold" and "answers" not in payload
    assert "workflow owner review required" in payload["instruction"]
