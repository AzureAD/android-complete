"""Offline restarts, local checkpoints and exact reads for opt-in compound writers."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli_common as C, git_write_plans as P, revision, write_review as W
from orchestrator.commands import integ_prs_cmd as IC, oneauth_pr_cmd as OC, step_action
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState
from steps.finalize import integ_prs as I, oneauth_common_pr as O
from tools import git_review as G, oneauth as OA, prs as PR
from test_git_write_review import arguments


@pytest.fixture
def compound(tmp_path, monkeypatch):
    roots, tips, prs, pbis, calls, covered = {}, {}, {}, {}, [], {}
    for index, key in enumerate(I.REPO_ORDER):
        roots[key] = str(tmp_path / key)
        monkeypatch.setitem(I.CONFIG, key, {
            "name": key, "dir": roots[key], "tool": "gh", "gh_repo": "offline/" + key,
            "target": "dev", "labels": ["checked"]})
        tips[key] = {
            "dev": f"{index + 1:040x}", "working/release/1": f"{index + 5:040x}",
            "release/1": f"{index + 9:040x}", "release-integration/1": f"{index + 13:040x}"}

    def repo(target):
        return target["key"]

    monkeypatch.setattr(G, "branch", lambda name: name)
    monkeypatch.setattr(G, "clean_repository", lambda root: Path(root))
    monkeypatch.setattr(G, "remote_url", str)
    monkeypatch.setattr(G, "remote_tip", lambda root, remote, name: tips[Path(root).name][name])
    monkeypatch.setattr(PR, "provider_branch_object_id", lambda target, name: tips[repo(target)][name])
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: [target["remote"]])
    monkeypatch.setattr(PR, "gh_find_open_pr", lambda slug, head, base: (
        True, deepcopy(prs.get((slug, head, base))), ""))

    def plan_ri(root, head, base, target):
        commit = hashlib.sha1((head + base).encode()).hexdigest()
        return {"tree": "a" * 40, "parents": [head, base], "edits": {},
                "commit": "" if covered.get(head) == base else "exact transient commit",
                "commit_id": commit}

    def push(root, remote, head, expected, content, validate):
        validate()
        assert tips[Path(root).name][head] == expected
        calls.append(("push", Path(root).name, head, content["parents"][1], content["commit_id"]))
        tips[Path(root).name][head] = content["commit_id"]
        covered[content["commit_id"]] = content["parents"][1]
        return content["commit_id"]

    def create(slug, head, base, title, body, labels):
        calls.append(("pr", slug, head, base))
        identity = len(prs) + 1
        prs[(slug, head, base)] = {
            "number": identity, "url": f"https://example.test/pr/{identity}",
            "title": title, "body": body, "labels": labels}
        return True, prs[(slug, head, base)]["url"], ""

    def create_pbi(org, project, title):
        calls.append(("pbi", org, project))
        pbis["101"] = {"id": 101, "title": title, "project": project,
                       "type": "Product Backlog Item"}
        return True, 101, "https://example.test/101", ""

    monkeypatch.setattr(G, "plan_ri", plan_ri)
    monkeypatch.setattr(G, "push_reviewed", push)
    monkeypatch.setattr(PR, "gh_create_pr", create)
    monkeypatch.setattr(PR, "create_pbi", create_pbi)
    monkeypatch.setattr(PR, "read_pbi", lambda org, project, identity: (
        True, deepcopy(pbis.get(identity)), ""))
    monkeypatch.setattr(PR, "gh_ensure_labels", lambda *args: pytest.fail("No label replay expected"))
    monkeypatch.setattr(C, "emit", lambda *args, **kwargs: None)

    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "finalize", "name": "Finalize", "steps": [{
            "id": I.ID, "name": "Integration PRs", "kind": "external",
            "write_command": I.WRITE_COMMAND}]}]}), encoding="utf-8")
    state = ReleaseState(release_id="r", ccd="2026-07-08", timezone="UTC", owner_email="owner@example.test",
                         versions={key: "1" for key in I.REPO_ORDER})
    orch = Orchestrator(str(config), state, mocks={}, as_of=date(2026, 7, 8))
    revision.bind_initial(orch)
    path = tmp_path / "r" / "release-state.json"
    state.save(str(path))
    args = arguments(tmp_path)
    args.config, args.repos = str(config), None
    args.execute, args.auto_approve, args.approved_by = True, True, None
    s = SimpleNamespace(args=args, path=path, calls=calls, tips=tips, prs=prs, pbis=pbis,
                        checkpoint=None, last_orch=None, command=IC.cmd_create_integration_prs)
    def read_pr(target, identity):
        if target["tool"] == "gh":
            observed = prs.get((target["gh_repo"], target["head"], target["base"]))
        else:
            observed = prs.get("oneauth")
            if observed:
                observed = {**observed, "number": observed["id"], "body": observed["description"]}
        if not observed or str(observed["number"]) != identity:
            return False, None, "missing exact PR"
        return True, deepcopy(observed), ""
    monkeypatch.setattr(PR, "read_pr_result", read_pr)

    def load(*_args):
        state = ReleaseState.load(str(path))
        orch = Orchestrator(str(config), state, mocks={}, as_of=date(2026, 7, 8))
        def checkpoint():
            if s.checkpoint:
                s.checkpoint(state, lambda: state.save(str(path)))
            else:
                state.save(str(path))
        state._checkpoint = checkpoint
        s.last_orch = orch
        return state, orch

    monkeypatch.setattr(C, "load_orch", load)
    def run(**changes):
        invocation = deepcopy(args)
        for key, value in changes.items():
            setattr(invocation, key, value)
        with C.state_lock(args.runs_root, args.release):
            return s.command(invocation)
    s.run, s.load = run, load
    return s


@pytest.fixture
def oneauth_compound(compound, monkeypatch):
    s = compound
    config = Path(s.args.config)
    config.write_text(yaml.safe_dump({"phases": [{
        "id": "finalize", "name": "Finalize", "steps": [{
            "id": O.ID, "name": "OneAuth", "kind": "external",
            "write_command": O.WRITE_COMMAND}]}]}), encoding="utf-8")
    state = ReleaseState(release_id="r", ccd="2026-07-08", timezone="UTC", owner_email="owner@example.test",
                         versions={"common": "1", "msal": "1"})
    orch = Orchestrator(str(config), state, mocks={}, as_of=date(2026, 7, 8))
    revision.bind_initial(orch)
    state.save(str(s.path))
    s.command = OC.cmd_create_oneauth_common_pr
    s.args.repo_dir = str(s.path.parent.parent / "common")
    monkeypatch.setattr(OA, "ORG", "https://example.test")
    monkeypatch.setattr(OA, "PROJECT", "offline")
    monkeypatch.setattr(OA, "REPO", "oneauth")
    monkeypatch.setattr(OA, "INGEST_BRANCH", "working/release/1")
    monkeypatch.setattr(OA, "TARGET_BRANCH", "dev")
    monkeypatch.setattr(OA, "review_repo_dir", lambda repository, path: path)
    monkeypatch.setattr(OA, "repository_remote_url", lambda repository: s.args.repo_dir)
    head = OA.INGEST_BRANCH
    final_files = {key: "reviewed final " + key for key in OA.FILES}
    s.files = {s.tips["common"][head]: {key: "initial " + key for key in OA.FILES}}
    monkeypatch.setattr(OA, "branch_object_id", lambda name, **kwargs: (
        True, s.tips["common"][name], ""))
    monkeypatch.setattr(OA, "read_text", lambda path, ref, **kwargs: (
        True, s.files[ref][next(k for k, v in OA.FILES.items() if v == path)], ""))
    monkeypatch.setattr(OA, "ahead_behind", lambda base, tip, **kwargs: (
        True, {"ahead": 1, "behind": int(s.files[tip] != final_files)}, ""))
    def merge(root, tip, base, paths, edits, comment):
        value = G.plan_ri(root, tip, base, OA.TARGET_BRANCH)
        value.update(files_before=s.files[tip], final_files=final_files,
                     ahead=1, behind=int(s.files[tip] != final_files), merge="none")
        return value
    monkeypatch.setattr(G, "plan_merge_edits", merge)
    push = G.push_reviewed
    def push_files(*args):
        tip = push(*args)
        s.files[tip] = dict(args[4]["final_files"])
        return tip
    monkeypatch.setattr(G, "push_reviewed", push_files)
    monkeypatch.setattr(OA, "find_open_pr", lambda *args, **kwargs: (
        True, deepcopy(s.prs.get("oneauth")), ""))
    def create(head, base, title, body, **kwargs):
        s.calls.append(("pr", "oneauth", head, base))
        s.prs["oneauth"] = {"id": 12, "url": "https://example.test/pr/12",
                            "title": title, "description": body}
        return True, deepcopy(s.prs["oneauth"]), ""
    monkeypatch.setattr(OA, "create_pr", create)
    return s


def record(s):
    return ReleaseState.load(str(s.path)).get_step("finalize", I.ID)


def interrupt_after_prs(s, count):
    def checkpoint(state, save):
        save()
        operations = state.get_step("finalize", I.ID).execution["write_progress"]["operations"]
        verified = [op for op in operations
                    if op["descriptor"]["kind"] == "integration.create" and op["receipt"]]
        if len(verified) == count:
            raise KeyboardInterrupt("offline process exit after durable acknowledgment")
    s.checkpoint = checkpoint
    with pytest.raises(KeyboardInterrupt):
        s.run()
    s.checkpoint = None


def test_three_of_eight_restart_replans_only_remaining_and_reuses_pbi(compound, monkeypatch):
    s = compound
    interrupt_after_prs(s, 3)
    previous = deepcopy(record(s).execution)
    assert len(s.prs) == 3
    s.tips["broker"]["dev"] = "f" * 40
    original_plan = G.plan_ri
    planned = []
    monkeypatch.setattr(G, "plan_ri", lambda root, *args: (
        planned.append(Path(root).name) or original_plan(root, *args)))
    assert s.run() == 0
    final = record(s)
    assert final.status == "done" and len(s.prs) == 8
    ledger = final.data["last_write_progress"]
    assert final.data["last_write_review"]["execution_id"] == previous["id"]
    assert ledger["reviews"][:1] == previous["write_progress"]["reviews"]
    assert ledger["operations"][:len(previous["write_progress"]["operations"])] == previous["write_progress"]["operations"]
    assert planned == ["msal", "broker", "authenticator"]
    assert [c[0] for c in s.calls].count("pbi") == 1
    assert [c[0] for c in s.calls].count("pr") == 8
    assert [c[1] for c in s.calls if c[0] == "push"].count("common") == 1
    assert all("AB#101" in pr["body"] for pr in s.prs.values())
    assert next(c for c in s.calls if c[:2] == ("push", "broker"))[3] == "f" * 40
    saved = s.path.read_text(encoding="utf-8")
    assert "exact transient commit" not in saved and "Scout-generated" not in saved


def test_other_repo_drift_does_not_block_unaffected_next_operation(compound, monkeypatch):
    s = compound
    create = PR.gh_create_pr
    def moving(*args, **kwargs):
        result = create(*args, **kwargs)
        if len(s.prs) == 3:
            s.tips["broker"]["dev"] = "f" * 40
        return result
    monkeypatch.setattr(PR, "gh_create_pr", moving)
    assert s.run() == 2
    assert len(s.prs) == 5  # Common pair, MSAL pair, Broker freeze survived unrelated drift.
    before = deepcopy(record(s).execution)
    assert all(op["receipt"] for op in before["write_progress"]["operations"])
    assert not any(c[:2] == ("push", "broker") for c in s.calls)
    assert s.run() == 0
    assert len(s.prs) == 8
    assert record(s).data["last_write_review"]["execution_id"] == before["id"]


@pytest.mark.parametrize("boundary", ["pbi", "pr", "push"])
def test_success_before_local_ack_recovers_exact_result_without_replay(compound, monkeypatch, boundary):
    s = compound
    if boundary == "pbi":
        # With no durable returned ID, an unknown PBI cannot be found by guessing its title.
        original = PR.create_pbi
        def create(*args):
            original(*args)
            raise KeyboardInterrupt("provider accepted, ID lost")
        monkeypatch.setattr(PR, "create_pbi", create)
    elif boundary == "pr":
        original = PR.gh_create_pr
        def create(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt("provider accepted, URL lost")
        monkeypatch.setattr(PR, "gh_create_pr", create)
    else:
        original = G.push_reviewed
        def push(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt("provider accepted, receipt lost")
        monkeypatch.setattr(G, "push_reviewed", push)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    calls = list(s.calls)
    monkeypatch.setattr(PR, "create_pbi", original) if boundary == "pbi" else None
    monkeypatch.setattr(PR, "gh_create_pr", original) if boundary == "pr" else None
    monkeypatch.setattr(G, "push_reviewed", original) if boundary == "push" else None
    if boundary == "pbi":
        assert s.run() == 1
        assert s.calls == calls
        assert record(s).execution["write_progress"]["operations"][0]["receipt"] is None
    else:
        assert s.run() == 0
        assert [c[0] for c in s.calls].count("pbi") == 1
        assert [c[0] for c in s.calls].count("pr") == 8
        assert len([c for c in s.calls if c[:2] == ("push", "common")]) == 1


@pytest.mark.parametrize("change", ["title", "missing", "pbi", "branch", "scope", "policy", "corrupt"])
def test_changed_receipt_or_frozen_intent_holds_without_more_writes(compound, change):
    s = compound
    interrupt_after_prs(s, 3)
    before = s.path.read_bytes()
    calls = list(s.calls)
    changes = {}
    if change == "title":
        next(iter(s.prs.values()))["title"] = "externally edited"
    elif change == "missing":
        s.prs.pop(next(iter(s.prs)))
    elif change == "pbi":
        s.pbis["101"]["title"] = "externally edited"
    elif change == "branch":
        s.tips["common"]["release-integration/1"] = "f" * 40
    elif change == "scope":
        changes["repos"] = ["common"]
    elif change == "policy":
        changes.update(auto_approve=False, review_hash=revision.digest({}), approved_by="someone-else")
    else:
        data = json.loads(before)
        data["steps"]["finalize.integ_prs"]["execution"]["write_progress"]["operations"][0]["receipt"]["id"] = "999"
        s.path.write_text(json.dumps(data), encoding="utf-8")
        before = s.path.read_bytes()
    if change == "corrupt":
        with pytest.raises(ValueError, match="Invalid verified"):
            s.run()
    else:
        assert s.run(**changes) == 1
    assert s.calls == calls and s.path.read_bytes() == before


@pytest.mark.parametrize("status", ["in_flight", "blocked"])
def test_normal_continue_exposes_checked_adapter_not_generic_override(compound, status):
    s = compound
    interrupt_after_prs(s, 3)
    state, orch = s.load()
    current = state.get_step("finalize", I.ID)
    current.status = status
    state.set_step("finalize", I.ID, current)
    report = orch.status_report()
    assert I.ID in report["scout_pending"]
    assert report["action"] is None
    assert report["current_steps"][0]["state"] == "scout"
    assert not report["active_phase"]["steps"][0]["needs_owner"]
    task, = report["work_dispatch"]["pending_work"]
    assert task["key"] == f"finalize.{I.ID}" and task["continuation"]
    assert not report["work_dispatch"]["may_return_to_user"]
    from orchestrator.dispatch import handoff_plan
    before = deepcopy(state.steps)
    held = handoff_plan(report["work_dispatch"], [
        f"finalize.{I.ID}=Exact provider readback remains unknown; owner recovery required"])
    assert held["may_return_to_user"] and not held["permission_to_execute"]
    assert state.steps == before
    outcome = W.continuation(orch, "finalize", I.ID)
    assert outcome.kind == "needs_skill" and "--execute --auto-approve" in outcome.payload["followup_command"]
    args = SimpleNamespace(phase="finalize", step=I.ID, release="r")
    prepared = step_action.prepare_step(args, state, orch)
    assert prepared["payload"]["continuation"] and prepared["permission_to_execute"] is False
    assert "checked command" in orch.step_once().message
    assert orch.complete_step("finalize", I.ID, "override").kind != "ran"
    assert orch.reopen("finalize", I.ID, "override").kind != "ran"
    kernel = orch._transition_kernel()
    assert not kernel.skip("finalize", I.ID, "override", record(s).execution["id"]).changed


@pytest.mark.parametrize("after_provider", [False, True])
def test_checkpoint_failure_never_authorizes_further_mutations(compound, after_provider):
    s = compound
    def checkpoint(state, save):
        operations = state.get_step("finalize", I.ID).execution["write_progress"]["operations"]
        if operations and (not after_provider or operations[0]["receipt"]):
            raise OSError("offline durable checkpoint unavailable")
        save()
    s.checkpoint = checkpoint
    assert s.run() == 2
    assert [c[0] for c in s.calls] == (["pbi"] if after_provider else [])
    s.checkpoint = None
    assert s.run() == 0
    assert [c[0] for c in s.calls].count("pbi") == 1


def test_target_advances_at_last_push_guard_never_sends_stale_commit(compound, monkeypatch):
    s = compound
    original = G.push_reviewed
    def raced(root, remote, head, expected, content, validate):
        s.tips[Path(root).name]["dev"] = "f" * 40
        return original(root, remote, head, expected, content, validate)
    monkeypatch.setattr(G, "push_reviewed", raced)
    assert s.run() == 2
    assert len(s.prs) == 1 and not any(c[0] == "push" for c in s.calls)
    assert all(op["receipt"] for op in record(s).execution["write_progress"]["operations"])
    monkeypatch.setattr(G, "push_reviewed", original)
    assert s.run() == 0
    assert next(c for c in s.calls if c[0] == "push")[3] == "f" * 40


def test_changed_manual_remainder_requires_fresh_hash_not_original_approval(compound, capsys):
    s = compound
    assert s.run(execute=False, auto_approve=False, approved_by="reviewer") == 0
    first_hash = json.loads(capsys.readouterr().out)["review_hash"]
    def checkpoint(state, save):
        save()
        ops = state.get_step("finalize", I.ID).execution["write_progress"]["operations"]
        if sum(bool(op["receipt"]) and op["descriptor"]["kind"] == "integration.create" for op in ops) == 3:
            raise KeyboardInterrupt("manual execution interrupted")
    s.checkpoint = checkpoint
    with pytest.raises(KeyboardInterrupt):
        s.run(auto_approve=False, approved_by="reviewer", review_hash=first_hash)
    s.checkpoint = None
    calls = list(s.calls)
    s.tips["broker"]["dev"] = "f" * 40
    assert s.run(auto_approve=False, approved_by="reviewer", review_hash=first_hash) == 1
    assert s.calls == calls
    capsys.readouterr()
    assert s.run(execute=False, auto_approve=False, approved_by="reviewer") == 0
    remaining_hash = json.loads(capsys.readouterr().out)["review_hash"]
    assert remaining_hash != first_hash
    assert s.run(auto_approve=False, approved_by="reviewer", review_hash=remaining_hash) == 0
    assert len(s.prs) == 8


@pytest.mark.parametrize("change", ["versions", "target", "root", "pbi-intent"])
def test_other_frozen_scope_changes_cannot_smuggle_writes(compound, monkeypatch, change):
    s = compound
    interrupt_after_prs(s, 3)
    calls, before = list(s.calls), s.path.read_bytes()
    kwargs = {}
    if change == "versions":
        data = json.loads(before)
        data["versions"]["broker"] = "2"
        s.path.write_text(json.dumps(data), encoding="utf-8")
        before = s.path.read_bytes()
    elif change == "target":
        monkeypatch.setitem(I.CONFIG["broker"], "target", "other")
    elif change == "root":
        monkeypatch.setitem(I.CONFIG["broker"], "dir", str(s.path.parent / "elsewhere"))
    else:
        kwargs["pbi"] = "101"
    assert s.run(**kwargs) == 1
    assert s.calls == calls and s.path.read_bytes() == before


@pytest.mark.parametrize("interruption", ["acknowledged-push", "provider-push", "provider-pr"])
def test_oneauth_shared_progress_recovers_exact_push_and_pr_without_replay(
        oneauth_compound, monkeypatch, interruption):
    s = oneauth_compound
    if interruption == "acknowledged-push":
        def checkpoint(state, save):
            save()
            ops = state.get_step("finalize", O.ID).execution["write_progress"]["operations"]
            if ops and ops[-1]["receipt"]:
                raise KeyboardInterrupt("acknowledged bump")
        s.checkpoint = checkpoint
    else:
        module, name = (G, "push_reviewed") if interruption == "provider-push" else (OA, "create_pr")
        original = getattr(module, name)
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt("provider success before receipt")
        monkeypatch.setattr(module, name, interrupted)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    s.checkpoint = None
    if interruption != "acknowledged-push":
        monkeypatch.setattr(module, name, original)
    previous = ReleaseState.load(str(s.path)).get_step("finalize", O.ID).execution
    assert s.run() == 0
    final = ReleaseState.load(str(s.path)).get_step("finalize", O.ID)
    assert final.status == "done"
    assert final.data["last_write_review"]["execution_id"] == previous["id"]
    assert [call[0] for call in s.calls] == ["push", "pr"]


def test_oneauth_mismatched_pending_pr_holds_without_duplicate(oneauth_compound, monkeypatch):
    s = oneauth_compound
    original = OA.create_pr
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("provider success")
    monkeypatch.setattr(OA, "create_pr", interrupted)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    monkeypatch.setattr(OA, "create_pr", original)
    s.prs["oneauth"]["description"] = "external edit"
    calls, saved = list(s.calls), s.path.read_bytes()
    assert s.run() == 1
    assert s.calls == calls and s.path.read_bytes() == saved


def stop_before_push(s, monkeypatch):
    original = G.push_reviewed
    def stopped(root, remote, head, expected, content, validate):
        validate()
        raise ValueError("offline writer exited before sending its push")
    monkeypatch.setattr(G, "push_reviewed", stopped)
    assert s.run() == 2
    monkeypatch.setattr(G, "push_reviewed", original)
    return original


def recover(s, **changes):
    return s.run(execute=False, auto_approve=False, approved_by=None, executor=None,
                 confirmed_by="owner@example.test", recovery_reason="Owner checked the stopped writer",
                 **changes)


@pytest.mark.parametrize("advance_base", [False, True])
def test_confirmed_unapplied_push_replans_without_erasing_attempt(compound, monkeypatch, advance_base):
    s = compound
    stop_before_push(s, monkeypatch)
    previous = record(s).execution
    calls = list(s.calls)
    assert s.run() == 1 and s.calls == calls
    if advance_base:
        s.tips["common"]["dev"] = "f" * 40
    assert recover(s, confirm_not_applied=True) == 0
    assert s.calls == calls
    resolved = record(s).execution
    assert resolved["id"] == previous["id"]
    attempted = resolved["write_progress"]["operations"][-1]
    assert attempted["receipt"] is None
    assert attempted["resolutions"][-1]["mode"] == "not_applied"
    assert attempted["resolutions"][-1]["previous"] == revision.digest(
        previous["write_progress"]["operations"][-1])
    assert s.run() == 0
    assert len(s.prs) == 8 and [c[0] for c in s.calls].count("pbi") == 1
    ledger = record(s).data["last_write_progress"]
    assert len({op["key"] for op in ledger["operations"]}) == len(ledger["operations"])
    assert ledger["operations"][len(previous["write_progress"]["operations"]) - 1] == attempted
    assert next(c for c in s.calls if c[:2] == ("push", "common"))[3] == s.tips["common"]["dev"]


@pytest.mark.parametrize("kind", ["integration", "oneauth"])
def test_owner_manual_commit_is_recorded_read_only_and_not_overwritten(
        request, monkeypatch, kind):
    s = request.getfixturevalue("compound" if kind == "integration" else "oneauth_compound")
    stop_before_push(s, monkeypatch)
    step = I.ID if kind == "integration" else O.ID
    previous = ReleaseState.load(str(s.path)).get_step("finalize", step).execution
    op = previous["write_progress"]["operations"][-1]
    manual = "e" * 40
    s.tips["common"][op["descriptor"]["target"]["head"]] = manual
    if kind == "oneauth":
        s.files[manual] = dict(next(iter(s.files.values())))
    calls = list(s.calls)
    assert recover(s, manual_result=manual) == 0
    assert s.calls == calls
    assert s.run() == 0
    final = ReleaseState.load(str(s.path)).get_step("finalize", step)
    assert final.data["last_write_review"]["execution_id"] == previous["id"]
    assert not any(c[:2] == ("push", "common") for c in s.calls)
    proof = next(op for op in final.data["last_write_progress"]["operations"] if op["resolutions"])
    assert proof["receipt"]["commit"] == manual
    assert proof["resolutions"][-1]["mode"] == "manual"


def test_manual_pbi_recovers_lost_creation_id_without_creating_another(compound, monkeypatch):
    s = compound
    original = PR.create_pbi
    def lost(*args):
        original(*args)
        raise KeyboardInterrupt("provider ID lost")
    monkeypatch.setattr(PR, "create_pbi", lost)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    monkeypatch.setattr(PR, "create_pbi", original)
    calls = list(s.calls)
    assert recover(s, confirm_not_applied=True) == 1
    assert recover(s, manual_result="101") == 0
    assert s.calls == calls
    assert s.run() == 0
    assert len(s.prs) == 8 and [c[0] for c in s.calls].count("pbi") == 1


def test_manual_pr_may_have_owner_edited_content_but_not_another_target(compound, monkeypatch):
    s = compound
    original = PR.gh_create_pr
    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("provider URL lost")
    monkeypatch.setattr(PR, "gh_create_pr", lost)
    with pytest.raises(KeyboardInterrupt):
        s.run()
    monkeypatch.setattr(PR, "gh_create_pr", original)
    pr = next(iter(s.prs.values()))
    pr["title"], pr["body"] = "Owner completed the freeze", "Owner's manual PR description"
    calls, saved = list(s.calls), s.path.read_bytes()
    assert recover(s, manual_result="999") == 1
    assert s.path.read_bytes() == saved
    assert recover(s, manual_result=str(pr["number"])) == 0
    assert s.calls == calls
    assert s.run() == 0 and len(s.prs) == 8
    assert next(iter(s.prs.values()))["title"] == "Owner completed the freeze"


@pytest.mark.parametrize("change", [
    "writer-options", "actor", "reason", "branch", "origin", "provider", "scope", "checkpoint"])
def test_recovery_rejects_invalid_proof_or_authority_without_writes(
        compound, monkeypatch, change):
    s = compound
    stop_before_push(s, monkeypatch)
    calls = list(s.calls)
    options = dict(execute=False, auto_approve=False, approved_by=None, executor=None,
                   confirm_not_applied=True, confirmed_by="owner@example.test", recovery_reason="Stopped")
    if change == "writer-options":
        options.update(execute=True, auto_approve=True)
    elif change == "actor":
        options["confirmed_by"] = "other@example.test"
    elif change == "reason":
        options["recovery_reason"] = ""
    elif change == "branch":
        s.tips["common"]["release-integration/1"] = "e" * 40
    elif change == "origin":
        monkeypatch.setattr(G, "remote_url", lambda root: "different")
    elif change == "provider":
        monkeypatch.setattr(PR, "provider_branch_object_id", lambda *args: "e" * 40)
    elif change == "scope":
        options["repos"] = ["common"]
    else:
        def failed(state, save):
            raise OSError("Recovery checkpoint failed")
        s.checkpoint = failed
    saved = s.path.read_bytes()
    assert s.run(**options) == 1
    assert s.calls == calls and s.path.read_bytes() == saved


def test_owner_can_correct_a_manual_result_with_append_only_history(compound, monkeypatch):
    s = compound
    stop_before_push(s, monkeypatch)
    s.tips["common"]["release-integration/1"] = "e" * 40
    assert recover(s, manual_result="e" * 40) == 0
    previous = record(s).execution["write_progress"]["operations"][-1]
    s.tips["common"]["release-integration/1"] = "f" * 40
    assert recover(s, manual_result="f" * 40) == 0
    updated = record(s).execution["write_progress"]["operations"][-1]
    assert updated["resolutions"][:-1] == previous["resolutions"]
    assert updated["resolutions"][-1]["previous"] == revision.digest(previous)
    assert s.run() == 0


def test_recovery_needs_no_clean_checkout_and_does_not_erase_generic_guards(compound, monkeypatch):
    s = compound
    stop_before_push(s, monkeypatch)
    monkeypatch.setattr(G, "clean_repository", lambda *a, **k: (_ for _ in ()).throw(ValueError("dirty")))
    assert recover(s, confirm_not_applied=True) == 0
    state, orch = s.load()
    assert orch.complete_step("finalize", I.ID, "override").kind != "ran"
    assert orch.reopen("finalize", I.ID, "override").kind != "ran"
    assert s.run() == 1


def test_manual_completion_unblocks_owner_without_provider_access(compound, monkeypatch, capsys):
    s = compound
    stop_before_push(s, monkeypatch)
    previous = record(s).execution
    calls = list(s.calls)
    monkeypatch.setattr(PR, "provider_repository_urls", lambda *a: (_ for _ in ()).throw(ValueError("denied")))
    monkeypatch.setattr(PR, "read_pbi", lambda *a: (False, None, "denied"))
    monkeypatch.setattr(G, "remote_url", lambda *a: (_ for _ in ()).throw(ValueError("checkout unavailable")))
    assert recover(s, manual_completion="https://example.test/release/evidence") == 0
    response = capsys.readouterr().out
    assert '"machine_verified": false' in response
    final = record(s)
    assert final.status == "done" and final.execution is None and final.by == "human"
    ledger = final.data["last_write_progress"]
    assert ledger["operations"] == previous["write_progress"]["operations"]
    assert not ledger["finished"]
    assert ledger["manual_completion"]["confirmed_by"] == "owner@example.test"
    assert final.data["last_write_review"]["execution_id"] == previous["id"]
    assert s.calls == calls
    assert s.run() == 1 and s.calls == calls


def test_oneauth_manual_completion_does_not_require_a_checkout(oneauth_compound, monkeypatch):
    s = oneauth_compound
    stop_before_push(s, monkeypatch)
    monkeypatch.setattr(OA, "review_repo_dir", lambda *a: (_ for _ in ()).throw(ValueError("checkout missing")))
    calls = list(s.calls)
    assert recover(s, manual_completion="https://example.test/manual/oneauth", repo_dir=None) == 0
    assert ReleaseState.load(str(s.path)).get_step("finalize", O.ID).status == "done"
    assert s.calls == calls


@pytest.mark.parametrize("problem", ["owner", "evidence", "checkpoint", "prerequisite"])
def test_manual_completion_cannot_fake_verification_or_bypass_gates(
        compound, monkeypatch, problem):
    s = compound
    stop_before_push(s, monkeypatch)
    options = dict(execute=False, auto_approve=False, approved_by=None, executor=None,
                   confirmed_by="owner@example.test", recovery_reason="Manually completed all work; writer stopped",
                   manual_completion="https://example.test/release/evidence")
    if problem == "owner":
        options["confirmed_by"] = "someone@example.test"
    elif problem == "evidence":
        options["manual_completion"] = "https://token@example.test/evidence"
    elif problem == "checkpoint":
        s.checkpoint = lambda *args: (_ for _ in ()).throw(OSError("offline save failed"))
    else:
        from orchestrator.transitions import TransitionKernel
        monkeypatch.setattr(TransitionKernel, "_gate_precondition_problem", lambda *a, **k: "gate not approved")
    calls, saved = list(s.calls), s.path.read_bytes()
    assert s.run(**options) == 1
    assert s.calls == calls and s.path.read_bytes() == saved
    assert s.last_orch.state.get_step("finalize", I.ID).execution is not None


def test_merged_pr_receipt_is_read_by_identity_not_recreated(compound, monkeypatch):
    s = compound
    interrupt_after_prs(s, 3)
    monkeypatch.setattr(PR, "gh_find_open_pr", lambda slug, head, base: (
        True, None if (slug, head, base) == next(iter(s.prs)) else
        deepcopy(s.prs.get((slug, head, base))), ""))
    assert s.run() == 0
    assert len(s.prs) == 8 and [c[0] for c in s.calls].count("pr") == 8


@pytest.mark.parametrize("tool", ["gh", "ado"])
@pytest.mark.parametrize("problem", [
    "none", "merged", "repository", "project", "head", "base", "closed", "unreadable", "fork", "id", "body"])
def test_exact_pr_result_readback_binds_host_repository_branches_and_status(monkeypatch, tool, problem):
    target = {"tool": tool, "gh_repo": "host.example.test/org/repo", "head": "release/1", "base": "dev",
              "ado": {"org": "https://dev.azure.com/org", "project": "P", "repository": "R"}}
    if tool == "gh":
        value = {"number": 7, "html_url": "https://host.example.test/org/repo/pull/7",
                 "title": "Release", "body": "Reviewed", "labels": [{"name": "release"}],
                 "head": {"ref": target["head"], "repo": {"full_name": "org/repo"}},
                 "base": {"ref": target["base"], "repo": {"full_name": "org/repo"}},
                 "state": "open", "merged": False}
        if problem == "merged":
            value.update(state="closed", merged=True)
        elif problem in ("repository", "project", "fork"):
            value["head"]["repo"]["full_name"] = "other/repo"
        elif problem in ("head", "base"):
            value[problem]["ref"] = "other"
        elif problem == "closed":
            value["state"] = "closed"
        elif problem == "id":
            value["number"] = 8
        elif problem == "body":
            value["body"] = False
    else:
        value = {"pullRequestId": 7, "title": "Release", "description": "Reviewed",
                 "sourceRefName": "refs/heads/" + target["head"], "targetRefName": "refs/heads/" + target["base"],
                 "repository": {"id": "rid", "name": "R", "project": {"id": "pid", "name": "P"}},
                 "status": "active"}
        if problem == "merged":
            value["status"] = "completed"
        elif problem == "repository":
            value["repository"]["name"] = "other"
        elif problem == "project":
            value["repository"]["project"]["name"] = "other"
        elif problem in ("head", "base"):
            value["sourceRefName" if problem == "head" else "targetRefName"] = "refs/heads/other"
        elif problem == "fork":
            value["forkSource"] = {"repository": "foreign"}
        elif problem == "closed":
            value["status"] = "abandoned"
        elif problem == "id":
            value["pullRequestId"] = 8
        elif problem == "body":
            value["description"] = False
    calls = []
    monkeypatch.setattr(PR, "_run", lambda args, **kwargs: (
        calls.append(args) or (1 if problem == "unreadable" else 0), json.dumps(value), "offline denied"))
    monkeypatch.setattr(PR, "read_pr_work_items", lambda *args: (True, ["101"], ""))
    ok, observed, detail = PR.read_pr_result(target, "7")
    assert ok == (problem in ("none", "merged"))
    assert observed is not None if ok else detail
    assert "create" not in calls[0] and "update" not in calls[0]


@pytest.mark.parametrize("command", [I.WRITE_COMMAND, O.WRITE_COMMAND])
def test_manual_completion_is_available_on_both_existing_cli_commands(command):
    from orchestrator.cli import build_parser
    args = build_parser().parse_args([
        command, "--release", "r", "--manual-completion", "https://example.test/evidence",
        "--confirmed-by", "owner@example.test", "--recovery-reason", "All work completed; writer stopped"])
    assert not args.execute and not args.auto_approve and args.executor is None
    assert args.manual_completion == "https://example.test/evidence"
