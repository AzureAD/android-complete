"""Common review authority: durable before mutation, scoped and never replayable."""
from argparse import Namespace
from copy import deepcopy
from dataclasses import asdict
import json

import pytest
import yaml

from orchestrator import cli_common as C, revision, write_review as W
from orchestrator.commands import notice
from orchestrator.engine import Orchestrator
from orchestrator.outcomes import Blocked, Done
from orchestrator.state import ReleaseState


@pytest.fixture
def reviewed(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "finalize", "name": "Finalize", "steps": [{
            "id": "wiki_payload", "name": "Payload", "kind": "external",
            "write_command": "create-payload-wiki",
        }],
    }]}), encoding="utf-8")
    state = ReleaseState(release_id="review-test", timezone="UTC")
    orch = Orchestrator(str(config), state, mocks={})
    revision.bind_initial(orch)
    path = C.state_path(str(tmp_path), state.release_id)
    state.save(path)
    args = Namespace(
        runs_root=str(tmp_path), release=state.release_id, config=str(config),
        review_hash=None, approved_by="reviewer", executor="session",
        execution_id=None, reserve=False, as_of=None,
    )
    with C.state_lock(args.runs_root, args.release):
        state, orch = C.load_orch(args.runs_root, args.release, args.config)
        plan = W.WritePlan("create-payload-wiki", {"mode": "update"}, (
            W.WriteOperation("wiki.update", {"wiki": "reviewed", "path": "/payload"},
                             {"body": "exact reviewed content"}, {"etag": "v1"}),
        ), {"version": "1.0.0"})
        args.review_hash = W.review_hash(orch, "finalize", "wiki_payload", plan)
        yield args, orch, plan, path


def authorize(fixture, planner=None):
    args, orch, plan, _ = fixture
    return W.authorize(args, orch, "finalize", "wiki_payload", planner or (lambda: plan))


def test_plan_is_deeply_immutable_and_hashes_all_semantics(reviewed):
    args, orch, plan, _ = reviewed
    value = plan.as_dict()
    value["operations"][0]["content"]["body"] = "not reviewed"
    assert plan.operations[0].content["body"] == "exact reviewed content"
    with pytest.raises(TypeError):
        plan.operations[0].target["wiki"] = "another"
    base = W.envelope(orch, "finalize", "wiki_payload", plan)
    assert base["version"] == 1
    for field, replacement in (
            ("release", "another"), ("step", "other.step"), ("generation", "next"),
            ("workflow_revision", "sha256:" + "a" * 64),
            ("command", "other-command"), ("parameters", {"mode": "create"}),
            ("preconditions", {"version": "2.0.0"}), ("operations", [])):
        changed = {**base, field: replacement}
        assert revision.digest(changed) != args.review_hash
    for field in ("target", "content", "preconditions"):
        changed = deepcopy(base)
        changed["operations"][0][field] = {"changed": True}
        assert revision.digest(changed) != args.review_hash


@pytest.mark.parametrize("field,value", [
    ("review_hash", None), ("review_hash", "sha256:" + "f" * 64),
    ("approved_by", ""), ("approved_by", None), ("execution_id", "another-owner"),
    ("release", "another-release"),
])
def test_absent_stale_or_mismatched_approval_is_mutation_free(reviewed, field, value):
    args, orch, _, path = reviewed
    before = deepcopy(asdict(orch.state))
    disk = open(path, encoding="utf-8").read()
    setattr(args, field, value)
    with pytest.raises(ValueError):
        authorize(reviewed)
    assert asdict(orch.state) == before
    assert open(path, encoding="utf-8").read() == disk


def test_generic_reservation_cannot_bypass_review(reviewed):
    _, orch, _, _ = reviewed
    result = orch.reserve_execution("finalize", "wiki_payload", "session")
    assert not result.changed and "--review-hash" in result.message
    assert not orch.step_execution("finalize", "wiki_payload")


@pytest.mark.parametrize("failure_checkpoint", [1, 2])
def test_failed_persistence_never_returns_write_permission(reviewed, failure_checkpoint):
    args, orch, _, path = reviewed
    actual = orch.state._checkpoint
    snapshots = []

    def checkpoint():
        snapshots.append(deepcopy(asdict(orch.state)))
        if len(snapshots) == failure_checkpoint:
            raise OSError("disk unavailable")
        actual()

    orch.state._checkpoint = checkpoint
    with pytest.raises(OSError, match="disk unavailable"):
        authorize(reviewed)
    saved = ReleaseState.load(path).get_step("finalize", "wiki_payload")
    assert saved.status == ("pending" if failure_checkpoint == 1 else "running")
    assert orch.state.get_step("finalize", "wiki_payload") == saved
    assert not orch._transition_kernel()._outcome_permits


def test_reservation_and_attempt_are_separate_durable_boundaries(reviewed):
    args, orch, plan, path = reviewed
    args.reserve = True
    reserved = authorize(reviewed)
    record = ReleaseState.load(path).get_step("finalize", "wiki_payload")
    assert record.status == "running"
    assert record.execution["write_review"] == {
        "hash": args.review_hash, "approved_by": "reviewer",
    }
    assert set(record.execution) == {
        "id", "owner", "started_at", "refresh", "previous_status", "write_review",
    }
    assert "exact reviewed content" not in json.dumps(asdict(ReleaseState.load(path)))
    with pytest.raises(ValueError, match="not permission"):
        reserved.validate()
    args.reserve = False
    live = authorize(reviewed)
    assert live.execution_id == reserved.execution_id
    assert ReleaseState.load(path).get_step("finalize", "wiki_payload").status == "in_flight"
    live.validate()
    assert live.plan == plan
    with pytest.raises(ValueError, match="already started"):
        authorize(reviewed)


def test_replan_after_reservation_prevents_provider_mutation(reviewed):
    _, orch, plan, path = reviewed
    second = W.WritePlan(plan.command, plan.parameters, (
        W.WriteOperation("wiki.update", {"wiki": "different"}, {"body": "different"}),
    ), plan.preconditions)
    calls = iter([plan, second])
    with pytest.raises(ValueError, match="changed after reservation"):
        authorize(reviewed, lambda: next(calls))
    assert ReleaseState.load(path).get_step("finalize", "wiki_payload").status == "running"
    assert orch.state.get_step("finalize", "wiki_payload").execution["write_review"]


def test_partial_uncertain_write_keeps_owner_until_explicit_resolution(reviewed):
    args, orch, _, path = reviewed
    live = authorize(reviewed)
    orch.settle_execution("finalize", "wiki_payload", live.execution_id,
                          Blocked("Provider timeout; page may have been updated"))
    orch.state.checkpoint()
    failed = ReleaseState.load(path).get_step("finalize", "wiki_payload")
    assert failed.status == "blocked" and failed.execution["id"] == live.execution_id
    assert "last_write_review" not in failed.data
    assert not orch.reopen("finalize", "wiki_payload").changed
    assert orch.reopen("finalize", "wiki_payload", "Inspected provider: original page unchanged").changed
    closed = orch.state.get_step("finalize", "wiki_payload")
    assert closed.status == "pending" and closed.execution is None
    assert closed.data["last_write_review"] == {
        "execution_id": live.execution_id, "hash": args.review_hash, "approved_by": "reviewer",
        "approved_at": failed.execution["started_at"],
        "workflow_revision": revision.revision_id(orch.state.workflow_revision),
    }
    with pytest.raises(ValueError):
        live.validate()
    with pytest.raises(ValueError):
        authorize(reviewed)


def test_closed_review_survives_result_data_replacement_and_invalidation(reviewed):
    args, orch, _, _ = reviewed
    live = authorize(reviewed)
    orch.settle_execution("finalize", "wiki_payload", live.execution_id,
                          Done("Page verified"), data={"provider_note": "verified"})
    closed = orch.state.get_step("finalize", "wiki_payload").data["last_write_review"]
    assert closed["hash"] == args.review_hash
    assert closed["execution_id"] == live.execution_id
    assert not orch.annotate_step(
        "finalize", "wiki_payload", data={"last_write_review": {"hash": "forged"}}).changed
    assert orch.reopen("finalize", "wiki_payload", "Owner requested new page review").changed
    assert orch.state.get_step("finalize", "wiki_payload").data["last_write_review"] == closed
    with pytest.raises(ValueError):
        live.validate()


def test_latest_receipt_changes_only_when_next_reviewed_execution_closes(reviewed):
    args, orch, plan, _ = reviewed
    first = authorize(reviewed)
    orch.settle_execution("finalize", "wiki_payload", first.execution_id, Done("First verified"))
    previous = deepcopy(orch.state.get_step("finalize", "wiki_payload").data["last_write_review"])
    assert orch.reopen("finalize", "wiki_payload", "Review another exact update").changed
    args.execution_id = None
    args.review_hash = W.review_hash(orch, "finalize", "wiki_payload", plan)
    second = authorize(reviewed)
    assert orch.state.get_step("finalize", "wiki_payload").data["last_write_review"] == previous
    orch.settle_execution("finalize", "wiki_payload", second.execution_id, Done("Second verified"))
    latest = orch.state.get_step("finalize", "wiki_payload").data["last_write_review"]
    assert latest["execution_id"] == second.execution_id != previous["execution_id"]
    assert latest["hash"] == args.review_hash != previous["hash"]


@pytest.mark.parametrize("resolution", ["done", "skip"])
def test_explicit_owner_closure_retains_authorization_not_provider_success(reviewed, resolution):
    _, orch, _, _ = reviewed
    live = authorize(reviewed)
    if resolution == "done":
        result = orch.complete_step("finalize", "wiki_payload", "Owner verified provider page and content")
    else:
        result = orch._transition_kernel().skip(
            "finalize", "wiki_payload", "Owner verified no further writes needed",
            execution_id=live.execution_id)
    assert result.kind == "ran"
    record = orch.state.get_step("finalize", "wiki_payload")
    assert record.execution is None
    assert record.data["last_write_review"]["execution_id"] == live.execution_id


def test_generic_record_cannot_certify_a_reviewed_write(reviewed):
    args, orch, _, _ = reviewed
    authorize(reviewed)
    args.phase, args.step, args.status, args.detail = "finalize", "wiki_payload", "pass", "pretend"
    before = deepcopy(asdict(orch.state))
    assert notice.cmd_record_step(args) == 1
    assert asdict(orch.state) == before


def test_permit_detects_owner_or_generation_change(reviewed):
    _, orch, _, _ = reviewed
    live = authorize(reviewed)
    record = orch.state.get_step("finalize", "wiki_payload")
    record.execution["owner"] = "another-session"
    orch.state.set_step("finalize", "wiki_payload", record)
    with pytest.raises(ValueError, match="generation changed"):
        live.validate()


def test_closed_review_requires_evidence_to_reopen(reviewed):
    _, orch, _, _ = reviewed
    live = authorize(reviewed)
    orch.settle_execution("finalize", "wiki_payload", live.execution_id, Done("Verified provider"))
    before = deepcopy(asdict(orch.state))
    assert not orch.reopen("finalize", "wiki_payload").changed
    assert asdict(orch.state) == before


def test_typed_step_evidence_cannot_drop_or_forge_closed_authorization(reviewed):
    from orchestrator.authority import EvidenceAuthority, OwnStepData
    from orchestrator.context_boundary import EvidenceSession
    from orchestrator.evidence import StepData

    args, orch, plan, _ = reviewed
    live = authorize(reviewed)
    orch.settle_execution("finalize", "wiki_payload", live.execution_id, Done("First verified"))
    receipt = deepcopy(orch.state.get_step("finalize", "wiki_payload").data["last_write_review"])
    assert orch.reopen("finalize", "wiki_payload", "Owner reviewed another update").changed
    args.execution_id = None
    args.review_hash = W.review_hash(orch, "finalize", "wiki_payload", plan)
    live = authorize(reviewed)
    session = EvidenceSession(
        orch.state, live._permit, orch.validate_evidence_permit,
        authority=EvidenceAuthority((OwnStepData(),)), durable=False)
    session.apply((StepData({"domain": "observed"}),))
    assert orch.state.get_step("finalize", "wiki_payload").data["last_write_review"] == receipt
    before = deepcopy(asdict(orch.state))
    with pytest.raises(ValueError, match="authorization"):
        session.apply((StepData({"last_write_review": {**receipt, "approved_by": "forged"}}),))
    assert asdict(orch.state) == before


@pytest.mark.parametrize("command", [["az", "repos", "show"], ["gh", "api", "repos/a/b"],
                                    ["workiq", "ask"], ["curl", "https://example.test"], "az version"])
def test_offline_guard_blocks_unmocked_provider_processes(command):
    from tests.conftest import _offline_process
    with pytest.raises(RuntimeError, match="REAL"):
        _offline_process(lambda *args, **kwargs: pytest.fail("provider process invoked"), command)


def test_offline_git_guard_allows_only_fixture_transport():
    from tests.conftest import _offline_process
    before = {"GIT_ALLOW_PROTOCOL": "https:ssh", "OTHER": "value"}
    actual = _offline_process(lambda *args, **kwargs: kwargs["env"],
                              ["git", "push", "https://example.test/repo"], env=before)
    assert actual == {"GIT_ALLOW_PROTOCOL": "file", "OTHER": "value"}
    assert before["GIT_ALLOW_PROTOCOL"] == "https:ssh"
