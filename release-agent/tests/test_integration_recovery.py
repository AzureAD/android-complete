"""Offline crash/reconciliation contracts; never use live release/provider state."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from orchestrator import cli_common as C, integration_recovery as R, revision, write_review as W
from orchestrator.commands import integ_prs_cmd as IC
from orchestrator.outcomes import Done
from orchestrator.state import ReleaseState
from tools import integration_receipts as reads, prs as PR
from test_git_write_review import integration, checkout, bound_command, P


REAL_PUSH = R.G.push_reviewed


@pytest.fixture
def recovery(integration, tmp_path, monkeypatch):
    s = integration
    _, args, orch, path = bound_command(tmp_path, monkeypatch, "integ_prs")
    original = P.integration_plan(orch.context("finalize", "integ_prs"), args)
    args.review_hash = W.review_hash(orch, "finalize", "integ_prs", original)
    args.execute = True
    artifact = W.preview(orch, "finalize", "integ_prs", original)
    args.original_preview = str(tmp_path / "original.json")
    with open(args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream)
    args.existing_pbi = "101"
    args.recovery_hash = None
    args.confirm_no_inflight = True
    with C.state_lock(args.runs_root, args.release):
        C.load_orch(args.runs_root, args.release, args.config)
        auth = W.authorize(args, orch, "finalize", "integ_prs", lambda: original)
        orch.record_scout_step("finalize", "integ_prs", "attention", "legacy partial writer",
                               execution_id=auth.execution_id)
        C.save_state(orch.state, args.runs_root, args.release)
    s.args, s.orch, s.path, s.original = args, orch, path, artifact["plan"]
    started = orch.step_execution("finalize", "integ_prs")["started_at"]

    def pbi(target, wid, title, started_at):
        assert wid == "101" and started_at == started
        return {"id": wid, **target, "title": title, "type": "Product Backlog Item",
                "created_at": started, "url": "https://example.test/pbi/101"}

    def pr(target):
        value = deepcopy(s.states.get((target["gh_repo"], target["head"], target["base"])))
        if value is None:
            return None
        return {**value, "head": target["head"], "base": target["base"],
                "head_tip": P._tip(target, target["head"]),
                "state": "open", "repository": target["gh_repo"], "created_at": started}

    monkeypatch.setattr(reads, "pbi_receipt", pbi)
    monkeypatch.setattr(reads, "pr_receipt", pr)
    # Legacy partial: the PBI and freeze PR exist, but RI push and integration PR do not.
    op = artifact["plan"]["operations"][1]
    PR.gh_create_pr(op["target"]["gh_repo"], op["target"]["head"], op["target"]["base"],
                    op["content"]["title"], R._body(op, "101"), op["content"]["labels"])
    s.calls.clear()
    s.approve = lambda: setattr(args, "recovery_hash", R.preview(orch, args)["recovery_hash"])
    return s


def execute(s):
    with C.state_lock(s.args.runs_root, s.args.release):
        return R.execute(s.orch, s.args)


def test_preview_is_read_only_and_reports_only_unapplied_suffix(recovery):
    s = recovery
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    result = R.preview(s.orch, s.args)
    assert result["permission_to_execute"] is False
    assert [op["kind"] for op in result["plan"]["remaining"]] == ["integration.push", "integration.create"]
    assert [r["status"] for r in result["plan"]["receipts"]] == ["applied", "applied", "pending", "pending"]
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk and not s.calls


@pytest.fixture
def external_merge(recovery, monkeypatch):
    s = recovery
    initial = R.preview(s.orch, s.args)
    record = s.orch.state.get_step("finalize", "integ_prs")
    record.data[R.DATA_KEY] = {
        "original_hash": record.execution["write_review"]["hash"],
        "execution_id": record.execution["id"], "approvals": [],
        "receipts": deepcopy(initial["plan"]["receipts"]),
    }
    s.orch.state.set_step("finalize", "integ_prs", record)
    original_read = reads.pr_receipt
    s.merge_tip = "f" * 40
    s.tips["release/1"] = s.merge_tip

    def read(target):
        value = original_read(target)
        if value and target["base"] == "release/1":
            value.update(state="merged", merge_tip=s.merge_tip,
                         merged_at=value["created_at"])
        return value

    monkeypatch.setattr(reads, "pr_receipt", read)
    monkeypatch.setattr(R.G, "_git", lambda root, command, ancestor, merge:
                        ancestor.encode())
    return s


def test_external_merge_of_checkpointed_pr_is_explicit_read_only_evidence(external_merge):
    s = external_merge
    before = deepcopy(s.orch.state.steps)
    result = R.preview(s.orch, s.args)
    assert result["plan"]["receipts"][1]["evidence"]["state"] == "merged"
    assert result["plan"]["external_merges_requiring_approval"][0]["index"] == 1
    assert len(result["plan"]["remaining"]) == 2
    assert s.orch.state.steps == before and not s.calls


def test_recovery_preview_and_execution_support_python39_zip(external_merge, monkeypatch):
    s = external_merge
    monkeypatch.setattr(R, "zip", lambda *iterables: zip(*iterables), raising=False)
    s.approve()
    execute(s)
    assert s.orch.state.get_step("finalize", "integ_prs").status == "done"
    assert [call[0] for call in s.calls] == ["push", "create"]


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_recovery_rejects_mismatched_receipt_counts(recovery, change):
    s = recovery
    receipts = R.preview(s.orch, s.args)["plan"]["receipts"]
    record = s.orch.state.get_step("finalize", "integ_prs")
    record.data[R.DATA_KEY] = {
        "original_hash": record.execution["write_review"]["hash"],
        "execution_id": record.execution["id"], "approvals": [],
        "receipts": receipts[:-1] if change == "missing" else receipts + [deepcopy(receipts[-1])],
    }
    s.orch.state.set_step("finalize", "integ_prs", record)
    before = deepcopy(asdict(s.orch.state))
    with pytest.raises(ValueError, match="receipt count"):
        R.preview(s.orch, s.args)
    assert asdict(s.orch.state) == before and not s.calls


@pytest.mark.parametrize("change", ["uncheckpointed", "unrelated-base", "wrong-ancestry",
                                   "changed-content", "wrong-merge-date", "wrong-pr"])
def test_external_merge_cannot_hide_other_changes(external_merge, monkeypatch, change):
    s = external_merge
    if change == "uncheckpointed":
        s.orch.state.steps["finalize.integ_prs"]["data"][R.DATA_KEY]["receipts"][1]["status"] = "pending"
    elif change == "unrelated-base":
        s.tips["release/1"] = "9" * 40
    elif change == "wrong-ancestry":
        monkeypatch.setattr(R.G, "_git", lambda *a, **kw: b"9" * 40)
    else:
        original_read = reads.pr_receipt
        def read(target):
            value = original_read(target)
            if value and value["state"] == "merged":
                if change == "changed-content":
                    value["body"] += "unreviewed"
                elif change == "wrong-pr":
                    value["number"] += 1
                else:
                    value["merged_at"] = "2000-01-01T00:00:00Z"
            return value
        monkeypatch.setattr(reads, "pr_receipt", read)
    with pytest.raises(ValueError):
        R.preview(s.orch, s.args)
    assert not s.calls


def test_external_merge_receipt_is_rechecked_before_each_remaining_write(external_merge):
    s = external_merge
    s.approve()
    execute(s)
    assert [call[0] for call in s.calls] == ["push", "create"]
    stored = s.orch.state.get_step("finalize", "integ_prs")
    assert stored.status == "done"
    assert stored.data[R.DATA_KEY]["receipts"][1]["evidence"]["state"] == "merged"


@pytest.mark.parametrize("change", [None, "wrong-parent", "wrong-tree", "wrong-content",
                                   "extra-label", "removed-label"])
def test_msal_squash_requires_exact_tree_base_and_reviewed_board_edits(recovery, monkeypatch, change):
    s = recovery
    op = deepcopy(s.original["operations"][1])
    op["target"]["gh_repo"] = R.MSAL
    before = R.preview(s.orch, s.args)["plan"]["receipts"][1]
    before["evidence"]["repository"] = R.MSAL
    after = deepcopy(before)
    value = after["evidence"]
    value.update(state="merged", merge_tip="f" * 40, merged_at=value["created_at"])
    value["title"] += ", Fixes AB#101"
    value["body"] = value["body"].replace(
        "AB#101", "[AB#101](" + R.PBI_LINK + "101)").replace("\n", "\r\n")
    value["labels"] = sorted(set(value["labels"]) | {"changelog:none"})
    if change == "wrong-content":
        value["body"] += "unreviewed"
    elif change == "extra-label":
        value["labels"].append("bypass")
    elif change == "removed-label":
        value["labels"] = []

    def git(root, command, *args):
        base = op["preconditions"]["base_tip"]
        if command == "merge-base":
            return base.encode()
        if args[0].endswith("^"):
            return ("9" * 40 if change == "wrong-parent" else base).encode()
        return ("9" * 40 if change == "wrong-tree" and args[0].startswith("f" * 40)
                else "a" * 40).encode()
    monkeypatch.setattr(R.G, "_git", git)
    if change == "wrong-content":
        with pytest.raises(ValueError):
            R._merged_receipt_matches(before, after, op, "101")
    else:
        assert R._merged_receipt_matches(before, after, op, "101") is (change is None)


def test_partial_resume_preserves_execution_and_original_approval(recovery):
    s = recovery
    execution = deepcopy(s.orch.step_execution("finalize", "integ_prs"))
    s.approve()
    assert "receipt-backed" in execute(s)
    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert record.status == "done" and record.execution is None
    assert all(record.data["last_write_review"][key] == value for key, value in
               {"execution_id": execution["id"], **execution["write_review"]}.items())
    recovery = record.data[R.DATA_KEY]
    assert recovery["execution_id"] == execution["id"]
    assert recovery["original_hash"] == execution["write_review"]["hash"]
    assert recovery["approvals"][-1]["hash"] == s.args.recovery_hash
    assert all(r["status"] == "applied" for r in recovery["receipts"])
    assert [call[0] for call in s.calls] == ["push", "create"]
    assert len(json.dumps(record.data)) < 20000


def test_exact_four_repo_partial_prefix_resumes_only_six_prs_and_three_pushes(recovery, monkeypatch):
    from pathlib import Path
    s = recovery
    template = deepcopy(s.original)
    plan, branches, remotes, keys = deepcopy(template), {}, {}, {}
    plan["operations"] = [deepcopy(template["operations"][0])]
    plan["preconditions"]["repos"] = []
    plan["parameters"]["repos"] = ["common", "msal", "broker", "authenticator"]
    for key in plan["parameters"]["repos"]:
        repo = deepcopy(template["preconditions"]["repos"][0])
        target = repo["target"]
        target.update(key=key, root=str(s.checkout.root.parent / ("checkout-" + key)),
                      remote="https://example.test/" + key + ".git", gh_repo="offline/" + key)
        plan["preconditions"]["repos"].append(repo)
        branches[key] = deepcopy(repo["tips"])
        remotes[target["root"]], keys[target["root"]] = target["remote"], key
        for original in template["operations"][1:]:
            op = deepcopy(original)
            op["target"].update(target)
            plan["operations"].append(op)
    artifact = {"plan": plan, "review_hash": revision.digest(plan), "permission_to_execute": False}
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = artifact["review_hash"]
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream)
    monkeypatch.setattr(R, "_tip", lambda target, name: branches[target["key"]][name])
    monkeypatch.setattr(R.G, "clean_repository", lambda root: Path(root))
    monkeypatch.setattr(R.G, "remote_url", lambda root: remotes[root])
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: [target["remote"]])
    started = s.orch.step_execution("finalize", "integ_prs")["started_at"]
    def read_pr(target):
        value = deepcopy(s.states.get((target["gh_repo"], target["head"], target["base"])))
        return ({**value, "head": target["head"], "base": target["base"],
                 "head_tip": branches[target["key"]][target["head"]], "state": "open",
                 "repository": target["gh_repo"], "created_at": started} if value else None)
    monkeypatch.setattr(reads, "pr_receipt", read_pr)
    def push(root, remote, name, expected, content, validate):
        validate()
        key = keys[root]
        assert branches[key][name] == expected and remotes[root] == remote
        branches[key][name] = content["commit_id"]
        s.calls.append(("push", key))
    monkeypatch.setattr(R.G, "push_reviewed", push)
    s.states.clear()
    for op in (plan["operations"][1], plan["operations"][3]):
        target, content = op["target"], op["content"]
        PR.gh_create_pr(target["gh_repo"], target["head"], target["base"], content["title"],
                        R._body(op, "101"), content["labels"])
    branches["common"][plan["operations"][2]["target"]["head"]] = plan["operations"][2]["content"]["commit_id"]
    s.calls.clear()
    reviewed = R.preview(s.orch, s.args)
    assert len(reviewed["plan"]["remaining"]) == 9
    assert [r["status"] for r in reviewed["plan"]["receipts"]][:4] == ["applied"] * 4
    s.approve()
    execute(s)
    assert sum(call[0] == "create" for call in s.calls) == 6
    assert sum(call[0] == "push" for call in s.calls) == 3
    assert not any(call[0] == "pbi" for call in s.calls)
    assert s.orch.state.get_step("finalize", "integ_prs").status == "done"


@pytest.mark.parametrize("field,value", [
    ("execution_id", "other"), ("executor", "other"), ("existing_pbi", "102"),
    ("recovery_hash", "sha256:" + "0" * 64), ("approved_by", ""), ("confirm_no_inflight", False),
])
def test_wrong_identity_or_missing_manual_authorization_cannot_write(recovery, field, value):
    s = recovery
    s.approve()
    setattr(s.args, field, value)
    with pytest.raises((ValueError, AssertionError)):
        execute(s)
    assert not s.calls


def test_original_artifact_tampering_and_diagnostic_plan_are_not_permission(recovery):
    s = recovery
    s.approve()
    value = json.loads(open(s.args.original_preview, encoding="utf-8").read())
    value["plan"]["operations"][1]["content"]["title"] += " tampered"
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
    with pytest.raises(ValueError, match="frozen preview"):
        execute(s)
    assert not s.calls


@pytest.mark.parametrize("change", ["title", "body", "labels", "tip", "pbi", "hole"])
def test_provider_drift_fails_closed(recovery, monkeypatch, change):
    s = recovery
    s.approve()
    value = next(iter(s.states.values()))
    if change in ("title", "body"):
        value[change] += " unauthorized"
    elif change == "labels":
        value["labels"] = []
    elif change == "tip":
        s.tips["dev"] = "9" * 40
    elif change == "pbi":
        monkeypatch.setattr(reads, "pbi_receipt",
                            lambda *a: (_ for _ in ()).throw(ValueError("ambiguous PBI")))
    else:
        s.states.clear()
        s.tips["release-integration/1"] = s.content["commit_id"]
    with pytest.raises(ValueError):
        execute(s)
    assert not s.calls


def test_narrow_bot_difference_is_displayed_and_needs_fresh_manual_hash(recovery):
    s = recovery
    s.approve()
    before_hash = s.args.recovery_hash
    # The exception is intentionally restricted to the real Common hosting identity.
    for repo in s.original["preconditions"]["repos"]:
        repo["target"]["gh_repo"] = R.COMMON
    for operation in s.original["operations"][1:]:
        operation["target"]["gh_repo"] = R.COMMON
    artifact = {"plan": s.original, "review_hash": revision.digest(s.original),
                "permission_to_execute": False}
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = artifact["review_hash"]
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream)
    key, value = s.states.popitem()
    value["title"] += ", Fixes AB#101"
    value["body"] = value["body"].replace("AB#101", "[AB#101](" + R.PBI_LINK + "101)")
    s.states[(R.COMMON, key[1], key[2])] = value
    preview = R.preview(s.orch, s.args)
    assert len(preview["plan"]["content_changes_requiring_approval"]) == 1
    change = preview["plan"]["content_changes_requiring_approval"][0]
    assert change["before"]["title"] != change["after"]["title"]
    assert preview["recovery_hash"] != before_hash
    with pytest.raises(ValueError, match="stale"):
        execute(s)
    assert not s.calls


def test_crash_after_fence_without_remote_effect_cannot_replay(recovery, monkeypatch):
    s = recovery
    s.approve()
    monkeypatch.setattr(R.G, "push_reviewed", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        execute(s)
    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert record.execution["id"] == s.args.execution_id
    assert record.data[R.DATA_KEY]["receipts"][2]["status"] == "in_flight"
    with pytest.raises(ValueError, match="blind replay"):
        R.preview(s.orch, s.args)
    assert not s.calls


def test_post_write_checkpoint_failure_can_only_be_reconciled_with_new_review(recovery, monkeypatch):
    s = recovery
    s.approve()
    old = s.args.recovery_hash
    checkpoint = s.orch.state._checkpoint
    count = 0

    def fail():
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("disk failure after remote push")
        checkpoint()
    monkeypatch.setattr(s.orch.state, "_checkpoint", fail)
    with pytest.raises(OSError):
        execute(s)
    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert record.data[R.DATA_KEY]["receipts"][2]["status"] == "in_flight"
    assert s.orch.state.get_step("finalize", "integ_prs").data == record.data
    assert [call[0] for call in s.calls] == ["push"]
    with pytest.raises(ValueError, match="stale"):
        execute(s)
    s.approve()
    assert s.args.recovery_hash != old
    execute(s)
    assert [call[0] for call in s.calls] == ["push", "create"]


def test_created_pr_with_lost_response_is_reconciled_not_created_again(recovery, monkeypatch):
    s = recovery
    create = PR.gh_create_pr
    def lost_response(*args, **kwargs):
        create(*args, **kwargs)
        return False, "", "connection lost after acceptance"
    monkeypatch.setattr(PR, "gh_create_pr", lost_response)
    s.approve()
    with pytest.raises(ValueError, match="uncertain"):
        execute(s)
    saved = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    assert saved.data[R.DATA_KEY]["receipts"][3]["status"] == "in_flight"
    assert [call[0] for call in s.calls] == ["push", "create"]
    pending = R.preview(s.orch, s.args)
    assert pending["plan"]["remaining"] == []
    s.approve()
    execute(s)
    assert [call[0] for call in s.calls] == ["push", "create"]


def test_archived_prewrite_review_never_replaces_active_authority(recovery):
    s = recovery
    s.orch.state.steps["finalize.integ_prs"]["data"]["last_write_review"] = {
        "execution_id": "retired-prewrite", "hash": "sha256:" + "0" * 64,
        "approved_by": "old-reviewer"}
    result = R.preview(s.orch, s.args)
    assert result["plan"]["original_review"]["hash"] == s.args.review_hash
    s.args.execution_id = "retired-prewrite"
    with pytest.raises(ValueError, match="exact fenced"):
        R.preview(s.orch, s.args)


def test_initial_checkpoint_failure_restores_entire_snapshot_and_permits(recovery, monkeypatch):
    s = recovery
    s.approve()
    before, disk = deepcopy(asdict(s.orch.state)), s.path.read_bytes()
    monkeypatch.setattr(s.orch.state, "_checkpoint",
                        lambda: (_ for _ in ()).throw(OSError("cannot fence")))
    with pytest.raises(OSError):
        execute(s)
    assert asdict(s.orch.state) == before and s.path.read_bytes() == disk
    assert not s.orch._transition_kernel()._outcome_permits and not s.calls


def test_generic_paths_cannot_complete_or_replace_receipts(recovery, monkeypatch):
    s = recovery
    s.approve()
    monkeypatch.setattr(R.G, "push_reviewed", lambda *a: (_ for _ in ()).throw(ValueError("held")))
    with pytest.raises(ValueError):
        execute(s)
    kernel = s.orch._transition_kernel()
    assert not kernel.settle_execution("finalize", "integ_prs", s.args.execution_id, Done("pretend")).changed
    assert not kernel.complete("finalize", "integ_prs", "pretend").changed
    assert not kernel.skip("finalize", "integ_prs", "pretend", execution_id=s.args.execution_id).changed
    assert not kernel.reopen("finalize", "integ_prs", "pretend").changed
    assert not kernel.annotate_step("finalize", "integ_prs", data={R.DATA_KEY: {}}).changed
    from orchestrator.outcomes import Blocked
    assert not kernel.settle_execution("finalize", "integ_prs", s.args.execution_id,
                                       Blocked("drop receipts"), data={R.DATA_KEY: {}}).changed
    assert not kernel.complete_integration_recovery(None).changed
    assert s.orch.step_execution("finalize", "integ_prs")["id"] == s.args.execution_id


def test_legacy_owned_execution_cannot_use_generic_success_or_clear(recovery):
    s = recovery
    kernel = s.orch._transition_kernel()
    assert not kernel.settle_execution("finalize", "integ_prs", s.args.execution_id, Done("pretend")).changed
    assert not kernel.complete("finalize", "integ_prs", "pretend").changed
    assert not kernel.skip("finalize", "integ_prs", "pretend", execution_id=s.args.execution_id).changed
    assert not kernel.reopen("finalize", "integ_prs", "pretend").changed


@pytest.mark.parametrize("value", [None, "", "2000-01-01T00:00:00Z"])
@pytest.mark.parametrize("with_journal", [False, True])
def test_annotations_cannot_clear_or_replace_active_integration_fence(recovery, value, with_journal):
    s = recovery
    if with_journal:
        record = s.orch.state.get_step("finalize", "integ_prs")
        record.data["integration_write_journal"] = {
            "execution_id": s.args.execution_id, "original_hash": s.args.review_hash,
            "operations": {"0": {
                "operation_hash": revision.digest(s.original["operations"][0]),
                "status": "in_flight", "evidence": None}},
        }
        s.orch.state.set_step("finalize", "integ_prs", record)
    before = deepcopy(asdict(s.orch.state))
    kernel = s.orch._transition_kernel()
    assert not kernel.annotate_step(
        "finalize", "integ_prs", data={"in_flight_since": value}).changed
    assert not kernel.skip(
        "finalize", "integ_prs", "abandon uncertain writes", execution_id=s.args.execution_id).changed
    assert not kernel.reopen("finalize", "integ_prs", "replace uncertain writes").changed
    assert asdict(s.orch.state) == before and not s.calls
    assert kernel.annotate_step("finalize", "integ_prs", note="Owner is checking receipts").changed


@pytest.mark.parametrize("completion", ["normal", "recovery"])
def test_reopened_integration_archives_completed_journals_before_new_reservation(recovery, completion):
    s = recovery
    if completion == "normal":
        s.orch.state.steps.clear()
        s.args.execution_id = None
        plan = P.integration_plan(s.orch.context("finalize", "integ_prs"), s.args)
        s.args.review_hash = W.review_hash(s.orch, "finalize", "integ_prs", plan)
        with C.state_lock(s.args.runs_root, s.args.release):
            assert IC.cmd_create_integration_prs(s.args) == 0
    else:
        record = s.orch.state.get_step("finalize", "integ_prs")
        record.data["integration_write_journal"] = {
            "execution_id": s.args.execution_id, "original_hash": s.args.review_hash,
            "operations": {"0": {
                "operation_hash": revision.digest(s.original["operations"][0]),
                "status": "in_flight", "evidence": None}},
        }
        s.orch.state.set_step("finalize", "integ_prs", record)
        s.approve()
        execute(s)
    for _ in range(2):
        completed = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
        old_id = completed.data["last_write_review"]["execution_id"]
        keys = ("integration_write_journal", R.DATA_KEY, "integration_runtime_migration", "in_flight_since")
        evidence = {key: deepcopy(completed.data[key]) for key in keys if key in completed.data}
        prior_history = deepcopy(completed.data.get("integration_execution_history", {}))
        assert s.orch.reopen("finalize", "integ_prs", "Recheck the integration PRs").changed
        s.args.execution_id = None
        s.args.reserve = True
        plan = P.integration_plan(s.orch.context("finalize", "integ_prs"), s.args)
        s.args.review_hash = W.review_hash(s.orch, "finalize", "integ_prs", plan)
        with C.state_lock(s.args.runs_root, s.args.release):
            assert IC.cmd_create_integration_prs(s.args) == 0
        reserved = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
        assert reserved.execution["id"] != old_id
        assert not any(key in reserved.data for key in keys)
        history = reserved.data["integration_execution_history"]
        assert history == {
            **prior_history, old_id: {"review": completed.data["last_write_review"], "data": evidence}}
        assert not s.orch.annotate_step(
            "finalize", "integ_prs", data={"integration_execution_history": {}}).changed
        s.args.reserve = False
        with C.state_lock(s.args.runs_root, s.args.release):
            assert IC.cmd_create_integration_prs(s.args) == 0
        saved = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
        assert saved.status == "done" and saved.execution is None
        assert saved.data["integration_execution_history"] == history
        assert saved.data["integration_write_journal"]["execution_id"] == reserved.execution["id"]
        assert all(entry["status"] == "applied"
                   for entry in saved.data["integration_write_journal"]["operations"].values())


@pytest.mark.parametrize("integration", ["git"], indirect=True)
def test_recovery_rehydrates_real_reviewed_git_in_isolated_scratch(recovery, monkeypatch):
    s = recovery
    original_git = R.G._git
    pushed = []

    def transport(root, *args, **kwargs):
        if args[0] == "push":
            pushed.append(args)
            s.tips["release-integration/1"] = s.original["operations"][2]["content"]["commit_id"]
            return b"offline CAS accepted"
        return original_git(root, *args, **kwargs)
    monkeypatch.setattr(R.G, "_git", transport)
    monkeypatch.setattr(R.G, "push_reviewed", REAL_PUSH)
    s.approve()
    execute(s)
    assert len(pushed) == 1
    assert "--force-with-lease=refs/heads/release-integration/1:" + s.checkout.head in pushed[0]
    assert not list(s.checkout.root.glob(".write-review-*"))


def test_normal_writer_fences_every_operation_before_call_and_checkpoints_results(recovery, monkeypatch):
    s = recovery
    s.states.clear()
    original = W.WritePlan(
        "create-integration-prs", s.original["parameters"],
        tuple(W.WriteOperation(**op) for op in s.original["operations"]), s.original["preconditions"])
    # Exercise the original authorization on a new offline release state only.
    s.orch.state.steps.clear()
    s.args.execution_id = None
    s.args.review_hash = W.review_hash(s.orch, "finalize", "integ_prs", original)
    pbi = PR.create_pbi

    def fenced(*args):
        record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
        assert record.data["integration_write_journal"]["operations"]["0"]["status"] == "in_flight"
        return pbi(*args)
    monkeypatch.setattr(PR, "create_pbi", fenced)
    with C.state_lock(s.args.runs_root, s.args.release):
        auth = W.authorize(s.args, s.orch, "finalize", "integ_prs", lambda: original)
        P.execute_integration(auth)
    record = ReleaseState.load(str(s.path)).get_step("finalize", "integ_prs")
    journal = record.data["integration_write_journal"]
    assert len(journal["operations"]) == 4
    assert all(entry["status"] == "applied" for entry in journal["operations"].values())


def test_runtime_change_blocks_execution_and_adoption_but_preview_remains_read_only(recovery, monkeypatch):
    s = recovery
    before = deepcopy(asdict(s.orch.state))
    binding = deepcopy(s.orch.state.workflow_revision)
    binding["runtime_hash"] = "sha256:" + "1" * 64
    revision.validate_binding(binding)
    s.orch.state.workflow_revision = binding
    # Keep the original approved envelope bound to the stored revision.
    value = json.loads(open(s.args.original_preview, encoding="utf-8").read())
    value["plan"]["workflow_revision"] = revision.revision_id(binding)
    value["review_hash"] = revision.digest(value["plan"])
    s.orch.state.steps["finalize.integ_prs"]["execution"]["write_review"]["hash"] = value["review_hash"]
    with open(s.args.original_preview, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
    preview = R.preview(s.orch, s.args)
    assert preview["execution_blocker"] and not preview["permission_to_execute"]
    s.args.recovery_hash = preview["recovery_hash"]
    with pytest.raises(ValueError, match="revision mismatch"):
        execute(s)
    adoption = revision.adoption_preview(s.orch)
    assert "steps.finalize.integ_prs: owned execution" in adoption["blockers"]
    assert adoption["invalidation"]["step_keys"] == []
    assert not s.calls


def test_cli_has_no_auto_approval_or_new_attempt_switch():
    from orchestrator.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["recover-integration-prs", "--release", "r",
                              "--execution-id", "id", "--executor", "owner",
                              "--original-preview", "plan.json", "--existing-pbi", "101"])
    assert not args.execute and not hasattr(args, "auto_approve") and not hasattr(args, "reserve")
