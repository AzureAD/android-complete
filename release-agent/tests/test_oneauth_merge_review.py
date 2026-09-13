"""OneAuth merge reviews using real, network-disabled Git fixture repositories only."""
from argparse import ArgumentParser
import base64
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli_common as C, git_write_plans as P, revision, write_review as W
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState
from steps.lib.mockctx import MISSING
from tools import git_review as G, oneauth as OA, pipelines as PL, prs as PR


@pytest.fixture(autouse=True)
def offline_git(monkeypatch, tmp_path):
    run = subprocess.run

    def guarded(command, *args, **kwargs):
        assert isinstance(command, list) and command[0] == "git", "Non-Git subprocess is forbidden"
        cwd = kwargs.get("cwd")
        assert cwd is not None or "check-ref-format" in command
        if cwd is not None:
            assert Path(cwd).resolve().is_relative_to(tmp_path.resolve())
        env = dict(kwargs.get("env", os.environ))
        env.update(GIT_ALLOW_PROTOCOL="file", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        kwargs["env"] = env
        return run(command, *args, **kwargs)

    def blocked(*args, **kwargs):
        pytest.fail("Network/token/server-side merge or unreviewed REST push is forbidden")

    monkeypatch.setattr(subprocess, "run", guarded)
    for name in ("_token", "push_edits", "merge_dev_into_ingestion"):
        monkeypatch.setattr(OA, name, blocked)


def git(root, *args):
    env = G._environment()
    env.update(GIT_AUTHOR_DATE="1700000000 +0000", GIT_COMMITTER_DATE="1700000000 +0000")
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-c", "core.hooksPath=", *args],
        cwd=root, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8").strip()


def commit(root, message):
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", message)
    return git(root, "rev-parse", "HEAD")


def write(root, path, text):
    filename = root / path.lstrip("/")
    filename.parent.mkdir(parents=True, exist_ok=True)
    filename.write_bytes(text.encode("utf-8"))


class Permit:
    def __init__(self, plan):
        self.plan, self.validations = plan, 0

    def validate(self):
        self.validations += 1


@pytest.fixture
def oneauth(tmp_path, monkeypatch):
    root, remote = tmp_path / "checkout", tmp_path / "remote.git"
    root.mkdir()
    remote.mkdir()
    git(remote, "init", "--quiet", "--bare", "--template=")
    git(remote, "config", "core.longpaths", "true")
    git(root, "init", "--quiet", "-b", "dev", "--template=")
    git(root, "config", "user.name", "Offline Reviewer")
    git(root, "config", "user.email", "reviewer@example.test")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "core.symlinks", "false")
    paths = dict(OA.FILES)
    data = {
        "toml": 'msIdentityCommon = "0"\nmsIdentityCommonTest = "unchanged"\n',
        "cgmanifest": '{"groupId":"com.microsoft.identity","artifactId":"common","version":"0"}\n',
        "readme": "| MSAL Android Common | 0 |\n",
        "changelog": "## [Unreleased]\n### Other Changes\n",
    }
    for key, path in paths.items():
        write(root, path, data[key])
    write(root, "deleted-on-base.txt", "old\n")
    initial = commit(root, "initial")
    head_name, base_name = "android/common-ingestion", "dev"
    git(root, "checkout", "--quiet", "-b", head_name)
    write(root, "ingestion-only.txt", "keep ingestion content\n")
    head = commit(root, "ingestion change")
    git(root, "checkout", "--quiet", base_name)
    write(root, paths["toml"], data["toml"] + "# keep base TOML settings\n")
    write(root, paths["cgmanifest"], data["cgmanifest"].replace('"version":"0"', '"version":"3"'))
    write(root, paths["readme"], "Base documentation\n\n" + data["readme"])
    write(root, paths["changelog"], data["changelog"] + "- Base release note\n")
    (root / "base-binary.dat").write_bytes(b"\x00\xff\x01")
    git(root, "rm", "deleted-on-base.txt")
    base = commit(root, "base changes")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "--quiet", "origin", head_name, base_name)
    repository = {"org": "https://example.test", "project": "Reviewed", "repository": "OneAuth"}
    for key, constant in (("org", "ORG"), ("project", "PROJECT"), ("repository", "REPO")):
        monkeypatch.setattr(OA, constant, repository[key])
    context = SimpleNamespace(
        release=SimpleNamespace(versions={"common": "1.2.3", "msal": "4.5.6"}, release_id="r"),
        input=lambda key, default=MISSING: default)
    args = SimpleNamespace(repo_dir=str(root), release="r", runs_root=str(tmp_path),
                           review_hash=None, approved_by="reviewer", executor="offline-session",
                           execution_id=None, reserve=False)
    state = SimpleNamespace(
        root=root, remote=remote, initial=initial, head=head, base=base, head_name=head_name,
        base_name=base_name, repository=repository, paths=paths, args=args, context=context,
        calls=[], pr=None, fail_create=False, before_write=lambda: None)

    def verify(repository):
        assert repository == state.repository

    def tip(name, *, repository):
        verify(repository)
        return True, git(remote, "rev-parse", "refs/heads/" + name), ""

    def counts(base, head, *, repository, ref_type):
        verify(repository)
        assert ref_type == "commit"
        return True, {
            "ahead": int(git(remote, "rev-list", "--count", f"{base}..{head}")),
            "behind": int(git(remote, "rev-list", "--count", f"{head}..{base}")),
        }, ""

    def read(path, ref, ref_type, *, repository):
        verify(repository)
        assert ref_type == "commit" and G.object_id(ref)
        result = subprocess.run(["git", "show", ref + ":" + path[1:]], cwd=remote,
                                env=G._environment(), capture_output=True, timeout=30)
        assert result.returncode == 0
        return True, result.stdout.decode("utf-8"), ""

    def find(head, base, *, repository):
        verify(repository)
        assert (head, base) == (state.head_name, state.base_name)
        return True, deepcopy(state.pr), ""

    def create(head, base, title, body, *, repository):
        verify(repository)
        assert (head, base) == (state.head_name, state.base_name)
        state.before_write()
        state.calls.append(("create", deepcopy(repository), head, base, title, body))
        if state.fail_create:
            return False, None, "offline PR failure"
        state.pr = {"id": 12, "title": title, "description": body, "url": "https://example.test/pr/12"}
        return True, deepcopy(state.pr), ""

    original_push = G.push_reviewed

    def push(root, remote, name, expected, content, validate):
        state.before_write()
        result = original_push(root, remote, name, expected, content, validate)
        state.calls.append(("push", root, remote, name, expected, deepcopy(content)))
        return result

    def remote_uri(repository):
        verify(repository)
        return str(remote)

    monkeypatch.setattr(OA, "branch_object_id", tip)
    monkeypatch.setattr(OA, "ahead_behind", counts)
    monkeypatch.setattr(OA, "read_text", read)
    monkeypatch.setattr(OA, "find_open_pr", find)
    monkeypatch.setattr(OA, "create_pr", create)
    monkeypatch.setattr(OA, "repository_remote_url", remote_uri)
    monkeypatch.setattr(G, "push_reviewed", push)
    return state


def checkout_snapshot(root):
    return {
        "head": git(root, "rev-parse", "HEAD"),
        "refs": git(root, "show-ref"),
        "index": (root / ".git" / "index").read_bytes(),
        "objects": sorted(str(p.relative_to(root)) for p in (root / ".git" / "objects").rglob("*")),
        "files": {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.relative_to(root).parts},
    }


def test_behind_plan_exact_post_merge_edits_stable_and_no_user_ref_changes(oneauth):
    s = oneauth
    before = checkout_snapshot(s.root)
    plan = P.oneauth_plan(s.context, s.args)
    assert plan == P.oneauth_plan(s.context, s.args)
    assert checkout_snapshot(s.root) == before
    assert not list(s.root.glob(".write-review-*"))
    value = plan.as_dict()
    push = value["operations"][0]["content"]
    assert value["parameters"]["repo_dir"] == str(s.root.resolve())
    assert value["parameters"]["merge"] == "merge-tree"
    assert push["parents"] == [s.head, s.base]
    assert push["ahead"] == push["behind"] == 1
    assert push["merged_tree"] != push["tree"]
    assert '"version":"3"' in push["merged_files"]["cgmanifest"]
    assert '"version":"1.2.3"' in push["final_files"]["cgmanifest"]
    assert "# keep base TOML settings" in push["final_files"]["toml"]
    assert "Base documentation" in push["final_files"]["readme"]
    assert "- Base release note" in push["final_files"]["changelog"]
    assert OA.changelog_line("1.2.3", "4.5.6") in push["final_files"]["changelog"]
    assert len(push["version_edits"]) == 4
    assert len(push["edits"]) == 6
    assert push["edits"]["deleted-on-base.txt"]["after"] is None
    assert base64.b64decode(push["edits"]["base-binary.dat"]["after"]["base64"]) == b"\x00\xff\x01"
    assert "ingestion-only.txt" not in push["edits"]
    assert "1700000001 +0000" in push["commit"]
    assert "Offline Reviewer <reviewer@example.test>" in push["commit"]
    assert value["operations"][1]["preconditions"]["head_tip"] == push["commit_id"]
    assert s.calls == []


def test_execution_pushes_exact_two_parent_commit_without_replanning_or_late_inputs(oneauth, monkeypatch):
    s = oneauth
    plan = P.oneauth_plan(s.context, s.args)
    before = checkout_snapshot(s.root)
    reviewed = plan.as_dict()["operations"]
    s.args.repo_dir = "unreviewed"
    s.context.release.versions.update(common="999", msal="999")
    for constant in ("ORG", "PROJECT", "REPO", "INGEST_BRANCH", "TARGET_BRANCH"):
        monkeypatch.setattr(OA, constant, "unreviewed")
    monkeypatch.setattr(OA, "FILES", {})
    monkeypatch.setattr(OA, "apply_edits", lambda *a, **kw: pytest.fail("Late edits are forbidden"))
    monkeypatch.setattr(G, "plan_merge_edits", lambda *a, **kw: pytest.fail("Late merge is forbidden"))
    auth = Permit(plan)
    assert "PR https://example.test/pr/12" in P.execute_oneauth(auth)
    assert [call[0] for call in s.calls] == ["push", "create"]
    content = reviewed[0]["content"]
    assert s.calls[0][-1] == content
    assert s.calls[1][-2:] == (reviewed[1]["content"]["title"], reviewed[1]["content"]["body"])
    assert git(s.remote, "rev-parse", s.head_name) == content["commit_id"]
    assert git(s.remote, "cat-file", "commit", content["commit_id"]) == content["commit"].rstrip("\n")
    assert git(s.remote, "rev-parse", s.head_name + "^{tree}") == content["tree"]
    assert git(s.remote, "show", s.head_name + ":ingestion-only.txt") == "keep ingestion content"
    assert checkout_snapshot(s.root) == before
    assert auth.validations >= len(reviewed)


def test_conflicts_hold_without_any_provider_writes_or_checkout_changes(oneauth):
    s = oneauth
    write(s.root, s.paths["toml"], 'msIdentityCommon = "8"\n')
    commit(s.root, "conflicting base")
    git(s.root, "checkout", "--quiet", s.head_name)
    write(s.root, s.paths["toml"], 'msIdentityCommon = "9"\n')
    commit(s.root, "conflicting ingestion")
    git(s.root, "push", "--quiet", "origin", s.head_name, s.base_name)
    before = checkout_snapshot(s.root)
    with pytest.raises(ValueError, match="Merge conflicts"):
        P.oneauth_plan(s.context, s.args)
    assert checkout_snapshot(s.root) == before
    assert not list(s.root.glob(".write-review-*"))
    assert s.calls == []


@pytest.mark.parametrize("dirty", ["tracked", "untracked", "staged", "merge"])
def test_dirty_or_in_progress_checkout_is_held(oneauth, dirty):
    s = oneauth
    if dirty == "merge":
        (s.root / ".git" / "MERGE_HEAD").write_text(s.head + "\n", encoding="utf-8")
    else:
        write(s.root, "new.txt" if dirty == "untracked" else s.paths["readme"], "dirty\n")
        if dirty == "staged":
            git(s.root, "add", ".")
    before = checkout_snapshot(s.root)
    with pytest.raises(ValueError, match="checkout|In-progress"):
        P.oneauth_plan(s.context, s.args)
    assert checkout_snapshot(s.root) == before
    assert s.calls == []


def test_origin_mismatch_even_with_same_commit_ids_is_held(oneauth, tmp_path):
    s = oneauth
    wrong = tmp_path / "wrong.git"
    wrong.mkdir()
    git(wrong, "init", "--quiet", "--bare", "--template=")
    git(wrong, "config", "core.longpaths", "true")
    git(s.root, "push", "--quiet", str(wrong), s.head_name, s.base_name)
    git(s.root, "remote", "set-url", "origin", str(wrong))
    with pytest.raises(ValueError, match="remote URI"):
        P.oneauth_plan(s.context, s.args)
    assert s.calls == []


@pytest.mark.parametrize("name", ["head_name", "base_name"])
def test_remote_tip_drift_before_execution_prevents_all_writes(oneauth, name):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    git(s.remote, "update-ref", "refs/heads/" + getattr(s, name), s.initial)
    with pytest.raises(ValueError, match="branch changed"):
        P.execute_oneauth(auth)
    assert s.calls == []


def test_compare_and_swap_rejects_race_at_push_without_overwriting_remote(oneauth, monkeypatch):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    original = G._git
    attempted = []

    def race(root, *args, **kwargs):
        if args[0] == "push":
            git(s.remote, "update-ref", "refs/heads/" + s.head_name, s.initial)
            attempted.append(args)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(G, "_git", race)
    with pytest.raises(ValueError, match="push failed"):
        P.execute_oneauth(auth)
    assert len(attempted) == 1
    assert f"--force-with-lease=refs/heads/{s.head_name}:{s.head}" in attempted[0]
    assert git(s.remote, "rev-parse", s.head_name) == s.initial
    assert s.calls == []


def test_base_drift_during_commit_rehydration_prevents_push(oneauth, monkeypatch):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    original = G._git

    def race(root, *args, **kwargs):
        if args[0] == "write-tree":
            git(s.remote, "update-ref", "refs/heads/" + s.base_name, s.initial)
        assert args[0] != "push", "Base drift must block before pushing"
        return original(root, *args, **kwargs)

    monkeypatch.setattr(G, "_git", race)
    with pytest.raises(ValueError, match="branch changed"):
        P.execute_oneauth(auth)
    assert s.calls == []


def test_checkout_becomes_dirty_during_commit_rehydration_prevents_push(oneauth, monkeypatch):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    original = G._git

    def race(root, *args, **kwargs):
        if args[0] == "write-tree":
            write(s.root, s.paths["readme"], "concurrent local edit\n")
        assert args[0] != "push", "Dirty checkout must block before pushing"
        return original(root, *args, **kwargs)

    monkeypatch.setattr(G, "_git", race)
    with pytest.raises(ValueError, match="Dirty"):
        P.execute_oneauth(auth)
    assert s.calls == []
    assert (s.root / s.paths["readme"][1:]).read_bytes() == b"concurrent local edit\n"


def test_missing_local_objects_require_separate_fetch_without_preview_fetch(oneauth, monkeypatch):
    s = oneauth
    monkeypatch.setattr(OA, "branch_object_id", lambda *a, **kw: (True, "f" * 40, ""))
    monkeypatch.setattr(G, "remote_tip", lambda *a, **kw: "f" * 40)
    monkeypatch.setattr(OA, "ahead_behind", lambda *a, **kw: (True, {"ahead": 1, "behind": 1}, ""))
    monkeypatch.setattr(OA, "read_text", lambda *a, **kw: (True, "", ""))
    original = G._git

    def no_fetch(root, *args, **kwargs):
        assert args[0] not in ("fetch", "pull", "push", "clone")
        return original(root, *args, **kwargs)

    monkeypatch.setattr(G, "_git", no_fetch)
    with pytest.raises(ValueError, match="fetch separately"):
        P.oneauth_plan(s.context, s.args)
    assert s.calls == []
    assert not list(s.root.glob(".write-review-*"))


def test_changed_symlink_is_explicitly_unsupported(oneauth):
    s = oneauth
    oid = git(s.root, "rev-parse", s.base + ":deps/README.md")
    git(s.root, "update-index", "--add", "--cacheinfo", "120000", oid, "base-link")
    git(s.root, "commit", "--quiet", "-m", "unsupported symlink")
    git(s.root, "reset", "--hard", "HEAD")
    git(s.root, "push", "--quiet", "origin", s.base_name)
    with pytest.raises(ValueError, match="Symlink/submodule"):
        P.oneauth_plan(s.context, s.args)
    assert s.calls == []


def test_post_merge_missing_anchor_blocks_all_writes(oneauth):
    s = oneauth
    write(s.root, s.paths["changelog"], "Base removed the required anchors\n")
    commit(s.root, "remove changelog anchor")
    git(s.root, "push", "--quiet", "origin", s.base_name)
    with pytest.raises(ValueError, match="Unreleased"):
        P.oneauth_plan(s.context, s.args)
    assert s.calls == []


def test_author_metadata_changes_review_hash(oneauth):
    s = oneauth
    first = revision.digest(P.oneauth_plan(s.context, s.args).as_dict())
    git(s.root, "config", "user.name", "Another Reviewed Author")
    second = revision.digest(P.oneauth_plan(s.context, s.args).as_dict())
    assert first != second
    assert s.calls == []


def test_nonmerge_bump_also_has_exact_single_parent_commit(oneauth):
    s = oneauth
    git(s.remote, "update-ref", "refs/heads/" + s.base_name, s.initial)
    plan = P.oneauth_plan(s.context, s.args)
    content = plan.as_dict()["operations"][0]["content"]
    assert content["merge"] == "none" and content["behind"] == 0
    assert content["parents"] == [s.head]
    assert len(content["edits"]) == 4
    P.execute_oneauth(Permit(plan))
    assert git(s.remote, "rev-parse", s.head_name) == content["commit_id"]


def test_noop_reuses_exact_existing_pr_without_commit(oneauth):
    s = oneauth
    git(s.root, "checkout", "--quiet", s.head_name)
    data = {key: (s.root / path[1:]).read_text(encoding="utf-8") for key, path in s.paths.items()}
    for path, text in OA.apply_edits(data, "1.2.3", "4.5.6", paths=s.paths).items():
        write(s.root, path, text)
    commit(s.root, "already ingested")
    git(s.root, "push", "--quiet", "origin", s.head_name)
    git(s.remote, "update-ref", "refs/heads/" + s.base_name, s.initial)
    s.pr = {"id": 7, "title": "Reviewed title", "description": "Reviewed body",
            "url": "https://example.test/pr/7"}
    plan = P.oneauth_plan(s.context, s.args)
    assert [op.kind for op in plan.operations] == ["oneauth.reuse"]
    assert plan.operations[0].content["title"] == "Reviewed title"
    assert "https://example.test/pr/7" in P.execute_oneauth(Permit(plan))
    assert s.calls == []


def test_fast_forward_merge_still_reviews_explicit_two_parent_bumped_commit(oneauth):
    s = oneauth
    git(s.remote, "update-ref", "refs/heads/" + s.head_name, s.initial)
    plan = P.oneauth_plan(s.context, s.args)
    content = plan.as_dict()["operations"][0]["content"]
    assert content["parents"] == [s.initial, s.base]
    assert content["ahead"] == 0 and content["behind"] == 1
    assert content["merge"] == "merge-tree"
    P.execute_oneauth(Permit(plan))
    assert git(s.remote, "rev-parse", s.head_name) == content["commit_id"]


def test_normalized_explicit_path_and_repository_root_default_are_bound(oneauth, monkeypatch):
    s = oneauth
    parser = ArgumentParser()
    OA.add_review_arguments(parser)
    args = parser.parse_args(["--repo-dir", str(s.root) + "\\..\\checkout"])
    plan = P.oneauth_plan(s.context, args)
    assert plan.parameters["repo_dir"] == str(s.root.resolve())
    monkeypatch.setattr(PR, "repo_dir", lambda name: s.root if name == "OneAuth" else None)
    assert P.oneauth_plan(s.context, SimpleNamespace()) == plan
    s.args.repo_dir = str(s.root.parent / "missing")
    with pytest.raises(ValueError, match="provide --repo-dir"):
        P.oneauth_plan(s.context, s.args)


@pytest.mark.parametrize("remote", ["http://example.test/repo", "https://user:secret@example.test/repo",
                                  "ssh://example.test/repo", "https://example.test/repo?unreviewed=1"])
def test_provider_clone_uri_rejects_unsafe_transports(monkeypatch, remote):
    monkeypatch.setattr(PL, "_ado_rest_get", lambda *a: (True, {"remoteUrl": remote}, ""))
    with pytest.raises(ValueError, match="remote URI"):
        OA.repository_remote_url({"org": "https://example.test", "project": "p", "repository": "r"})


def bound_orchestrator(s, tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{"id": "finalize", "name": "Finalize", "steps": [
        {"id": "oneauth_common_pr", "name": "OneAuth", "kind": "external",
         "write_command": "create-oneauth-common-pr"}]}]}), encoding="utf-8")
    state = ReleaseState(release_id="r", ccd="2026-07-08", timezone="UTC",
                         versions=dict(s.context.release.versions))
    orch = Orchestrator(str(config), state, mocks={}, as_of=date(2026, 7, 8))
    revision.bind_initial(orch)
    path = tmp_path / "r" / "release-state.json"
    state.save(str(path))
    s.args.config = str(config)
    return orch, path


def locked_orchestrator(s):
    state = C.load_state(s.args.runs_root, s.args.release)
    return Orchestrator(s.args.config, state, mocks={}, as_of=date(2026, 7, 8))


def test_prepared_operations_match_durable_review_before_any_write(oneauth, tmp_path):
    s = oneauth
    orch, path = bound_orchestrator(s, tmp_path)
    plan = P.oneauth_plan(s.context, s.args)
    s.args.review_hash = W.review_hash(orch, "finalize", "oneauth_common_pr", plan)
    replans = []

    def planner():
        value = P.oneauth_plan(s.context, s.args)
        replans.append(value)
        return value

    def check_review():
        record = ReleaseState.load(str(path)).get_step("finalize", "oneauth_common_pr")
        assert record.execution["write_review"] == {
            "hash": s.args.review_hash, "approved_by": "reviewer"}
        assert record.status == "in_flight"
        assert record.execution["id"] == s.args.execution_id
        assert all(key not in record.execution for key in ("plan", "operations", "edits", "payload"))
        assert "base-binary.dat" not in path.read_text(encoding="utf-8")

    s.before_write = check_review
    with C.state_lock(s.args.runs_root, s.args.release):
        orch = locked_orchestrator(s)
        authorization = W.authorize(s.args, orch, "finalize", "oneauth_common_pr", planner)
        assert authorization.plan == plan
        assert len(replans) == 2 and all(p == plan for p in replans)
        P.execute_oneauth(authorization)
    assert [c[0] for c in s.calls] == ["push", "create"]
    assert s.calls[0][-1] == plan.as_dict()["operations"][0]["content"]


def test_stale_review_writes_neither_reservation_nor_provider(oneauth, tmp_path):
    s = oneauth
    orch, path = bound_orchestrator(s, tmp_path)
    plan = P.oneauth_plan(s.context, s.args)
    s.args.review_hash = W.review_hash(orch, "finalize", "oneauth_common_pr", plan)
    before = path.read_bytes()
    git(s.root, "config", "user.email", "another@example.test")
    with C.state_lock(s.args.runs_root, s.args.release):
        orch = locked_orchestrator(s)
        with pytest.raises(ValueError, match="hash is stale"):
            W.authorize(s.args, orch, "finalize", "oneauth_common_pr",
                        lambda: P.oneauth_plan(s.context, s.args))
    assert path.read_bytes() == before
    assert s.calls == []


def test_partial_failure_keeps_owned_attempt_and_cannot_replay(oneauth, tmp_path):
    s = oneauth
    orch, path = bound_orchestrator(s, tmp_path)
    plan = P.oneauth_plan(s.context, s.args)
    s.args.review_hash = W.review_hash(orch, "finalize", "oneauth_common_pr", plan)
    s.fail_create = True
    with C.state_lock(s.args.runs_root, s.args.release):
        orch = locked_orchestrator(s)
        authorization = W.authorize(s.args, orch, "finalize", "oneauth_common_pr",
                                    lambda: P.oneauth_plan(s.context, s.args))
        with pytest.raises(ValueError, match="offline PR failure"):
            P.execute_oneauth(authorization)
        calls = deepcopy(s.calls)
        with pytest.raises(ValueError, match="already started|uncertain"):
            W.authorize(s.args, orch, "finalize", "oneauth_common_pr", lambda: plan)
    record = ReleaseState.load(str(path)).get_step("finalize", "oneauth_common_pr")
    assert record.execution["id"] == s.args.execution_id
    assert record.status == "in_flight"
    assert s.calls == calls
    assert git(s.remote, "rev-parse", s.head_name) == plan.operations[0].content["commit_id"]
