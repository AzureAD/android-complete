"""Live validation failures, partial corrections and ADO revision protection."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tools import distribution as D, pipelines as P
from tools.distribution import set_assigned_to as set_assigned_to_request
from tests.test_distribution import inputs, state, inspect, data, cli, observe, fake_writes


def test_partial_failure_is_read_back_and_old_approval_cannot_overwrite_it(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=["Alice"])
    calls = fake_writes(monkeypatch, inputs)
    writer = D.set_assigned_to
    count = 0
    def fail(cid, upn, **kwargs):
        nonlocal count
        count += 1
        return writer(cid, upn, **kwargs) if count == 1 else (False, "HTTP 412")
    monkeypatch.setattr(D, "set_assigned_to", fail)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    assert len(calls) == 1 and not state.is_done("bug_bash", "distribute_tests")
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 1
    assert len(calls) == 1 and set(data(state)) == {"oof"}
    _, fresh = inspect(state, inputs)
    monkeypatch.setattr(D, "set_assigned_to", writer)
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", fresh["review_hash"])[0] == 0
    assert sum(c[0] == "case" for c in calls) == 9


def test_provider_success_without_correct_ado_values_does_not_complete(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=[])
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(D, "sync_point_testers", lambda *a, **kw: (True, ""))
    assert cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])[0] == 2
    assert not state.is_done("bug_bash", "distribute_tests")


def test_unexpected_crash_leaves_no_assignment_map_and_fresh_reads_detect_partial_work(state, inputs, monkeypatch):
    _, report = inspect(state, inputs, oof=[])
    calls = fake_writes(monkeypatch, inputs)
    writer = D.set_assigned_to
    def crash(cid, upn, **kwargs):
        if calls:
            raise RuntimeError("crashed")
        return writer(cid, upn, **kwargs)
    monkeypatch.setattr(D, "set_assigned_to", crash)
    with pytest.raises(RuntimeError, match="crashed"):
        cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])
    _, after = inspect(state, inputs)
    assert not after["valid"] and after["review_hash"] != report["review_hash"]
    assert set(data(state)) == {"oof"}


def test_completion_failure_is_recovered_using_ado_without_reapplying(state, inputs, monkeypatch):
    from orchestrator.commands import distribute as command
    _, report = inspect(state, inputs, oof=[])
    calls = fake_writes(monkeypatch, inputs)
    finish = command._finish
    def crash(*a):
        raise RuntimeError("completion failed")
    monkeypatch.setattr(command, "_finish", crash)
    with pytest.raises(RuntimeError, match="completion failed"):
        cli(monkeypatch, state, inputs, "--apply", "--review-hash", report["review_hash"])
    before = len(calls)
    monkeypatch.setattr(command, "_finish", finish)
    assert cli(monkeypatch, state, inputs, "--apply")[0] == 0 and len(calls) == before


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
                       "assignedTo": {"id": f"owner-{cid}"}}], ""
    monkeypatch.setattr(P, "_ado_rest_get_all", get)
    ok, groups, detail = D.read_point_testers(1, 11, ["1", "2"])
    assert ok and not detail and len(groups) == 2
    assert groups[1]["points"] == [{"id": 2, "case_id": "2", "tester_id": "owner-2"}]
    assert not D.read_point_testers(1, 11, ["1", "3"])[0]


def test_point_alignment_rejects_concurrent_changes_and_preserves_outcomes(monkeypatch):
    pts = [{"id": 1, "testCase": {"id": 10}, "configuration": {"id": 84},
            "outcome": "Failed", "state": "NotReady", "assignedTo": {"id": "old"}},
           {"id": 2, "testCase": {"id": 11}, "configuration": {"id": 84},
            "outcome": "Passed", "state": "Completed", "assignedTo": {"id": "untouched"}}]
    monkeypatch.setattr(P, "_ado_rest_get_all", lambda *a: (True, deepcopy(pts), ""))
    assert not D.sync_point_testers(1, 11, {10: "owner@example.com"}, expected_testers={1: "different"})[0]
    monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, {"value": [
        {"id": 10, "fields": {"System.AssignedTo": {"id": "owner-id", "uniqueName": "owner@example.com"}}}]}, ""))
    calls = []
    def send(url, method, body, timeout):
        calls.append(body)
        pts[0]["assignedTo"] = {"id": body["tester"]["id"]}
        return True, {}, ""
    monkeypatch.setattr(P, "_ado_rest_send", send)
    assert D.sync_point_testers(1, 11, {10: "owner@example.com"}, expected_testers={1: "old"})[0]
    assert calls == [{"tester": {"id": "owner-id"}}]
    assert pts[0]["outcome"] == "Failed" and pts[1]["assignedTo"]["id"] == "untouched"
