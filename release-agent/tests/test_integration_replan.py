"""Offline exact Broker suffix extensions, including same-owner crash fencing."""
from copy import deepcopy
import json

import pytest

from orchestrator import integration_recovery as R, integration_replan as B, revision
from test_integration_recovery import recovery, integration, checkout, execute
from test_git_write_review import git, commit


NEW = "f" * 40


@pytest.fixture
def broker(recovery, monkeypatch):
    s = recovery
    plan = deepcopy(s.original)
    repo = plan["preconditions"]["repos"][0]
    repo["target"].update(key="broker", gh_repo="msft.ghe.com/security/ad-accounts-for-android")
    plan["parameters"]["repos"] = ["broker"]
    for op in plan["operations"][1:]:
        op["target"].update(repo["target"])
    original_hash = revision.digest(plan)
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = original_hash
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump({"plan": plan, "review_hash": original_hash, "permission_to_execute": False}, stream)
    s.states.update({(repo["target"]["gh_repo"], key[1], key[2]): s.states.pop(key)
                     for key in list(s.states)})
    s.original = plan
    old = R.preview(s.orch, s.args)
    record = s.orch.state.get_step("finalize", "integ_prs")
    record.data[R.DATA_KEY] = {
        "original_hash": original_hash, "execution_id": record.execution["id"],
        "approvals": [{"hash": old["recovery_hash"], "approved_by": "prior", "at": "old"}],
        "receipts": deepcopy(old["plan"]["receipts"])}
    s.orch.state.set_step("finalize", "integ_prs", record)
    fresh = deepcopy(s.content)
    fresh.update(tree="1" * 40, parents=[s.checkout.head, NEW], commit_id="2" * 40,
                 commit="new exact commit bytes")
    monkeypatch.setattr(R.G, "plan_ri", lambda root, head, base, target: deepcopy(fresh))
    monkeypatch.setattr(R.G, "_git", lambda root, command, *args, **kwargs:
                        (s.checkout.base if command == "merge-base" else
                         "new source commit\n" if command == "log" else "exact target diff\n").encode())
    s.tips["dev"] = NEW
    s.args.broker_target_tip = NEW
    s.new_content = fresh
    return s


def test_preview_exact_delta_is_read_only_and_original_is_immutable(broker):
    s = broker
    before = deepcopy(s.orch.state.steps)
    artifact = open(s.args.original_preview, "rb").read()
    reviewed = R.preview(s.orch, s.args)
    extension = reviewed["plan"]["replans"][-1]
    change = extension["change"]
    assert change["changed_operations"] == [2, 3]
    assert change["before_target"] == s.checkout.base and change["after_target"] == NEW
    assert change["after_content"] == s.new_content
    assert extension["approval_hash"] is None
    assert len(reviewed["plan"]["remaining"]) == 2
    assert reviewed["plan"]["original_review"]["hash"] == revision.digest(s.original)
    assert s.orch.state.steps == before and not s.calls
    assert open(s.args.original_preview, "rb").read() == artifact


def test_fresh_approval_executes_only_suffix_preserving_original_owner_and_receipts(broker):
    s = broker
    initial = deepcopy(s.orch.state.get_step("finalize", "integ_prs"))
    s.approve()
    execute(s)
    record = s.orch.state.get_step("finalize", "integ_prs")
    journal = record.data[R.DATA_KEY]
    assert record.status == "done"
    assert journal["receipts"][:2] == initial.data[R.DATA_KEY]["receipts"][:2]
    assert journal["approvals"][:1] == initial.data[R.DATA_KEY]["approvals"]
    assert journal["replans"][-1]["approval_hash"] == s.args.recovery_hash
    assert journal["original_hash"] == initial.execution["write_review"]["hash"]
    assert [call[0] for call in s.calls] == ["push", "create"]


@pytest.mark.parametrize("change", ["applied", "uncertain", "original-fence", "stale", "rebase",
                                   "different-tip", "wrong-repository", "other-source"])
def test_replan_rejects_non_suffix_or_unapproved_drift(broker, monkeypatch, change):
    s = broker
    s.approve()
    raw = s.orch.state.steps["finalize.integ_prs"]
    if change in ("applied", "uncertain"):
        raw["data"][R.DATA_KEY]["receipts"][2]["status"] = "applied" if change == "applied" else "in_flight"
    elif change == "original-fence":
        raw["data"]["integration_write_journal"] = {"operations": {"2": {"status": "in_flight"}}}
    elif change == "stale":
        s.args.recovery_hash = raw["data"][R.DATA_KEY]["approvals"][0]["hash"]
    elif change == "rebase":
        monkeypatch.setattr(R.G, "_git", lambda *a, **kw: b"9" * 40)
    elif change == "different-tip":
        s.tips["dev"] = "8" * 40
    elif change == "other-source":
        s.tips["working/release/1"] = "7" * 40
    else:
        s.original["preconditions"]["repos"][0]["target"]["gh_repo"] = "other/repo"
        with pytest.raises(ValueError):
            B.effective(s.orch.state.get_step("finalize", "integ_prs"), s.original, NEW)
        return
    with pytest.raises(ValueError):
        execute(s)
    assert not s.calls


def test_replan_crash_fence_is_not_replayed_on_absence(broker, monkeypatch):
    s = broker
    s.approve()
    monkeypatch.setattr(R.G, "push_reviewed",
                        lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        execute(s)
    record = s.orch.state.get_step("finalize", "integ_prs")
    assert record.data[R.DATA_KEY]["receipts"][2]["status"] == "in_flight"
    with pytest.raises(ValueError, match="blind replay"):
        R.preview(s.orch, s.args)
    assert not s.calls


def test_second_replan_requires_exact_prior_chain_and_approval(broker):
    s = broker
    s.approve()
    original = R.G.push_reviewed
    R.G.push_reviewed = lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt())
    try:
        with pytest.raises(KeyboardInterrupt):
            execute(s)
    finally:
        R.G.push_reviewed = original
    journal = s.orch.state.steps["finalize.integ_prs"]["data"][R.DATA_KEY]
    journal["replans"][0]["change"]["after_target"] = "8" * 40
    with pytest.raises(ValueError, match="provenance"):
        R.preview(s.orch, s.args)


def test_repeated_pending_suffix_replan_requires_and_retains_prior_approval(broker, monkeypatch):
    s = broker
    reviewed = R.preview(s.orch, s.args)
    kernel = s.orch._transition_kernel()
    permit = kernel.begin_integration_recovery(
        s.args.execution_id, reviewed["plan"], reviewed["recovery_hash"], "first-reviewer")
    kernel._outcome_permits.pop(permit)
    first = deepcopy(s.orch.state.get_step("finalize", "integ_prs").data[R.DATA_KEY])
    after = "9" * 40
    s.args.broker_target_tip = after
    s.tips["dev"] = after
    def git_read(root, command, *args, **kw):
        if command == "merge-base":
            return args[0].encode()
        if args[-1].endswith(NEW) or args[-1] == NEW:
            return b"new source commit\n" if command == "log" else b"exact target diff\n"
        return b"next target change\n"
    monkeypatch.setattr(R.G, "_git", git_read)
    fresh = R.preview(s.orch, s.args)
    chain = fresh["plan"]["replans"]
    assert len(chain) == 2 and chain[0] == first["replans"][0]
    assert chain[1]["approval_hash"] is None
    assert chain[1]["change"]["before_plan_hash"] == first["effective_plan_hash"]
    assert chain[1]["change"]["before_target"] == NEW
    assert fresh["recovery_hash"] != reviewed["recovery_hash"]
    assert not s.calls


def test_merge_conflicts_and_changed_effective_identity_are_held(broker, monkeypatch):
    s = broker
    content = deepcopy(s.new_content)
    content["resolved_gradle_conflicts"] = ["build.gradle"]
    monkeypatch.setattr(R.G, "plan_ri", lambda *a, **kw: content)
    with pytest.raises(ValueError, match="conflict-free"):
        R.preview(s.orch, s.args)
    assert not s.calls


def test_real_git_replan_includes_fast_forward_tree_and_preserves_metadata(checkout):
    s = checkout
    old = R.G.plan_ri(str(s.root), s.head, s.base, "dev")
    target = {"key": "broker", "tool": "gh", "gh_repo": "msft.ghe.com/security/ad-accounts-for-android",
              "root": str(s.root), "head": "release-integration/1", "base": "dev"}
    push = {"kind": "integration.push", "target": target, "content": old,
            "preconditions": {"head_tip": s.head, "base_tip": s.base}}
    create = {"kind": "integration.create", "target": deepcopy(target),
              "content": {"title": "frozen", "body": "AB#101", "labels": ["frozen"]},
              "preconditions": {"head_tip": old["commit_id"], "base_tip": s.base}}
    plan = {"operations": [push, create], "preconditions": {"repos": [
        {"target": target, "tips": {"dev": s.base, "release-integration/1": s.head}, "ri": old}]}}
    (s.root / "new-test.txt").write_text("deterministic\n")
    after = commit(s.root, "deterministic test")
    updated, proof = B._extend(deepcopy(plan), after)
    assert updated["operations"][1]["content"] == create["content"]
    assert updated["operations"][0]["content"]["parents"] == [s.head, after]
    assert "new-test.txt" in updated["operations"][0]["content"]["edits"]
    assert "deterministic test" in proof["target_commits"]
    assert "new-test.txt" in proof["target_diff"]
    assert not git(s.root, "status", "--porcelain")
    assert git(s.root, "rev-parse", "release-integration/1") == s.head
    with pytest.raises(ValueError, match="fast-forward"):
        B._extend(deepcopy(plan), s.head)
