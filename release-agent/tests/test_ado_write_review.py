"""Offline checked ADO plans, exact receipts and single-attempt command execution."""
from argparse import Namespace
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from orchestrator import cli_common as C, revision, write_review as W
from orchestrator.cli import build_parser
from orchestrator.commands import distribute, localization, payload_wiki_cmd as wiki
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState
from steps.ccd import localization as L
from tools import distribution as D, localization as provider
from tests._harness import _active_step


def make_orch(phase, step):
    state = _active_step(
        ReleaseState(release_id="checked-ado", owner_email="owner@example.com", ccd="2026-09-09"),
        phase, step)
    orch = Orchestrator(C.DEFAULT_CONFIG, state, mocks={})
    assert state.workflow_revision is not None
    return orch


def arguments(command, *flags):
    return build_parser().parse_args([command, "--release", "checked-ado", *flags])


@pytest.fixture
def memory(monkeypatch):
    snapshots = []

    def attach(orch):
        monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))
        monkeypatch.setattr(C, "save_state", lambda state, *_: snapshots.append(deepcopy(asdict(state))))
        monkeypatch.setattr(C, "emit", lambda *a, **k: None)
        orch.state._checkpoint = lambda: snapshots.append(deepcopy(asdict(orch.state)))
        token = C._LOCKED_STATE.set((os.path.abspath(C.state_path(C.DEFAULT_RUNS_ROOT, "checked-ado")), object()))
        return token

    yield attach, snapshots
    C._LOCKED_STATE.set(None)


@pytest.fixture
def distribution_inputs(monkeypatch):
    inputs = {
        "roster": [{"name": "Alice", "upn": "alice@example.com"},
                   {"name": "Bob", "upn": "bob@example.com"},
                   {"name": "Charlie", "upn": "charlie@example.com"}],
        "oce": "oce@example.com", "auth_automated": [],
        "broker_cases": [{"id": "1"}, {"id": "2"}, {"id": "3"}], "auth_cases": [],
        "case_snapshot": {str(i): {"assignee": "alice@example.com",
                                  "identity_id": "alice-id", "revision": 7} for i in range(1, 4)},
        "point_sets": [{"prefix": "B", "plan_id": 91, "suite_id": 92,
                        "points": [{"id": i, "case_id": str(i), "tester_id": "alice-id"}
                                   for i in range(1, 4)]}],
    }
    monkeypatch.setattr(distribute.mocks_mod, "load_mocks", lambda: {"bug_bash.distribute_tests": inputs})
    monkeypatch.setattr(D, "resolve_tester_identity", lambda upn, **kw: upn)
    return inputs


def test_distribution_preview_is_read_only_and_normalizes_inputs(distribution_inputs, monkeypatch, capsys):
    orch = make_orch("bug_bash", "distribute_tests")
    before = deepcopy(asdict(orch.state))
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))
    monkeypatch.setattr(C, "save_state", lambda *_: pytest.fail("preview saved state"))
    args = arguments("distribute-tests", "--oof", " Charlie ", "--oce", " OCE@EXAMPLE.COM ")
    assert distribute.cmd_distribute_tests(args) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["permission_to_execute"] is False
    assert shown["plan"]["parameters"] == {"oof": ["charlie@example.com"], "oce": "oce@example.com"}
    assert asdict(orch.state) == before
    again = arguments("distribute-tests", "--oof", "CHARLIE@example.com", "--oof", "Charlie",
                      "--oce", "oce@example.com")
    plan = distribute.plan_distribution(again, orch)
    assert W.review_hash(orch, "bug_bash", "distribute_tests", plan) == shown["review_hash"]
    assert plan.operations[0].preconditions["revision"] == 7
    assert plan.operations[0].target["org"] == D.ORG
    assert plan.operations[-1].target["suite_id"] == 92


@pytest.mark.parametrize("change", ["revision", "tester", "suite", "roster", "org"])
def test_distribution_hash_binds_live_review(distribution_inputs, monkeypatch, change):
    orch = make_orch("bug_bash", "distribute_tests")
    args = arguments("distribute-tests", "--no-oof")
    before = W.review_hash(orch, "bug_bash", "distribute_tests", distribute.plan_distribution(args, orch))
    if change == "revision":
        distribution_inputs["case_snapshot"]["1"]["revision"] += 1
    elif change == "tester":
        distribution_inputs["point_sets"][0]["points"][0]["tester_id"] = "new-id"
    elif change == "suite":
        distribution_inputs["point_sets"][0]["suite_id"] += 1
    elif change == "roster":
        distribution_inputs["roster"].append({"name": "Dan", "upn": "dan@example.com"})
    else:
        monkeypatch.setattr(D, "ORG", "https://dev.azure.com/other")
    after = W.review_hash(orch, "bug_bash", "distribute_tests", distribute.plan_distribution(args, orch))
    assert before != after


def test_distribution_invalid_selection_and_revision_reject(distribution_inputs):
    orch = make_orch("bug_bash", "distribute_tests")
    with pytest.raises(ValueError, match="Unknown OOF"):
        distribute.plan_distribution(arguments("distribute-tests", "--oof", "Nobody"), orch)
    distribution_inputs["case_snapshot"]["1"].pop("revision")
    with pytest.raises(ValueError, match="revisions"):
        distribute.plan_distribution(arguments("distribute-tests", "--no-oof"), orch)


def test_distribution_missing_availability_returns_filtered_candidates(distribution_inputs, monkeypatch, capsys):
    orch = make_orch("bug_bash", "distribute_tests")
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))
    before = deepcopy(asdict(orch.state))
    assert distribute.cmd_distribute_tests(arguments("distribute-tests")) == 1
    result = json.loads(capsys.readouterr().out)
    assert {row["upn"] for row in result["candidates"]} == {
        "alice@example.com", "bob@example.com", "charlie@example.com"}
    assert "review_hash" not in result and result["permission_to_execute"] is False
    assert asdict(orch.state) == before


def test_unverified_launch_receipt_identifies_known_queue_response_for_recovery(monkeypatch):
    operation = W.WriteOperation("queue_localization_build", {
        "org": "https://dev.azure.com/review", "project": "project", "definition_id": 1,
    }, {"definition": {"id": 1}})
    calls = []
    monkeypatch.setattr(provider.P, "_ado_rest_send", lambda *args: (
        calls.append(args) or True, {"id": 123}, ""))

    def unreadable(*args):
        raise ValueError("provider readback unavailable")

    monkeypatch.setattr(provider, "read_build", unreadable)
    with pytest.raises(ValueError, match="123.*unverified"):
        provider.trigger(operation)
    assert len(calls) == 1


def test_distribution_executor_uses_captured_targets(distribution_inputs, monkeypatch):
    orch = make_orch("bug_bash", "distribute_tests")
    plan = distribute.plan_distribution(arguments("distribute-tests", "--no-oof"), orch)
    original_org = D.ORG
    monkeypatch.setattr(D, "ORG", "https://dev.azure.com/changed")
    calls = []
    checks = []
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **kw: (calls.append((a, kw)) or True, ""))
    monkeypatch.setattr(D, "sync_point_testers", lambda *a, **kw: (calls.append((a, kw)) or True, ""))
    distribute._apply(Namespace(plan=plan, validate=lambda: checks.append(True)))
    assert len(checks) == len(plan.operations)
    assert all(kw["org"] == original_org for _, kw in calls)
    assert calls[-1][1]["expected_testers"] == {1: "alice-id", 2: "alice-id", 3: "alice-id"}
    assert calls[-1][1]["reviewed_updates"] == plan.operations[-1].as_dict()["content"]["updates"]


@pytest.mark.parametrize("drift", [False, True])
def test_distribution_provider_uses_reviewed_tester_ids(monkeypatch, drift):
    points = [{"id": 1, "testCase": {"id": 10}, "configuration": {"id": 293},
               "assignedTo": {"id": "old"}, "outcome": "Passed"}]
    calls = []
    monkeypatch.setattr(D.P, "_ado_rest_get_all", lambda *a: (True, deepcopy(points), ""))
    monkeypatch.setattr(D.P, "_ado_rest_get", lambda *a: (True, {
        "value": [{"id": 10, "fields": {"System.AssignedTo": {
            "uniqueName": "alice@example.com", "id": "drift" if drift else "reviewed"}}}]}, ""))

    def send(url, method, body, timeout):
        calls.append((url, body))
        points[0]["assignedTo"]["id"] = body["tester"]["id"]
        return True, {}, ""

    monkeypatch.setattr(D.P, "_ado_rest_send", send)
    ok, detail = D.sync_point_testers(
        91, 92, {"10": "alice@example.com"}, org="https://dev.azure.com/reviewed", project="project",
        expected_testers={1: "old"}, expected_identities={"10": "reviewed"},
        reviewed_updates=[{"tester_id": "reviewed", "point_ids": [1]}])
    if drift:
        assert not ok and not calls and "identities differ" in detail
    else:
        assert ok and calls == [(
            "https://dev.azure.com/reviewed/project/_apis/test/Plans/91/Suites/92/points/1?api-version=5.0",
            {"tester": {"id": "reviewed"}})]


def test_distribution_identity_resolution_requires_exact_unique_account(monkeypatch):
    rows = {"value": [{"id": "exact", "isActive": True,
                       "properties": {"Account": {"$value": "Alice@example.com"}}},
                      {"id": "nearby", "providerDisplayName": "Alice Example"}]}
    monkeypatch.setattr(D.P, "_ado_rest_get", lambda *a: (True, rows, ""))
    assert D.resolve_tester_identity("alice@example.com") == "exact"
    rows["value"].append({"id": "other", "properties": {"Mail": {"$value": "alice@example.com"}}})
    with pytest.raises(ValueError, match="one exact"):
        D.resolve_tester_identity("alice@example.com")


@pytest.fixture
def wiki_inputs(monkeypatch):
    existing = {"content": "owner's existing page", "etag": '"etag-7"', "exists": True}
    monkeypatch.setattr(wiki.checks, "wiki_page_exists", lambda *a: existing["exists"])
    monkeypatch.setattr(wiki.checks, "get_wiki_page",
                        lambda *a, **kw: (True, existing["content"], existing["etag"], ""))
    monkeypatch.setattr(wiki, "_step_mocks", lambda orch: {
        "version": {"version": "6.0.0", "build_url": "https://example.test/build/1"}, "prs": []})
    return existing


def test_wiki_plan_binds_content_target_and_etag(wiki_inputs, monkeypatch):
    orch = make_orch("finalize", "wiki_payload")
    # StepContext inputs come from the orchestrator, not an ambient write payload.
    orch.mocks = {"finalize.wiki_payload": wiki._step_mocks(orch)}
    plan = wiki.plan_payload_wiki(orch)
    operation = plan.operations[0]
    assert operation.preconditions == {"exists": True, "content": "owner's existing page", "etag": '"etag-7"'}
    assert operation.content["content"].startswith("#App Version\n6.0.0")
    digest = W.review_hash(orch, "finalize", "wiki_payload", plan)
    wiki_inputs["etag"] = '"etag-8"'
    assert W.review_hash(orch, "finalize", "wiki_payload", wiki.plan_payload_wiki(orch)) != digest
    wiki_inputs["etag"] = '"etag-7"'
    monkeypatch.setitem(wiki.S.CONFIG, "wiki", "different.wiki")
    assert W.review_hash(orch, "finalize", "wiki_payload", wiki.plan_payload_wiki(orch)) != digest


@pytest.mark.parametrize("etag,exists", [("", True), ('""', True), ("*", True), ('"x"', None)])
def test_wiki_unknown_existence_and_unconditional_update_reject(wiki_inputs, etag, exists):
    orch = make_orch("finalize", "wiki_payload")
    orch.mocks = {"finalize.wiki_payload": wiki._step_mocks(orch)}
    wiki_inputs.update(etag=etag, exists=exists)
    with pytest.raises(ValueError):
        wiki.plan_payload_wiki(orch)


@pytest.mark.parametrize("exists", [True, False])
def test_wiki_executor_never_rereads_etag_for_write(wiki_inputs, monkeypatch, exists):
    orch = make_orch("finalize", "wiki_payload")
    wiki_inputs["exists"] = exists
    plan = wiki.plan_payload_wiki(orch)
    calls, validations = [], []
    args = arguments("create-payload-wiki", "--execute")
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))

    def authorize(*a):
        monkeypatch.setitem(wiki.S.CONFIG, "wiki", "late.wiki")
        wiki_inputs["etag"] = '"newer-etag"'
        return Namespace(plan=plan, execution_id="owned", reserved_only=False,
                         validate=lambda: validations.append(True))

    def write(**kw):
        calls.append(kw)
        wiki_inputs["content"] = kw["content"]
        return Namespace(ok=True, detail="")

    recorded = []
    monkeypatch.setattr(W, "authorize", authorize)
    monkeypatch.setattr(wiki.checks, "update_wiki_page", write)
    monkeypatch.setattr(wiki.checks, "create_wiki_page", write)
    monkeypatch.setattr(wiki, "_record", lambda *a, **kw: recorded.append((a, kw)))
    assert wiki.cmd_create_payload_wiki(args) == 0
    assert len(calls) == 1 and len(validations) == 2
    assert calls[0]["wiki"] == plan.operations[0].target["wiki"]
    if exists:
        assert calls[0]["etag"] == '"etag-7"'
    else:
        assert calls[0]["require_absent"] is True
    assert recorded[0][0][2] == "pass"


def test_wiki_stale_etag_zero_writes(wiki_inputs, memory, monkeypatch):
    orch = make_orch("finalize", "wiki_payload")
    memory[0](orch)
    args = arguments("create-payload-wiki", "--execute", "--approved-by", "reviewer")
    args.review_hash = W.review_hash(orch, "finalize", wiki.S.ID, wiki.plan_payload_wiki(orch))
    wiki_inputs["etag"] = "stale"
    monkeypatch.setattr(wiki.checks, "update_wiki_page", lambda **kw: pytest.fail("stale update"))
    assert wiki.cmd_create_payload_wiki(args) == 1
    assert not orch.state.get_step("finalize", wiki.S.ID).execution


def test_distribution_final_validation_persists_only_availability(distribution_inputs, monkeypatch):
    orch = make_orch("bug_bash", "distribute_tests")
    args = arguments("distribute-tests", "--apply", "--no-oof")
    plan = distribute.plan_distribution(args, orch)
    stored = []
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))
    monkeypatch.setattr(C, "save_state", lambda *a: None)
    monkeypatch.setattr(W, "authorize", lambda *a: Namespace(
        plan=plan, execution_id="owned", reserved_only=False, validate=lambda: None))
    monkeypatch.setattr(orch, "settle_execution", lambda *a, **kw: stored.append((a, kw)))

    def assign(case_id, upn, **kw):
        row = distribution_inputs["case_snapshot"][case_id]
        assert row["revision"] == kw["expected_revision"]
        row.update(assignee=upn, identity_id=upn, revision=row["revision"] + 1)
        return True, ""

    def sync(plan_id, suite_id, assignments, **kw):
        points = distribution_inputs["point_sets"][0]["points"]
        assert kw["expected_testers"] == {p["id"]: p["tester_id"] for p in points}
        for point in points:
            point["tester_id"] = distribution_inputs["case_snapshot"][point["case_id"]]["identity_id"]
        return True, ""

    monkeypatch.setattr(D, "set_assigned_to", assign)
    monkeypatch.setattr(D, "sync_point_testers", sync)
    assert distribute.cmd_distribute_tests(args) == 0
    data = stored[-1][1]["data"]
    assert set(data) == {"oof", "oce"}
    assert data["oof"]["upns"] == []


def test_distribution_partial_write_never_claims_completion(distribution_inputs, monkeypatch):
    orch = make_orch("bug_bash", "distribute_tests")
    args = arguments("distribute-tests", "--apply", "--no-oof")
    plan = distribute.plan_distribution(args, orch)
    calls, settlements = [], []
    monkeypatch.setattr(C, "load_orch", lambda *_: (orch.state, orch))
    monkeypatch.setattr(C, "save_state", lambda *a: None)
    monkeypatch.setattr(W, "authorize", lambda *a: Namespace(
        plan=plan, execution_id="owned", reserved_only=False, validate=lambda: None))
    monkeypatch.setattr(orch, "settle_execution", lambda *a, **kw: settlements.append((a, kw)))
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **kw: (calls.append(a) or len(calls) < 2, "revision changed"))
    monkeypatch.setattr(D, "sync_point_testers", lambda *a, **kw: pytest.fail("continued after partial failure"))
    assert distribute.cmd_distribute_tests(args) == 2
    assert len(calls) == 2
    assert "Earlier writes may have succeeded" in settlements[0][0][-1].reason
    assert not settlements[0][1]


@pytest.fixture
def launch_provider(monkeypatch):
    definition = {
        "id": L.CONFIG["pipeline_id"], "revision": 3,
        "project": {"id": "project-guid", "name": L.CONFIG["project"]},
        "repository": {"id": "repo-guid", "type": "TfsGit", "defaultBranch": "refs/heads/main"},
    }
    refs = {"value": [{"name": "refs/heads/main", "objectId": "a" * 40}]}
    reads, writes = [], []
    monkeypatch.setattr(localization.mocks_mod, "load_mocks", lambda: {})

    def read(url, timeout):
        reads.append(url)
        if "/definitions/" in url:
            return True, deepcopy(definition), ""
        if "/refs?" in url:
            return True, deepcopy(refs), ""
        raise AssertionError(f"Unexpected read: {url}")
    monkeypatch.setattr(provider.P, "_ado_rest_get", read)
    monkeypatch.setattr(provider.P, "_ado_rest_send", lambda *a: pytest.fail("Unexpected provider write"))
    return definition, refs, reads, writes


def build_receipt(plan, *, queue_time=None):
    target = plan.operations[0].target
    content = plan.operations[0].as_dict()["content"]
    return {
        **content, "id": 123, "project": {"id": target["project"], "name": L.CONFIG["project"]},
        "queueTime": queue_time or datetime.now(timezone.utc).isoformat(),
        "url": f"{target['org']}/{target['project']}/_apis/build/builds/123",
    }


def test_localization_plan_normalizes_args_and_reconstructs_receipt(launch_provider):
    orch = make_orch("ccd", "localization")
    a = arguments("launch-localization", "--branch", "main",
                  "--variable", "isCreatePrSelected=TRUE")
    b = arguments("launch-localization", "--branch", "refs/heads/main",
                  "--variable", "ISCREATEPRSELECTED=true")
    plan = localization.plan_localization(a, orch)
    assert plan == localization.plan_localization(b, orch)
    assert plan.operations[0].content["sourceVersion"] == "a" * 40
    assert plan.operations[0].content["definition"]["revision"] == 3
    receipt = build_receipt(plan)
    assert provider.receipt_plan(orch, L.CONFIG, receipt) == plan
    receipt["url"] = "https://msazure.visualstudio.com/DefaultCollection/project-guid/_apis/build/builds/123"
    assert provider.receipt_plan(orch, L.CONFIG, receipt) == plan


@pytest.mark.parametrize("changed", ["revision", "branch", "version", "variables", "repository", "project", "definition", "url"])
def test_localization_receipt_identity_drift_rejected_or_changes_hash(launch_provider, changed):
    orch = make_orch("ccd", "localization")
    plan = localization.plan_localization(arguments("launch-localization"), orch)
    build = build_receipt(plan)
    if changed == "revision":
        build["definition"]["revision"] += 1
    elif changed == "branch":
        build["sourceBranch"] = "refs/heads/other"
    elif changed == "version":
        build["sourceVersion"] = "b" * 40
    elif changed == "variables":
        build["parameters"] = '{"isCreatePrSelected":"false"}'
    elif changed == "repository":
        build["repository"]["id"] = "other"
    elif changed == "project":
        build["project"]["id"] = "other"
    elif changed == "definition":
        build["definition"]["id"] += 1
    else:
        build["url"] = build["url"].replace("msazure", "other")
    try:
        observed = provider.receipt_plan(orch, L.CONFIG, build)
    except ValueError:
        return
    assert W.review_hash(orch, "ccd", L.ID, observed) != W.review_hash(orch, "ccd", L.ID, plan)


def test_localization_receipt_time_must_belong_to_attempt():
    now = datetime.now(timezone.utc)
    step = Namespace(execution={"started_at": now.isoformat()},
                     data={"in_flight_since": now.isoformat()})
    for seconds in (-1, 301):
        with pytest.raises(ValueError, match="window"):
            provider.receipt_time({"queueTime": (now + timedelta(seconds=seconds)).isoformat()}, step,
                                  now=now + timedelta(minutes=10))
    assert provider.receipt_time({"queueTime": now.isoformat()}, step, now=now) == now.isoformat()


def test_localization_recovery_hash_verification_without_launch_payload(launch_provider, monkeypatch):
    orch = make_orch("ccd", "localization")
    args = arguments("record-localization-run", "--execution-id", "owned", "--build-id", "123")
    plan = localization.plan_localization(arguments("launch-localization"), orch)
    now = datetime.now(timezone.utc).isoformat()
    step = orch.state.get_step("ccd", L.ID)
    step.status = "in_flight"
    step.execution = {"id": "owned", "started_at": now, "refresh": False,
                      "write_review": {"hash": W.review_hash(orch, "ccd", L.ID, plan), "approved_by": "owner"}}
    step.data = {"in_flight_since": now}
    orch.state.set_step("ccd", L.ID, step)
    settlements = []
    monkeypatch.setattr(orch, "settle_execution", lambda *a, **kw: settlements.append((a, kw)))
    monkeypatch.setattr(C, "save_state", lambda *a: None)
    assert localization._attach_run(args, orch, build_receipt(plan, queue_time=now)) == 0
    assert set(settlements[0][1]["data"]) == {"in_flight_since", "build_id", "started_at", "run_url"}
    bad = build_receipt(plan, queue_time=now)
    bad["sourceVersion"] = "b" * 40
    with pytest.raises(ValueError, match="stored launch review"):
        localization._attach_run(args, orch, bad)


def test_localization_trigger_reads_actual_receipt_and_uses_frozen_plan(launch_provider, monkeypatch):
    orch = make_orch("ccd", "localization")
    plan = localization.plan_localization(arguments("launch-localization"), orch)
    receipt = build_receipt(plan)
    calls = []
    monkeypatch.setattr(provider.P, "_ado_rest_send",
                        lambda *a: (calls.append(a) or True, {"id": 123}, ""))
    monkeypatch.setattr(provider.P, "_ado_rest_get", lambda *a: (True, receipt, ""))
    monkeypatch.setitem(L.CONFIG, "pipeline_id", 999)
    monkeypatch.setitem(L.CONFIG, "org", "https://dev.azure.com/other")
    assert provider.trigger(plan.operations[0]) == receipt
    assert len(calls) == 1
    assert "msazure/project-guid/" in calls[0][0]
    assert calls[0][2]["definition"]["id"] == plan.operations[0].target["definition_id"]


def test_recovery_refuses_unreviewed_execution_before_provider_read(launch_provider, memory):
    orch = make_orch("ccd", "localization")
    memory[0](orch)
    args = arguments("record-localization-run", "--execution-id", "arbitrary", "--build-id", "123")
    assert localization.cmd_record_localization_run(args) == 1
    assert not launch_provider[2]


def test_launch_routes_to_checked_command():
    from tests._context import context
    orch = make_orch("ccd", "localization")
    action = L.build(context(orch.state))
    assert action.tool == "launch-localization"
    assert "azure_devops-pipelines_run_pipeline" not in json.dumps(action.payload)
    prompt = L.automation_prompt("checked-ado", {})
    assert "--review-hash" in prompt and "--approved-by" in prompt
    assert "step-action" not in prompt


def test_launch_checkpoints_before_only_trigger_and_recovers_no_payload(launch_provider, memory, monkeypatch):
    orch = make_orch("ccd", "localization")
    _, snapshots = memory
    memory[0](orch)
    args = arguments("launch-localization", "--execute", "--approved-by", "reviewer")
    plan = localization.plan_localization(args, orch)
    args.review_hash = W.review_hash(orch, "ccd", L.ID, plan)
    calls = []

    def trigger(operation):
        durable = snapshots[-1]["steps"]["ccd.localization"]
        assert durable["status"] == "in_flight"
        assert durable["execution"]["write_review"]["hash"] == args.review_hash
        assert operation == plan.operations[0]
        calls.append(operation)
        return build_receipt(plan)

    monkeypatch.setattr(provider, "trigger", trigger)
    assert localization.cmd_launch_localization(args) == 0
    step = orch.state.get_step("ccd", L.ID)
    assert step.status == "in_flight" and step.data["build_id"] == "123"
    assert "parameters" not in step.data and "operations" not in step.data
    assert localization.cmd_launch_localization(args) == 1
    assert len(calls) == 1


def test_launch_missing_receipt_identity_retains_owned_uncertainty(launch_provider, memory, monkeypatch):
    orch = make_orch("ccd", "localization")
    memory[0](orch)
    args = arguments("launch-localization", "--execute", "--approved-by", "reviewer")
    plan = localization.plan_localization(args, orch)
    args.review_hash = W.review_hash(orch, "ccd", L.ID, plan)
    monkeypatch.setattr(provider, "trigger", lambda *_: {"id": 123})
    assert localization.cmd_launch_localization(args) == 2
    step = orch.state.get_step("ccd", L.ID)
    assert step.execution["id"] == args.execution_id
    assert step.execution["write_review"]["hash"] == args.review_hash
    assert not step.data.get("build_id")
    assert step.status in ("blocked", "in_flight")


def test_distribution_stale_hash_zero_writes(distribution_inputs, memory, monkeypatch):
    orch = make_orch("bug_bash", "distribute_tests")
    memory[0](orch)
    args = arguments("distribute-tests", "--apply", "--no-oof", "--approved-by", "reviewer")
    args.review_hash = W.review_hash(
        orch, "bug_bash", "distribute_tests", distribute.plan_distribution(args, orch))
    distribution_inputs["case_snapshot"]["1"]["revision"] += 1
    monkeypatch.setattr(D, "set_assigned_to", lambda *a, **k: pytest.fail("stale write"))
    assert distribute.cmd_distribute_tests(args) == 1
    assert not orch.state.get_step("bug_bash", "distribute_tests").execution
