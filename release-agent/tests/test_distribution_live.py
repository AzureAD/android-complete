"""Live validation failures, partial corrections and ADO revision protection."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from tools import distribution as D, pipelines as P
from tools.distribution import set_assigned_to as set_assigned_to_request
from tests.test_distribution import inputs, state, inspect, data, cli, observe, fake_writes
from tests._context import fresh_orchestrator
from orchestrator import cli_common as C
from orchestrator import m365, write_review as W
from orchestrator.commands import distribute as command
from steps.bug_bash import distribute_tests as S


GROUP_ID = "55555555-5555-5555-5555-555555555555"


def roster_bundle(roster, *, group_mail=None, owner="owner@example.com"):
    mail = group_mail or D.load_config()["roster_group"]
    path = f"/groups/{GROUP_ID}/members?$select=id,displayName,userPrincipalName,mail&$top=100"
    def envelope(url, result):
        return {"version": 1, "tool": "workiq.fetch", "arguments": {"entityUrls": [url]},
                "account": owner, "observed_at": datetime.now(timezone.utc).isoformat(), "result": result}
    return {"observations": [
        envelope(D.roster_lookup_path(mail), {"value": [
            {"id": GROUP_ID, "mail": mail, "displayName": "Release roster"}]}),
        envelope(path, {"value": [
            {"@odata.type": "#microsoft.graph.user", "id": str(UUID(int=i + 1)),
             "displayName": row["name"], "userPrincipalName": row["upn"], "mail": row["upn"]}
            for i, row in enumerate(roster)]}),
    ]}


def roster_plan(monkeypatch, state, inputs, path):
    orch = fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    monkeypatch.setattr(command.mocks_mod, "load_mocks", lambda: {"bug_bash.distribute_tests": inputs})
    args = SimpleNamespace(no_oof=True, oof=None, oce="oce@example.com", roster_file=str(path))
    return orch, command.plan_distribution(args, orch)


def test_roster_connector_primary_path_never_uses_az_and_retains_source(monkeypatch, inputs):
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **kw: pytest.fail("Roster must not run az"))
    evidence = roster_bundle(inputs["roster"])
    members, source = D.roster_observed(D.load_config()["roster_group"], evidence, "owner@example.com")
    assert D.canonical_roster(members) == D.canonical_roster(inputs["roster"])
    assert source["group"]["id"] == GROUP_ID and source["observations"] == evidence["observations"]
    assert members[0]["type"] == "#microsoft.graph.user" and members[0]["mail"]
    assert not hasattr(D, "_graph_get") and not hasattr(D, "_graph_token")


def test_missing_roster_requests_exact_mail_entity_not_people_search(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **kw: pytest.fail("No az roster fallback"))
    mail = "test'roster@example.test"
    with pytest.raises(D.ConnectorObservationRequired) as failure:
        D.resolve_roster(mail, owner="owner@example.test")
    request = failure.value
    assert request.tool == "workiq.fetch"
    assert request.arguments == {"entityUrls": [D.roster_lookup_path(mail)]}
    assert request.requests == [m365.observation_request("workiq.fetch", request.arguments, "owner@example.test")]
    assert "If the required" in str(request) and "people search" in str(request)


def test_missing_roster_step_and_preview_request_connector_without_ado_writes(
        monkeypatch, state, inputs, capsys):
    inputs.pop("roster")
    orch = fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    context = orch.context("bug_bash", S.ID, parameters={"oof": [], "oce": "oce@example.com"},
                           inputs=inputs)
    outcome = S.build(context)
    assert outcome.kind == "needs_skill" and not outcome.outbound and outcome.tool == "workiq.fetch"
    assert outcome.payload["_connector_reads"] and "--roster-file" in outcome.payload["_followup"]
    result, saved = cli(monkeypatch, state, inputs, "--no-oof")
    report = json.loads(capsys.readouterr().out)
    assert result == 1 and not saved and not report["permission_to_execute"]
    assert report["connector_reads"] and "If the required" in report["error"]


@pytest.mark.parametrize("change", [
    "account", "stale_lookup", "stale_members", "wrong_group_mail", "wrong_group_id",
    "wrong_query", "missing_type", "missing_upn", "duplicate", "truncated", "count", "extra", "legacy",
])
def test_roster_rejects_unbound_stale_partial_or_ambiguous_evidence(inputs, change):
    evidence = roster_bundle(inputs["roster"])
    lookup, members = evidence["observations"]
    if change == "account":
        members["account"] = "other@example.com"
    elif change.startswith("stale_"):
        target = lookup if change == "stale_lookup" else members
        target["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    elif change == "wrong_group_mail":
        lookup["result"]["value"][0]["mail"] = "other@example.com"
    elif change == "wrong_group_id":
        lookup["result"]["value"][0]["id"] = "66666666-6666-6666-6666-666666666666"
    elif change == "wrong_query":
        lookup["arguments"]["entityUrls"] = ["/groups?$search=roster"]
    elif change == "missing_type":
        members["result"]["value"][0].pop("@odata.type")
    elif change == "missing_upn":
        members["result"]["value"][0].pop("userPrincipalName")
    elif change == "duplicate":
        members["result"]["value"].append(deepcopy(members["result"]["value"][0]))
    elif change == "truncated":
        members["result"]["hasMoreResults"] = True
    elif change == "count":
        members["result"]["@odata.count"] = len(members["result"]["value"]) + 1
    elif change == "extra":
        evidence["observations"].append(deepcopy(members))
    else:
        evidence = members["result"]["value"]
    with pytest.raises(ValueError):
        D.roster_observed(D.load_config()["roster_group"], evidence, "owner@example.com")


def test_roster_requires_every_actual_members_page_and_ignores_typed_nonusers(inputs):
    evidence = roster_bundle(inputs["roster"])
    first = evidence["observations"][1]
    path = f"/groups/{GROUP_ID}/members?$skiptoken=next"
    tail = deepcopy(first)
    tail["arguments"] = {"entityUrls": [path]}
    tail["result"]["value"] = first["result"]["value"][2:]
    first["result"]["value"] = first["result"]["value"][:2] + [{
        "@odata.type": "#microsoft.graph.group", "id": str(UUID(int=99)),
        "displayName": "Nested group", "userPrincipalName": "not-a-user@example.com"}]
    first["result"]["@odata.nextLink"] = "https://graph.microsoft.com/v1.0" + path
    with pytest.raises(D.ConnectorObservationRequired) as failure:
        D.roster_observed(D.load_config()["roster_group"], evidence, "owner@example.com")
    assert failure.value.arguments == {"entityUrls": [path]}
    evidence["observations"].append(tail)
    members, source = D.roster_observed(D.load_config()["roster_group"], evidence, "owner@example.com")
    assert len(members) == len(inputs["roster"]) and len(source["observations"]) == 3
    assert all(row["upn"] != "not-a-user@example.com" for row in members)


def test_roster_file_is_real_step_parameter_and_checked_tester_write_input(
        monkeypatch, tmp_path, state, inputs):
    inspect(state, inputs, oof=[])
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster_bundle(inputs["roster"])), encoding="utf-8")
    orch, plan = roster_plan(monkeypatch, state, inputs, path)
    parsed = orch.handler("bug_bash", S.ID).parse_parameters(values={"roster_file": str(path)})
    assert parsed.roster_file == str(path)
    outcome = S.build(orch.context("bug_bash", S.ID, parameters=parsed, inputs=inputs))
    assert outcome.kind == "blocked" and "corrections need review" in outcome.reason
    assert plan.parameters["roster_file"] == str(path)
    assert plan.preconditions["inputs"]["roster_source"]["observations"]
    calls = fake_writes(monkeypatch, inputs)
    review_hash = W.review_hash(orch, "bug_bash", S.ID, plan)
    result, _ = cli(monkeypatch, state, inputs, "--no-oof", "--roster-file", str(path),
                    "--apply", "--review-hash", review_hash)
    assert result == 0 and calls and all(row[0] == "point" for row in calls)


@pytest.mark.parametrize("change", ["membership", "source", "stale", "partial"])
def test_reviewed_roster_change_or_expiry_blocks_before_tester_writes(
        monkeypatch, tmp_path, state, inputs, change):
    inspect(state, inputs, oof=[])
    path = tmp_path / "roster.json"
    evidence = roster_bundle(inputs["roster"])
    path.write_text(json.dumps(evidence), encoding="utf-8")
    orch, plan = roster_plan(monkeypatch, state, inputs, path)
    reviewed = W.review_hash(orch, "bug_bash", S.ID, plan)
    if change == "membership":
        evidence["observations"][1]["result"]["value"][0]["id"] = str(UUID(int=999))
    elif change == "source":
        evidence["observations"][0]["result"]["value"][0]["displayName"] = "Changed group metadata"
    elif change == "stale":
        evidence["observations"][1]["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    else:
        evidence["observations"][1]["result"]["@odata.nextLink"] = (
            f"https://graph.microsoft.com/v1.0/groups/{GROUP_ID}/members?$skiptoken=missing")
    path.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(D, "sync_point_testers", lambda *a, **kw: pytest.fail("No tester write after roster drift"))
    with pytest.raises(ValueError):
        command._apply(SimpleNamespace(plan=plan, validate=lambda: None))
    if change in {"membership", "source"}:
        _, changed = roster_plan(monkeypatch, state, inputs, path)
        assert W.review_hash(orch, "bug_bash", S.ID, changed) != reviewed
    assert cli(monkeypatch, state, inputs, "--no-oof", "--roster-file", str(path),
               "--apply", "--review-hash", reviewed)[0] == 1


def test_partial_failure_is_read_back_and_old_approval_cannot_overwrite_it(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=["Alice"])
    calls = fake_writes(monkeypatch, inputs)
    writer = D.sync_point_testers
    count = 0
    def fail(*args, **kwargs):
        nonlocal count
        count += 1
        return writer(*args, **kwargs) if count == 1 else (False, "HTTP 412")
    monkeypatch.setattr(D, "sync_point_testers", fail)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    assert len(calls) == 2 and not state.is_done("bug_bash", "distribute_tests")
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 1
    assert len(calls) == 2 and set(data(state)) == {"oof", "in_flight_since"}
    owner = deepcopy(state.get_step("bug_bash", "distribute_tests").execution)
    assert owner["write_review"]["hash"] == report["review_hash"]
    _, pending = inspect(state, inputs)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", pending["review_hash"])[0] == 1
    assert state.get_step("bug_bash", "distribute_tests").execution == owner
    orch = fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    assert orch.reopen("bug_bash", "distribute_tests",
                       "Owner verified the first ADO update and stopped the failed worker.").changed
    _, fresh = inspect(state, inputs, oof=["Alice"])
    monkeypatch.setattr(D, "sync_point_testers", writer)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", fresh["review_hash"])[0] == 0
    assert sum(c[0] == "point" for c in calls) == 9


def test_provider_success_without_correct_ado_values_does_not_complete(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=[])
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(D, "sync_point_testers", lambda *a, **kw: (True, ""))
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    assert not state.is_done("bug_bash", "distribute_tests")


def test_unexpected_crash_leaves_no_assignment_map_and_fresh_reads_detect_partial_work(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=[])
    calls = fake_writes(monkeypatch, inputs)
    writer = D.sync_point_testers
    def crash(*args, **kwargs):
        if calls:
            raise RuntimeError("crashed")
        return writer(*args, **kwargs)
    monkeypatch.setattr(D, "sync_point_testers", crash)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    _, after = inspect(state, inputs)
    assert not after["valid"] and after["review_hash"] != report["review_hash"]
    assert set(data(state)) == {"oof", "in_flight_since"}
    record = state.get_step("bug_bash", "distribute_tests")
    assert record.status == "blocked"
    assert record.execution["write_review"]["hash"] == report["review_hash"]


def test_completion_failure_is_recovered_using_ado_without_reapplying(state, inputs, monkeypatch):
    from orchestrator.commands import distribute as command
    _, report = inspect(state, inputs, oof=[])
    calls = fake_writes(monkeypatch, inputs)
    readback = command._inspect
    def crash(*args, **kwargs):
        if calls:
            raise RuntimeError("completion failed")
        return readback(*args, **kwargs)
    monkeypatch.setattr(command, "_inspect", crash)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    before = len(calls)
    monkeypatch.setattr(command, "_inspect", readback)
    assert cli(monkeypatch, state, inputs, "--apply")[0] == 1 and len(calls) == before
    _, current = inspect(state, inputs)
    assert current["valid"]
    orch = fresh_orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    assert orch.reopen("bug_bash", "distribute_tests",
                       "Owner verified ADO has all reviewed assignments and the worker stopped.").changed
    _, current = inspect(state, inputs, oof=[])
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", current["review_hash"])[0] == 0
    assert len(calls) == before


@pytest.mark.parametrize("rows", [
    [], [{"fields": {"System.Id": 1}, "rev": 2}],
    [{"fields": {"System.Id": 1}, "rev": 2}] * 2,
    [{"fields": {"System.Id": 1, "System.AssignedTo": {"displayName": "Unknown"}}, "rev": 2}],
])
def test_assignment_read_rejects_missing_and_ambiguous_data(monkeypatch, rows):
    monkeypatch.setattr(P, "_ado_rest_get_h", lambda *a: (True, {"value": rows}, {}, ""))
    assert not D.case_assignment_snapshot(["1", "2"])[0]


def test_assignment_read_retains_identity_and_same_read_revision(monkeypatch):
    monkeypatch.setattr(P, "_ado_rest_get_h", lambda *a: (True, {"value": [
        {"fields": {"System.Id": 1}, "rev": 2},
        {"fields": {"System.Id": 2, "System.AssignedTo": {"uniqueName": "ALICE@example.com", "id": "ado-id"}}, "rev": 8},
    ]}, {}, ""))
    assert D.case_assignment_snapshot(["1", "2"])[1] == {
        "1": {"assignee": None, "identity_id": None, "revision": 2},
        "2": {"assignee": "ALICE@example.com", "identity_id": "ado-id", "revision": 8}}
    assert D._cases_assignedto(["1", "2"])[1] == {"1": None, "2": "ALICE@example.com"}


@pytest.mark.parametrize("revision", [None, 7])
def test_assignment_write_uses_atomic_revision_check_without_persisting_a_baseline(monkeypatch, revision):
    monkeypatch.setattr(D.shutil, "which", lambda *a: "fake-az")
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="fake-token"))
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    def open_request(req, **kwargs):
        requests.append(json.loads(req.data))
        return Response()
    monkeypatch.setattr(D.urllib.request, "urlopen", open_request)
    assert set_assigned_to_request(1, "alice@example.com", expected_revision=revision)[0]
    assignment = {"op": "add", "path": "/fields/System.AssignedTo", "value": "alice@example.com"}
    assert requests == [[{"op": "test", "path": "/rev", "value": 7}, assignment]
                        if revision is not None else [assignment]]


def test_reads_points_in_child_suites_and_rejects_missing_cases(monkeypatch):
    monkeypatch.setattr(D, "_suite_subtree", lambda *a: (True, [11, 12], ""))
    def get(url, *a):
        cid = 1 if "/11/" in url else 2
        return True, [{"id": cid, "testCase": {"id": cid}, "configuration": {"id": 84},
                       "assignedTo": {"id": f"owner-{cid}", "uniqueName": f"owner{cid}@example.test"}}], ""
    monkeypatch.setattr(P, "_ado_rest_get_all", get)
    ok, groups, detail = D.read_point_testers(1, 11, ["1", "2"])
    assert ok and not detail and len(groups) == 2
    assert groups[1]["points"] == [{"id": 2, "case_id": "2", "config_id": "84", "tester_id": "owner-2",
                                    "tester_upn": "owner2@example.test"}]
    assert not D.read_point_testers(1, 11, ["1", "3"])[0]


def test_point_alignment_rejects_concurrent_changes_and_preserves_outcomes(monkeypatch):
    pts = [{"id": 1, "testCase": {"id": 10}, "configuration": {"id": 84},
            "outcome": "Failed", "state": "NotReady", "assignedTo": {"id": "old"}},
           {"id": 2, "testCase": {"id": 11}, "configuration": {"id": 84},
            "outcome": "Passed", "state": "Completed", "assignedTo": {"id": "untouched"}}]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(pts), ""))
    assert not D.sync_point_testers(1, 11, {10: "owner@example.com"}, expected_testers={1: "different"})[0]
    monkeypatch.setattr(D, "resolve_tester_identity", lambda *a, **kw: "owner-id")
    calls = []
    def send(url, method, body, timeout):
        calls.append(body)
        pts[0]["assignedTo"] = {"id": body["tester"]["id"]}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    assert D.sync_point_testers(1, 11, {10: "owner@example.com"}, expected_testers={1: "old"})[0]
    assert calls == [{"tester": {"id": "owner-id"}}]
    assert pts[0]["outcome"] == "Failed" and pts[1]["assignedTo"]["id"] == "untouched"
