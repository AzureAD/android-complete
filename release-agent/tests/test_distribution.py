"""Owner-supplied Bug Bash availability: pure selection, preview, CLI and engine."""
from copy import deepcopy

import pytest

from orchestrator import cli_common as C
from orchestrator.cli import build_parser
from orchestrator.commands import distribute as command
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import Blocked, Done
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import distribute_tests as step
from steps.lib import mockctx
from tools import distribution as D


@pytest.fixture
def inputs():
    return {
        "roster": [
            {"name": "Alice", "upn": "ALICE@example.com"},
            {"name": "Bob", "upn": "bob@example.com"},
            {"name": "Charlie", "upn": "charlie@example.com"},
            {"name": "Owner", "upn": "owner@example.com"},
            {"name": "OCE", "upn": "oce@example.com"},
            {"name": "Always excluded", "upn": "moghosh@microsoft.com"},
            {"name": "Jia Le He", "upn": "JIALH@microsoft.com"},
            {"name": "Veena Soman", "upn": "veenasoman@microsoft.com"},
        ],
        "oce": "oce@example.com",
        "broker_cases": [{"id": str(i), "assignee": "ALICE@example.com"} for i in range(1, 8)],
        "auth_cases": [{"id": "8", "assignee": "owner@example.com"},
                       {"id": "9", "assignee": "oce@example.com"}],
        "auth_automated": [],
    }


@pytest.fixture
def state():
    return ReleaseState(release_id="test-oof", owner_email="owner@example.com",
                        readiness_signed=True)


def _build(state, inputs, **kwargs):
    with mockctx.active(inputs):
        return step.build(state, **kwargs)


def _data(state):
    return state.get_step("bug_bash", step.ID).data


def _cli(monkeypatch, state, inputs, *flags):
    saved = []
    monkeypatch.setattr(C, "load_state", lambda *_: state)
    monkeypatch.setattr(C, "save_state", lambda st, *_: saved.append(deepcopy(st)))
    monkeypatch.setattr(C, "emit", lambda *_, **__: None)
    monkeypatch.setattr(C, "load_orch",
                        lambda *_: (state, Orchestrator(C.DEFAULT_CONFIG, state, mocks={})))
    monkeypatch.setattr(command.mocks_mod, "load_mocks",
                        lambda: {"bug_bash.distribute_tests": inputs})
    monkeypatch.setattr(step, "sync_plan_testers", lambda *args: (True, ""))
    args = build_parser().parse_args(["distribute-tests", "--release", state.release_id, *flags])
    return command.cmd_distribute_tests(args), saved


def test_no_confirmation_blocks_before_case_reads_or_distribution(state, inputs, monkeypatch):
    def unexpected(*_, **__):
        pytest.fail("A case read or distribution occurred before owner input")

    monkeypatch.setattr(D, "broker_manual_cases", unexpected)
    monkeypatch.setattr(D, "auth_bugbash_cases", unexpected)
    monkeypatch.setattr(D, "distribute", unexpected)
    out = _build(state, {"roster": inputs["roster"], "oce": inputs["oce"]})
    assert isinstance(out, Blocked)
    assert "Is anyone OOF for this Bug Bash?" in out.reason
    assert "--no-oof" in out.reason
    assert _data(state)["oof_candidates"] == [
        {"name": name, "upn": f"{name.lower()}@example.com"} for name in ("Alice", "Bob", "Charlie")]
    assert "oof" not in _data(state) and "plan" not in _data(state)
    # An undocumented mock key is not an alternate production confirmation source.
    out = _build(state, {**inputs, "oof": [], "oof_confirmed": True})
    assert isinstance(out, Blocked) and "plan" not in _data(state)


def test_explicit_nobody_and_repeated_preview_reuse_confirmation(state, inputs):
    assert isinstance(_build(state, inputs, oof=[]), Done)
    confirmed = deepcopy(_data(state)["oof"])
    plan = deepcopy(_data(state)["plan"])
    assert confirmed["upns"] == []
    assert confirmed["confirmed_by"] == state.owner_email
    assert confirmed["source"] == "release-owner"
    assert confirmed["confirmed_at"] and confirmed["release_id"] == state.release_id
    assert plan["counts"] == dict.fromkeys(["alice@example.com", "bob@example.com",
                                          "charlie@example.com"], 3)
    assert isinstance(_build(state, inputs), Done)
    assert _data(state)["oof"] == confirmed
    assert _data(state)["plan"] == plan
    assert plan["review_inputs"]["oof"] == confirmed


def test_oof_rebalances_default_assignments_and_preserves_other_exclusions(state, inputs):
    out = _build(state, inputs, oof=[" Alice ", "ALICE@EXAMPLE.COM", "alice@example.com"])
    assert isinstance(out, Done)
    plan = _data(state)["plan"]
    assert _data(state)["oof"]["upns"] == ["alice@example.com"]
    assert sorted(plan["counts"].values()) == [4, 5]
    assert set(plan["assignments"].values()) == {"bob@example.com", "charlie@example.com"}
    assert plan["oof_excluded"] == [{"name": "Alice", "upn": "alice@example.com"}]
    assert "Alice <alice@example.com>" in out.note
    assert plan["owner_excluded"] == state.owner_email
    assert plan["oce_excluded"] == inputs["oce"]
    assert "moghosh@microsoft.com" not in plan["eligible"]


@pytest.mark.parametrize("selection", [["unknown@example.com"], ["Ali"], [""], [" "]])
def test_invalid_replacement_clears_old_confirmation_and_plan(state, inputs, selection):
    _build(state, inputs, oof=[])
    out = _build(state, inputs, oof=selection)
    assert isinstance(out, Blocked)
    assert "plan" not in _data(state) and "oof" not in _data(state)
    assert state.get_step("bug_bash", step.ID).status == "blocked"
    assert isinstance(_build(state, inputs), Blocked)  # no fallback to the previous answer


def test_duplicate_names_require_verified_upn_and_roster_is_deterministic(inputs):
    roster = inputs["roster"] + [{"name": "Alice", "upn": "another@example.com"},
                                {"name": "Bob", "upn": "BOB@EXAMPLE.COM"}]
    with pytest.raises(ValueError, match="Ambiguous"):
        D.resolve_oof(["Alice"], roster)
    assert D.resolve_oof(["ALICE@example.com", " Bob ", "BOB@example.com"], roster) == [
        "alice@example.com", "bob@example.com"]
    assert D.canonical_roster(list(reversed(roster))) == D.canonical_roster(roster)
    eligible = D.eligible_testers([" A@X ", "a@x", "B@X"], [], oof=["b@x"])
    assert eligible == ["a@x"]


def test_replacement_nobody_reincludes_previous_oof_and_persists_oce(state, inputs):
    _build(state, inputs, oof=["Alice"], oce="bob@example.com")
    assert _data(state)["plan"]["eligible"] == ["charlie@example.com", "oce@example.com"]
    _build(state, inputs, oof=[])
    assert "alice@example.com" in _data(state)["plan"]["eligible"]
    assert "bob@example.com" not in _data(state)["plan"]["eligible"]
    assert _data(state)["plan"]["oof_excluded"] == []


@pytest.mark.parametrize("failure", ["no_testers", "case_read", "roster_read", "injected"])
def test_failed_rebuild_never_leaves_previous_plan(state, inputs, monkeypatch, failure):
    _build(state, inputs, oof=[])
    updated = deepcopy(inputs)
    selection = ["Alice"]
    if failure == "no_testers":
        selection = ["Alice", "Bob", "Charlie"]
    elif failure == "case_read":
        updated.pop("auth_cases")
        monkeypatch.setattr(D, "auth_bugbash_cases", lambda *_: (False, None, "unavailable"))
    elif failure == "roster_read":
        updated.pop("roster")
        monkeypatch.setattr(D, "resolve_roster", lambda *_: (False, None, "unavailable"))
    else:
        updated["fail"] = "forced failure"
    assert isinstance(_build(state, updated, oof=selection), Blocked)
    assert "plan" not in _data(state)


def test_cli_returns_candidates_then_records_and_prints_owner_choice(state, inputs, monkeypatch, capsys):
    import json

    result, saved = _cli(monkeypatch, state, inputs, "--json")
    assert result == 1 and len(saved) == 1
    candidates = json.loads(capsys.readouterr().out)["candidates"]
    assert [m["upn"] for m in candidates] == ["alice@example.com", "bob@example.com", "charlie@example.com"]
    result, saved = _cli(monkeypatch, state, inputs, "--oof", "Alice", "--oof", "alice@example.com")
    assert result == 0 and len(saved) == 1
    assert "Alice <alice@example.com>" in capsys.readouterr().out
    assert _data(saved[-1])["oof"]["upns"] == ["alice@example.com"]


@pytest.mark.parametrize("oce", [None, "", "OCE", "a@@example.com", "@example.com", "a@", "a b@example.com"])
def test_unresolved_oce_hides_candidates_and_blocks_before_roster_reads(state, inputs, monkeypatch, oce):
    def unexpected(*args, **kwargs):
        pytest.fail("Roster or test data read before the on-call identity was resolved")

    monkeypatch.setattr(D, "resolve_roster", unexpected)
    _build(state, inputs)
    assert _data(state)["oof_candidates"]
    record = state.get_step("bug_bash", step.ID)
    record.data["oce"] = oce
    state.set_step("bug_bash", step.ID, record)
    out = _build(state, {})
    assert isinstance(out, Blocked) and "--oce <verified-upn>" in out.reason
    assert not _data(state).get("oof_candidates") and "plan" not in _data(state)


def test_owner_is_required_before_displaying_candidates(state, inputs):
    state.owner_email = None
    out = _build(state, inputs)
    assert isinstance(out, Blocked) and "release owner missing" in out.reason
    assert not _data(state).get("oof_candidates")


def test_changed_oce_refilters_candidates_without_changing_the_roster(state, inputs):
    original = deepcopy(inputs["roster"])
    _build(state, inputs, oce=" BOB@EXAMPLE.COM ")
    assert [m["upn"] for m in _data(state)["oof_candidates"]] == [
        "alice@example.com", "charlie@example.com", "oce@example.com"]
    _build(state, inputs)
    assert "bob@example.com" not in {m["upn"] for m in _data(state)["oof_candidates"]}
    assert inputs["roster"] == original
    assert {"jialh@microsoft.com", "moghosh@microsoft.com", "veenasoman@microsoft.com"} <= set(
        D.load_config()["always_excluded"])
    assert "shjameel@microsoft.com" not in D.load_config()["always_excluded"]


def test_blocked_cases_remain_owner_triage_not_silently_excluded(state, inputs):
    inputs["auth_cases"] += [
        {"id": "10", "assignee": "oce@example.com", "tags": ["Blocked"]},
        {"id": "11", "assignee": "former@example.com", "tags": ["blocked"]},
        {"id": "12", "assignee": "former@example.com", "tags": ["Automated"]},
    ]
    inputs["auth_automated"] = [8]
    inputs["auth_failed"] = [8]
    assert isinstance(_build(state, inputs, oof=[]), Done)
    plan = _data(state)["plan"]
    assert set(plan["owner_triage"]) == {"A:8", "A:10", "A:11"}
    assert plan["owner_triage"]["A:8"]["reasons"] == ["failed_automation"]
    assert all(v["assignee"] == state.owner_email for v in plan["owner_triage"].values())
    assert "A:12" in plan["assignments"]  # Shared tag alone does not prove Android automation.
    assert not set(plan["assignments"]) & set(plan["owner_triage"])
    with mockctx.active(inputs):
        step.validate_stored_plan(state)
    inputs["auth_cases"][-3]["tags"] = []
    with mockctx.active(inputs), pytest.raises(ValueError, match="triage changed"):
        step.validate_stored_plan(state)


def test_apply_includes_triage_and_requires_tester_alignment(state, inputs, monkeypatch):
    inputs["auth_cases"].append({"id": "10", "tags": ["Blocked"], "assignee": "former@example.com"})
    writes = []
    monkeypatch.setattr(D, "set_assigned_to", lambda cid, upn: (writes.append((cid, upn)) is None, ""))
    assert _cli(monkeypatch, state, inputs, "--no-oof")[0] == 0
    captured = []
    monkeypatch.setattr(C, "load_state", lambda *_: state)
    monkeypatch.setattr(C, "save_state", lambda *_: None)
    monkeypatch.setattr(step, "sync_plan_testers",
                        lambda st, assignments: (captured.append(assignments.copy()) is None and False,
                                                 "tester write failed"))
    args = build_parser().parse_args(["distribute-tests", "--release", state.release_id, "--apply"])
    assert command.cmd_distribute_tests(args) == 2
    assert ("10", state.owner_email) in writes
    assert captured[0]["A:10"] == state.owner_email
    assert not _data(state)["plan"]["applied"]


def test_sync_point_testers_preserves_outcomes_and_unselected_testers(monkeypatch):
    from tools import pipelines as P
    pts = [
        {"id": 1, "testCase": {"id": 10}, "configuration": {"id": 84},
         "outcome": "Failed", "state": "NotReady", "assignedTo": {"id": "old"}},
        {"id": 2, "testCase": {"id": 11}, "configuration": {"id": 84},
         "outcome": "Passed", "state": "Completed", "assignedTo": {"id": "unchanged"}},
    ]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(pts), ""))
    monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, {"value": [
        {"id": 10, "fields": {"System.AssignedTo": {"id": "owner-id", "uniqueName": "owner@example.com"}}}]}, ""))
    calls = []
    def send(url, method, body, timeout):
        calls.append(body)
        pts[0]["assignedTo"] = {"id": body["tester"]["id"]}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    assert D.sync_point_testers(714514, 901, {10: "owner@example.com"}) == (True, "")
    assert calls == [{"tester": {"id": "owner-id"}}]
    assert pts[0]["outcome"] == "Failed" and pts[1]["assignedTo"]["id"] == "unchanged"


def test_auth_reader_retains_blocked_and_shared_automated_tags_for_step_classification(monkeypatch):
    monkeypatch.setattr(D, "_wiql_ids", lambda *a: (True, ["10", "11"], ""))
    monkeypatch.setattr(D, "_tags_of", lambda *a: (True, {
        "10": {"tags": ["Blocked"], "assignee": "former@example.com"},
        "11": {"tags": ["Automated"], "assignee": "alice@example.com"},
    }, ""))
    ok, cases, detail = D.auth_bugbash_cases()
    assert ok and not detail
    assert cases == [
        {"id": "10", "tags": ["Blocked"], "assignee": "former@example.com"},
        {"id": "11", "tags": ["Automated"], "assignee": "alice@example.com"},
    ]
    monkeypatch.setattr(D, "_tags_of", lambda *a: (True, {}, ""))
    assert not D.auth_bugbash_cases()[0]


def test_cli_selection_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["distribute-tests", "--release", "test",
                                  "--oof", "a@x", "--no-oof"])


@pytest.mark.parametrize("flags", [("--no-oof",), ("--oof", "Alice"), ("--oce", "bob@example.com")])
def test_apply_cannot_combine_changed_inputs_with_write(state, inputs, monkeypatch, flags):
    _build(state, inputs, oof=[])
    before = deepcopy(_data(state))
    result, saved = _cli(monkeypatch, state, inputs, "--apply", *flags)
    assert result == 1 and saved == [] and _data(state) == before


@pytest.mark.parametrize("change", [
    "missing_confirmation", "legacy_plan", "selection", "confirmation_time", "invalid_upn",
    "wrong_owner", "wrong_source", "wrong_release", "missing_time", "removed_member",
    "added_member", "exclusion", "excluded_assignment", "unknown_assignment", "oce",
])
def test_apply_refuses_missing_invalid_or_stale_inputs_before_any_writes(
        state, inputs, monkeypatch, change):
    _build(state, inputs, oof=["Alice"])
    record = state.get_step("bug_bash", step.ID)
    confirmation, plan = record.data["oof"], record.data["plan"]
    cfg = D.load_config()
    if change == "missing_confirmation":
        record.data.pop("oof")
    elif change == "legacy_plan":
        plan.pop("review_inputs")
    elif change == "selection":
        confirmation["upns"] = []
    elif change == "confirmation_time":
        confirmation["confirmed_at"] = "2026-09-01T12:00:00+00:00"
    elif change == "invalid_upn":
        confirmation["upns"] = ["unknown@example.com"]
    elif change == "wrong_owner":
        state.owner_email = "different@example.com"
    elif change == "wrong_source":
        confirmation["source"] = "calendar"
    elif change == "wrong_release":
        confirmation["release_id"] = "other-release"
    elif change == "missing_time":
        confirmation.pop("confirmed_at")
    elif change == "removed_member":
        inputs["roster"] = inputs["roster"][1:]
    elif change == "added_member":
        inputs["roster"].append({"name": "New", "upn": "new@example.com"})
    elif change == "exclusion":
        cfg["always_excluded"].append("charlie@example.com")
        monkeypatch.setattr(D, "load_config", lambda: cfg)
    elif change == "excluded_assignment":
        plan["assignments"]["A:9"] = "alice@example.com"
    elif change == "unknown_assignment":
        plan["assignments"]["A:9"] = "outsider@example.com"
    else:
        record.data["oce"] = "charlie@example.com"
    state.set_step("bug_bash", step.ID, record)
    # Autouse network guard makes even a single real set_assigned_to call fail loudly.
    result, saved = _cli(monkeypatch, state, inputs, "--apply")
    assert result == 1 and len(saved) == 1
    assert "plan" not in _data(saved[-1])


def test_apply_success_revalidation_and_replacement_never_auto_writes(state, inputs, monkeypatch):
    writes = []
    monkeypatch.setattr(D, "set_assigned_to",
                        lambda cid, upn: (writes.append((cid, upn)) is None, ""))
    _cli(monkeypatch, state, inputs, "--oof", "Alice")
    assert writes == []
    assert _cli(monkeypatch, state, inputs, "--apply")[0] == 0
    assert len(writes) == 9 and _data(state)["plan"]["applied"]
    assert "alice@example.com" not in {upn for _, upn in writes}
    assert _cli(monkeypatch, state, inputs, "--apply")[0] == 0
    assert len(writes) == 9  # idempotent apply
    assert _cli(monkeypatch, state, inputs, "--no-oof")[0] == 0
    assert len(writes) == 9 and not _data(state)["plan"]["applied"]
    assert _cli(monkeypatch, state, inputs, "--apply")[0] == 0
    assert len(writes) == 18
    assert "alice@example.com" in {upn for _, upn in writes[9:]}


def test_cli_invalidates_plan_on_unexpected_preview_exception(state, inputs, monkeypatch):
    _build(state, inputs, oof=[])

    def fail(*_, **__):
        raise RuntimeError("unexpected gather error")

    monkeypatch.setattr(D, "distribute", fail)
    saved = []
    monkeypatch.setattr(C, "load_state", lambda *_: state)
    monkeypatch.setattr(C, "save_state", lambda st, *_: saved.append(deepcopy(st)))
    monkeypatch.setattr(command.mocks_mod, "load_mocks",
                        lambda: {"bug_bash.distribute_tests": inputs})
    args = build_parser().parse_args(["distribute-tests", "--release", state.release_id, "--no-oof"])
    with pytest.raises(RuntimeError, match="gather error"):
        command.cmd_distribute_tests(args)
    assert "plan" not in _data(saved[-1])


def test_legacy_done_or_applied_status_cannot_bypass_oof_gate(state, inputs, monkeypatch):
    _build(state, inputs, oof=[])
    record = state.get_step("bug_bash", step.ID)
    record.status = "done"
    record.data["plan"]["applied"] = True
    record.data.pop("oof")
    state.set_step("bug_bash", step.ID, record)
    assert _cli(monkeypatch, state, inputs, "--apply")[0] == 1
    assert not state.is_done("bug_bash", step.ID) and "plan" not in _data(state)


def test_cli_confirmation_and_preview_survive_reload(state, inputs, monkeypatch, tmp_path):
    from orchestrator.cli import main

    monkeypatch.setattr(command.mocks_mod, "load_mocks",
                        lambda: {"bug_bash.distribute_tests": inputs})
    root = str(tmp_path)
    C.save_state(state, root, state.release_id)
    argv = ["--runs-root", root, "distribute-tests", "--release", state.release_id]
    assert main([*argv, "--oof", "Alice"]) == 0
    recorded = C.load_state(root, state.release_id)
    confirmation = deepcopy(_data(recorded)["oof"])
    assert main(argv) == 0  # new load, no repeat prompt or implied empty answer
    reloaded = C.load_state(root, state.release_id)
    assert _data(reloaded)["oof"] == confirmation
    assert _data(reloaded)["plan"]["review_inputs"]["oof"] == confirmation
    assert "alice@example.com" not in _data(reloaded)["plan"]["eligible"]


def test_engine_blocks_repeatedly_until_owner_answers_and_preserves_data(state, inputs):
    orch = Orchestrator(C.DEFAULT_CONFIG, state,
                        mocks={"bug_bash.distribute_tests": inputs})
    for phase in orch.config["phases"]:
        if phase["id"] == "bug_bash":
            for spec in phase["steps"]:
                if spec["id"] == step.ID:
                    break
                state.set_step(phase["id"], spec["id"], StepState(status="done"))
            break
        for spec in phase["steps"]:
            state.set_step(phase["id"], spec["id"], StepState(status="done"))
    for _ in range(2):
        action = orch.step_once()
        assert action.step == step.ID and action.kind == "reminder"
        assert "Is anyone OOF for this Bug Bash?" in action.message
        assert not state.is_done("bug_bash", step.ID) and "plan" not in _data(state)
        assert _data(state)["oof_candidates"]
    _build(state, inputs, oof=["Alice"])
    confirmation = deepcopy(_data(state)["oof"])
    action = orch.step_once()
    assert action.step == step.ID and action.kind == "ran"
    assert state.is_done("bug_bash", step.ID)
    assert _data(state)["oof"] == confirmation and _data(state)["plan"]
    with mockctx.active(inputs):
        step.validate_stored_plan(state)
    # The normal next step is now allowed; do not execute its outbound Scout action.
    assert orch.step_once().step == "send_invite"
