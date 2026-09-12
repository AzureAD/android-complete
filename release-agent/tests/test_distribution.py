"""Live ADO distribution, owner availability and transient correction previews."""
from copy import deepcopy
import json

import pytest

from orchestrator import cli_common as C
from orchestrator.cli import build_parser
from orchestrator.commands import distribute as command
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import Done, Blocked
from orchestrator.state import ReleaseState, StepState
from steps.bug_bash import distribute_tests as step
from steps.lib import mockctx
from tools import distribution as D


def observe(inputs, *, automated=None, failed=None):
    """Refresh explicit fake ADO observations in place, preserving independent point testers."""
    snapshot, groups = {}, []
    automated = inputs.get("auth_automated", []) if automated is None else automated
    failed = inputs.get("auth_failed", []) if failed is None else failed
    for prefix, cases, pid, sid in (("B", inputs.get("broker_cases", []), 1, 11),
                                     ("A", inputs.get("auth_cases", []), 2, 22)):
        points = []
        for case in cases:
            cid = str(case["id"])
            owner = D._identity(case.get("assignee")) or None
            identity = "id-" + owner if owner else None
            snapshot[cid] = {"assignee": owner, "identity_id": identity, "revision": case.get("revision", 1)}
            case.setdefault("tester_id", identity)
            if prefix == "A" and int(cid) in automated and int(cid) not in failed and not any(
                    tag.casefold() == "blocked" for tag in case.get("tags", [])):
                continue
            points.append({"id": int(cid), "case_id": cid, "tester_id": case["tester_id"]})
        if points:
            groups.append({"prefix": prefix, "plan_id": pid, "suite_id": sid, "points": points})
    inputs.setdefault("case_snapshot", {}).clear()
    inputs["case_snapshot"].update(snapshot)
    inputs.setdefault("point_sets", [])[:] = groups
    return inputs


@pytest.fixture
def inputs():
    return observe({
        "roster": [{"name": name, "upn": upn} for name, upn in [
            ("Alice", "ALICE@example.com"), ("Bob", "bob@example.com"), ("Charlie", "charlie@example.com"),
            ("Owner", "owner@example.com"), ("OCE", "oce@example.com"),
            ("Always excluded", "moghosh@microsoft.com"), ("Jia Le He", "JIALH@microsoft.com"),
            ("Veena Soman", "veenasoman@microsoft.com")]],
        "oce": "oce@example.com",
        "broker_cases": [{"id": str(i), "assignee": "alice@example.com"} for i in range(1, 8)],
        "auth_cases": [{"id": "8", "assignee": "owner@example.com"}, {"id": "9", "assignee": "oce@example.com"}],
        "auth_automated": [],
    })


@pytest.fixture
def state():
    return ReleaseState(release_id="test-live-distribution", owner_email="owner@example.com", readiness_signed=True)


def inspect(state, inputs, **kwargs):
    observe(inputs)
    with mockctx.active(inputs):
        return step.inspect_distribution(state, **kwargs)


def data(state):
    return state.get_step("bug_bash", step.ID).data


def cli(monkeypatch, state, inputs, *flags):
    observe(inputs)
    saved = []
    monkeypatch.setattr(C, "load_state", lambda *_: state)
    monkeypatch.setattr(C, "save_state", lambda st, *_: saved.append(deepcopy(st)))
    monkeypatch.setattr(C, "load_orch", lambda *_: (state, Orchestrator(C.DEFAULT_CONFIG, state, mocks={})))
    monkeypatch.setattr(command.mocks_mod, "load_mocks", lambda: {"bug_bash.distribute_tests": inputs})
    args = build_parser().parse_args(["distribute-tests", "--release", state.release_id, *flags])
    return command.cmd_distribute_tests(args), saved


def fake_writes(monkeypatch, inputs):
    calls = []
    cases = {str(c["id"]): c for c in inputs["broker_cases"] + inputs["auth_cases"]}
    def write(cid, upn, *, expected_revision):
        assert expected_revision == cases[cid].get("revision", 1)
        calls.append(("case", cid, upn))
        cases[cid]["assignee"] = upn
        cases[cid]["revision"] = expected_revision + 1
        observe(inputs)
        return True, ""
    def sync(pid, sid, assignments, *, expected_testers):
        assert {int(cid): cases[str(cid)]["tester_id"] for cid in assignments} == expected_testers
        for cid, upn in assignments.items():
            target = "id-" + upn
            if cases[str(cid)]["tester_id"] != target:
                calls.append(("point", str(cid), upn))
                cases[str(cid)]["tester_id"] = target
        observe(inputs)
        return True, ""
    monkeypatch.setattr(D, "set_assigned_to", write)
    monkeypatch.setattr(D, "sync_point_testers", sync)
    return calls


def correct_ado(inputs, report):
    for case in inputs["broker_cases"] + inputs["auth_cases"]:
        key = ("B:" if case in inputs["broker_cases"] else "A:") + str(case["id"])
        if key in report["_targets"]:
            case["assignee"] = report["_targets"][key]
            case["tester_id"] = "id-" + case["assignee"]
    observe(inputs)


def test_confirmation_precedes_case_reads_and_candidates_are_transient(state, inputs):
    out, report = inspect(state, inputs)
    assert isinstance(out, Blocked) and "Is anyone OOF" in out.reason
    assert [m["name"] for m in report["candidates"]] == ["Alice", "Bob", "Charlie"]
    assert not data(state)


def test_preview_is_transient_and_reuses_only_availability(state, inputs):
    out, report = inspect(state, inputs, oof=[])
    assert isinstance(out, Blocked) and not report["valid"]
    assert report["proposed_counts"] == dict.fromkeys(["alice@example.com", "bob@example.com", "charlie@example.com"], 3)
    assert len(report["case_changes"]) == 6
    before = deepcopy(data(state))
    assert set(before) == {"oof"}
    assert before["oof"]["confirmed_by"] == state.owner_email
    assert before["oof"]["source"] == "release-owner"
    assert before["oof"]["confirmed_at"]
    assert inspect(state, inputs)[1]["review_hash"] == report["review_hash"]
    assert data(state) == before


def test_oof_balance_and_exclusions(state, inputs):
    _, report = inspect(state, inputs, oof=[" Alice ", "ALICE@example.com"])
    assert report["eligible"] == ["bob@example.com", "charlie@example.com"]
    assert sorted(report["proposed_counts"].values()) == [4, 5]
    assert report["oof_excluded"] == [{"name": "Alice", "upn": "alice@example.com"}]
    assert data(state)["oof"]["upns"] == ["alice@example.com"]
    assert len(report["case_changes"]) == 9


@pytest.mark.parametrize("selection", [["unknown@example.com"], ["Ali"], [""], [" "]])
def test_invalid_answer_clears_prior_confirmation(state, inputs, selection):
    inspect(state, inputs, oof=[])
    out, report = inspect(state, inputs, oof=selection)
    assert isinstance(out, Blocked) and report["error"] and "oof" not in data(state)
    assert isinstance(inspect(state, inputs)[0], Blocked)


def test_duplicate_roster_names_need_exact_upn(inputs):
    roster = inputs["roster"] + [{"name": "Alice", "upn": "another@example.com"}]
    with pytest.raises(ValueError, match="Ambiguous"):
        D.resolve_oof(["Alice"], roster)
    assert D.resolve_oof(["ALICE@example.com", " Bob "], roster) == ["alice@example.com", "bob@example.com"]
    assert D.canonical_roster(roster) == D.canonical_roster(list(reversed(roster)))


@pytest.mark.parametrize("oce", [None, "", "OCE", "a@@example.com", "@example.com", "a@", "a b@example.com"])
def test_missing_oce_hides_candidates(state, inputs, oce):
    state.set_step("bug_bash", step.ID, StepState(data={"oce": oce}))
    out, report = inspect(state, inputs)
    assert isinstance(out, Blocked) and "--oce" in out.reason and not report.get("candidates")


def test_missing_owner_hides_candidates(state, inputs):
    state.owner_email = None
    assert "release owner missing" in inspect(state, inputs)[0].reason


def test_replacement_availability_and_primary_oce_are_saved_not_assignments(state, inputs):
    _, report = inspect(state, inputs, oof=["Alice"], oce="bob@example.com")
    assert report["eligible"] == ["charlie@example.com", "oce@example.com"]
    _, report = inspect(state, inputs, oof=[])
    assert "alice@example.com" in report["eligible"] and "bob@example.com" not in report["eligible"]
    assert set(data(state)) == {"oof", "oce"}
    assert {"jialh@microsoft.com", "moghosh@microsoft.com", "veenasoman@microsoft.com"} <= set(D.load_config()["always_excluded"])


def test_automated_and_blocked_cases_have_explicit_triage(state, inputs):
    inputs["auth_cases"] += [{"id": "10", "assignee": "former@example.com", "tags": ["Blocked"]},
                             {"id": "11", "assignee": "former@example.com", "tags": ["Automated"]}]
    inputs["auth_automated"], inputs["auth_failed"] = [8], [8]
    _, report = inspect(state, inputs, oof=[])
    assert set(report["owner_triage"]) == {"A:8", "A:10"}
    assert report["owner_triage"]["A:8"]["reasons"] == ["failed_automation"]
    assert "A:11" in report["_targets"]
    assert "plan" not in data(state)


def test_valid_ado_requires_no_corrections_even_after_manual_changes(state, inputs):
    _, proposal = inspect(state, inputs, oof=[])
    correct_ado(inputs, proposal)
    assert isinstance(inspect(state, inputs)[0], Done)
    cases = inputs["broker_cases"] + inputs["auth_cases"]
    a = next(c for c in cases if c["assignee"] == "alice@example.com")
    b = next(c for c in cases if c["assignee"] == "bob@example.com")
    a["assignee"], b["assignee"] = b["assignee"], a["assignee"]
    a["tester_id"], b["tester_id"] = b["tester_id"], a["tester_id"]
    out, report = inspect(state, inputs)
    assert isinstance(out, Done) and not report["case_changes"] and not report["point_changes"]


def test_point_only_mismatch_is_detected_and_corrected(state, inputs, monkeypatch):
    _, proposal = inspect(state, inputs, oof=[])
    correct_ado(inputs, proposal)
    inputs["broker_cases"][0]["tester_id"] = "former-tester"
    _, report = inspect(state, inputs)
    assert not report["case_changes"] and len(report["point_changes"]) == 1
    calls = fake_writes(monkeypatch, inputs)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 0
    assert len(calls) == 1 and calls[0][0] == "point"
    assert state.is_done("bug_bash", step.ID) and "plan" not in data(state)


def test_live_corrections_apply_then_read_back_and_do_not_repeat(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=["Alice"])
    calls = fake_writes(monkeypatch, inputs)
    result, saved = cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])
    assert result == 0 and sum(c[0] == "case" for c in calls) == 9
    assert sum(c[0] == "point" for c in calls) == 9
    assert all(set(data(s)) <= {"oof", "oce"} for s in saved)
    assert cli(monkeypatch, state, inputs, "--apply")[0] == 0 and len(calls) == 18


@pytest.mark.parametrize("change", ["assignee", "point", "roster", "triage", "missing_hash"])
def test_changed_live_inputs_cannot_apply_unreviewed_corrections(state, inputs, monkeypatch, change):
    _, report = inspect(state, inputs, oof=[])
    token = report["review_hash"]
    if change == "assignee":
        inputs["broker_cases"][0]["assignee"] = "other@example.com"
    elif change == "point":
        inputs["broker_cases"][0]["tester_id"] = "other-tester"
    elif change == "roster":
        inputs["roster"].append({"name": "New", "upn": "new@example.com"})
    elif change == "triage":
        inputs["auth_cases"][0]["tags"] = ["Blocked"]
    else:
        token = ""
    calls = fake_writes(monkeypatch, inputs)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", token)[0] == 1
    assert not calls


def test_legacy_saved_map_and_applied_flag_are_ignored(state, inputs, monkeypatch):
    state.set_step("bug_bash", step.ID, StepState(status="done", data={
        "plan": {"assignments": {"B:1": "wrong@example.com"}, "applied": True}}))
    assert cli(monkeypatch, state, inputs, "--apply")[0] == 1
    assert "plan" not in data(state) and not state.is_done("bug_bash", step.ID)


@pytest.mark.parametrize("change", ["owner", "source", "release", "time", "upn"])
def test_stored_availability_is_still_validated_before_corrections(state, inputs, monkeypatch, change):
    inspect(state, inputs, oof=[])
    confirmation = data(state)["oof"]
    field, value = {"owner": ("confirmed_by", "other@example.com"), "source": ("source", "calendar"),
                    "release": ("release_id", "different"), "time": ("confirmed_at", "bad-date"),
                    "upn": ("upns", ["unknown@example.com"])}[change]
    confirmation[field] = value
    calls = fake_writes(monkeypatch, inputs)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", "old")[0] == 1
    assert not calls


def test_changed_identity_with_same_upn_changes_the_review(state, inputs):
    _, before = inspect(state, inputs, oof=[])
    inputs["case_snapshot"]["1"]["identity_id"] = "replacement-identity"
    with mockctx.active(inputs):
        _, after = step.inspect_distribution(state)
    assert before["review_hash"] != after["review_hash"]


@pytest.mark.parametrize("flags", [("--no-oof",), ("--oof", "Alice"), ("--oce", "bob@example.com")])
def test_apply_cannot_combine_new_availability_with_writes(state, inputs, monkeypatch, flags):
    before = deepcopy(state)
    result, saved = cli(monkeypatch, state, inputs, "--apply", *flags)
    assert result == 1 and not saved and state == before


def test_validation_reports_mismatches_without_writes_or_saved_lists(state, inputs, monkeypatch, capsys):
    result, _ = cli(monkeypatch, state, inputs, "--no-oof", "--validate", "--json")
    report = json.loads(capsys.readouterr().out)
    assert result == 1 and not report["valid"] and "case_changes" in report
    assert all(not key.startswith("_") for key in report)
    assert set(data(state)) == {"oof"}


def test_preview_availability_survives_reload_without_an_assignment_cache(state, inputs, monkeypatch, tmp_path):
    from orchestrator.cli import main
    monkeypatch.setattr(command.mocks_mod, "load_mocks", lambda: {"bug_bash.distribute_tests": inputs})
    C.save_state(state, str(tmp_path), state.release_id)
    argv = ["--runs-root", str(tmp_path), "distribute-tests", "--release", state.release_id]
    assert main([*argv, "--oof", "Alice"]) == 0
    stored = C.load_state(str(tmp_path), state.release_id)
    assert set(data(stored)) == {"oof"}
    assert main(argv) == 0 and data(C.load_state(str(tmp_path), state.release_id)) == data(stored)


def test_engine_holds_until_actual_assignments_and_testers_are_valid(state, inputs):
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={"bug_bash.distribute_tests": inputs})
    for phase in orch.config["phases"]:
        for spec in phase["steps"]:
            if phase["id"] == "bug_bash" and spec["id"] == step.ID:
                break
            state.set_step(phase["id"], spec["id"], StepState(status="done"))
        if phase["id"] == "bug_bash":
            break
    assert orch.step_once().kind == "reminder"
    _, report = inspect(state, inputs, oof=[])
    assert orch.step_once().kind != "ran"
    correct_ado(inputs, report)
    assert orch.step_once().kind == "ran"
    assert state.is_done("bug_bash", step.ID) and "plan" not in data(state)
