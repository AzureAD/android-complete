# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Pinned integration resolutions using real, offline Git object databases."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import git_write_plans as P, integration_resolutions as R, revision
from orchestrator.commands import integ_prs_cmd as IC
from steps.finalize import integ_prs as I
from tools import git_review as G, prs as PR
from test_git_write_review import Permit, arguments, commit, context, git


pytestmark = pytest.mark.git_integration


VALUES = {"broker_adAccountsVersion": "16.7.0", "broker_commonVersion": "25.0.0",
          "broker_msalVersion": "8.6.0"}
VERSIONS = {"broker": "16.7.0", "common": "25.0.0", "msal": "8.6.0",
            "authenticator": "release/2026/10/15"}


def properties(values):
    return "unchanged=true\n" + "".join(f"{k}={v}\n" for k, v in values.items()) + "# retained\n"


def spec_for(s):
    ancestor = git(s.root, "merge-base", "--all", s.head, s.base)
    choice = {"take": "base" if s.repo == "msal" else "head"}
    for side, oid in (("ancestor", ancestor), ("head", s.head), ("base", s.base)):
        mode, _, entry = G._tree(s.root, oid, G._environment())[s.path]
        choice[side] = {"mode": mode, "object_id": entry}
    if s.repo == "authenticator":
        choice["values"] = dict(VALUES)
    return {"repo": s.repo, "root": str(s.root), "head": s.head, "base": s.base,
            "ancestor": ancestor, "paths": {s.path: choice}}


def write_spec(s):
    value = deepcopy(s.spec)
    value.pop("repo")
    s.file.write_text(json.dumps({"version": 1, "repos": {s.repo: value}}), encoding="utf-8")


@pytest.fixture(scope="module", params=["msal", "authenticator"])
def conflict_history(tmp_path_factory, request):
    repo = request.param
    root = tmp_path_factory.mktemp("conflict-history") / repo
    root.mkdir()
    main = "dev" if repo == "msal" else "working"
    token = "8.6.0" if repo == "msal" else "2026/10/15"
    head_name = "release-integration/" + token
    path = "common" if repo == "msal" else "PhoneFactor/gradle.properties"
    git(root, "init", "--quiet", "-b", main, "--template=")
    git(root, "config", "user.name", "Offline Reviewer")
    git(root, "config", "user.email", "reviewer@example.test")
    git(root, "config", "core.autocrlf", "false")
    (root / "source.txt").write_bytes(b"ancestor\n")
    (root / "build.gradle").write_bytes(b'version="dynamic"\n')
    if repo == "msal":
        (root / "common").mkdir()
        git(root, "update-index", "--add", "--cacheinfo", "160000", "a" * 40, "common")
    else:
        (root / path).parent.mkdir()
        (root / path).write_bytes(properties({k: "0.0.1" for k in VALUES}).encode())
    ancestor = commit(root, "ancestor")
    git(root, "checkout", "--quiet", "-b", head_name)
    if repo == "msal":
        git(root, "update-index", "--cacheinfo", "160000", "b" * 40, "common")
    else:
        (root / path).write_bytes(properties(VALUES).encode())
    (root / "head-only.txt").write_bytes(b"preserve release change\n")
    (root / "build.gradle").write_bytes(b'version="final"\n')
    head = commit(root, "release")
    git(root, "branch", ("working/release/" if repo == "msal" else "working-release/") + token)
    git(root, "branch", "release/" + token, ancestor)
    git(root, "checkout", "--quiet", main)
    if repo == "msal":
        git(root, "update-index", "--cacheinfo", "160000", "c" * 40, "common")
    else:
        (root / path).write_bytes(properties({k: v + "-rc2-ecs" for k, v in VALUES.items()}).encode())
    (root / "base-only.txt").write_bytes(b"preserve mainline change\n")
    base = commit(root, "mainline")
    return SimpleNamespace(root=root, repo=repo, main=main, head=head, base=base,
                           path=path, head_name=head_name, token=token, ancestor=ancestor)


@pytest.fixture
def conflicted(tmp_path, conflict_history, monkeypatch):
    history = conflict_history
    repo, root = history.repo, tmp_path / history.repo
    git(tmp_path, "clone", "--quiet", "--shared", "--template=", str(history.root), str(root))
    git(root, "config", "user.name", "Offline Reviewer")
    git(root, "config", "user.email", "reviewer@example.test")
    git(root, "config", "core.autocrlf", "false")
    git(root, "branch", history.head_name, history.head)
    git(root, "branch", ("working/release/" if repo == "msal" else "working-release/") + history.token,
        history.head)
    git(root, "branch", "release/" + history.token, history.ancestor)
    git(root, "remote", "set-url", "origin", str(root))
    s = SimpleNamespace(root=root, repo=repo, main=history.main, head=history.head,
                        base=history.base, path=history.path, head_name=history.head_name,
                        file=tmp_path / "resolutions.json")
    s.spec = spec_for(s)
    write_spec(s)
    cfg = dict(I.CONFIG[repo], dir=str(root), tool="gh", gh_repo="offline/review", target=s.main)
    monkeypatch.setitem(I.CONFIG, repo, cfg)
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: [str(root)])
    monkeypatch.setattr(PR, "provider_branch_object_id",
                        lambda target, name: G.remote_tip(root, str(root), name))
    monkeypatch.setattr(PR, "gh_find_open_pr", lambda *args: (True, None, ""))
    s.calls = []
    def forbidden(*args, **kwargs):
        s.calls.append(args)
        pytest.fail("No provider writer is allowed")
    for name in ("create_pbi", "gh_create_pr", "az_create_pr", "gh_ensure_labels"):
        monkeypatch.setattr(PR, name, forbidden)
    s.args = arguments(tmp_path)
    s.args.repos = [repo]
    s.args.resolutions_file = str(s.file)
    s.context = context(versions=VERSIONS)
    return s


def plan(s):
    return G.plan_ri(s.root, s.head, s.base, s.main, resolution=s.spec)


def test_default_conflicts_still_hold(conflicted):
    s = conflicted
    with pytest.raises(ValueError, match="Merge conflicts require human"):
        G.plan_ri(s.root, s.head, s.base, s.main)


def test_exact_explicit_tree_and_proof_without_persistent_changes(conflicted):
    s = conflicted
    refs, index = git(s.root, "show-ref"), (s.root / ".git" / "index").read_bytes()
    objects = sorted(str(p) for p in (s.root / ".git" / "objects").rglob("*"))
    content = plan(s)
    proof = content["resolved_conflicts"][s.path]
    assert proof["after"] == proof[s.spec["paths"][s.path]["take"]]
    if s.repo == "msal":
        assert content["edits"]["common"]["after"] == {
            "mode": "160000", "object_id": "c" * 40, "kind": "gitlink"}
    else:
        assert proof["after"]["text"] == properties(VALUES)
        assert s.path not in content["edits"]
    assert "head-only.txt" not in content["edits"]
    assert content["edits"]["base-only.txt"]["after"]["text"] == "preserve mainline change\n"
    assert content["edits"]["build.gradle"]["after"]["text"] == 'version="dynamic"\n'
    assert git(s.root, "show-ref") == refs
    assert (s.root / ".git" / "index").read_bytes() == index
    assert sorted(str(p) for p in (s.root / ".git" / "objects").rglob("*")) == objects
    G.clean_repository(s.root)
    assert not list(s.root.glob(".write-review-*"))


def test_real_local_push_rehydrates_exact_tree_and_preserves_worktree(conflicted):
    s = conflicted
    content = plan(s)
    assert G.push_reviewed(s.root, str(s.root), s.head_name, s.head, content, lambda: None) == content["commit_id"]
    final = G._tree(s.root, content["commit_id"], G._environment())
    chosen = s.spec["paths"][s.path][s.spec["paths"][s.path]["take"]]
    assert final[s.path][2] == chosen["object_id"]
    assert "head-only.txt" in final and "base-only.txt" in final
    assert git(s.root, "rev-parse", "HEAD") == s.base
    G.clean_repository(s.root)


@pytest.mark.parametrize("change", ["root", "head", "base", "ancestor", "entry", "choice"])
def test_stale_or_different_resolution_rejected(conflicted, change, tmp_path):
    s = conflicted
    if change == "root":
        s.spec["root"] = str(tmp_path)
    elif change in ("head", "base", "ancestor"):
        s.spec[change] = "d" * 40
    elif change == "entry":
        s.spec["paths"][s.path]["head"]["object_id"] = "d" * 40
    else:
        s.spec["paths"][s.path]["take"] = "head" if s.repo == "msal" else "base"
    with pytest.raises(ValueError):
        plan(s)
    assert not s.calls


@pytest.mark.parametrize("path", ["source.txt", "../common", "/common", "a\\common", ".git/config"])
def test_extra_or_unsafe_paths_rejected(conflicted, path):
    s = conflicted
    s.spec["paths"][path] = deepcopy(s.spec["paths"][s.path])
    with pytest.raises(ValueError):
        plan(s)


def test_symlink_mode_rejected(conflicted):
    s = conflicted
    s.spec["paths"][s.path]["head"]["mode"] = "120000"
    with pytest.raises(ValueError):
        plan(s)


def test_unapproved_additional_conflict_rejected(conflicted):
    s = conflicted
    git(s.root, "checkout", "--quiet", s.head_name)
    (s.root / "source.txt").write_text("release conflict\n", encoding="utf-8")
    s.head = commit(s.root, "additional release conflict")
    git(s.root, "checkout", "--quiet", s.main)
    (s.root / "source.txt").write_text("mainline conflict\n", encoding="utf-8")
    s.base = commit(s.root, "additional mainline conflict")
    s.spec = spec_for(s)
    with pytest.raises(ValueError, match="source.txt"):
        plan(s)


def test_nonconflict_resolution_rejected(conflicted):
    s = conflicted
    if s.repo == "msal":
        git(s.root, "update-index", "--cacheinfo", "160000", "b" * 40, "common")
    else:
        (s.root / s.path).write_text(properties(VALUES), encoding="utf-8")
    s.base = commit(s.root, "same entry no longer conflicts")
    s.spec = spec_for(s)
    with pytest.raises(ValueError):
        plan(s)


def test_properties_cannot_discard_other_parent_edits(conflicted):
    s = conflicted
    if s.repo != "authenticator":
        return
    with (s.root / s.path).open("a", encoding="utf-8") as out:
        out.write("other_upstream_change=preserve\n")
    s.base = commit(s.root, "additional property change")
    s.spec = spec_for(s)
    with pytest.raises(ValueError, match="exceed the three"):
        plan(s)


def test_checked_plan_hash_binds_file_and_resolution_inputs(conflicted):
    s = conflicted
    first = P.integration_plan(s.context, s.args)
    assert first.parameters["resolutions"]["repos"][s.repo] == s.spec
    s.file.write_text(s.file.read_text() + "\n", encoding="utf-8")
    second = P.integration_plan(s.context, s.args)
    assert revision.digest(first.as_dict()) != revision.digest(second.as_dict())
    assert not s.calls


def test_checked_executor_rejects_changed_resolution_file_before_provider_writes(conflicted):
    s = conflicted
    authorization = Permit(P.integration_plan(s.context, s.args))
    s.file.write_text(s.file.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after review"):
        P.execute_integration(authorization)
    assert not s.calls


def test_checked_executor_replans_instead_of_substituting_content(conflicted, monkeypatch):
    s = conflicted
    authorization = Permit(P.integration_plan(s.context, s.args))
    original = G.plan_ri
    def different(*args, **kwargs):
        result = original(*args, **kwargs)
        result["tree"] = "f" * 40
        return result
    monkeypatch.setattr(G, "plan_ri", different)
    with pytest.raises(ValueError, match="plan changed"):
        P.execute_integration(authorization)
    assert not s.calls


def test_verified_push_continuation_does_not_reapply_old_conflict_pins(conflicted):
    s = conflicted
    first = P.integration_plan(s.context, s.args)
    push = next(op.as_dict() for op in first.operations if op.kind == "integration.push")
    target, content = push["target"], push["content"]
    G.push_reviewed(s.root, str(s.root), s.head_name, s.head, content, lambda: None)
    completed = P._operation_key("integration.push", target, content["commit_id"])
    progress = SimpleNamespace(
        scope=revision.digest(P._integration_scope(s.context, s.args)),
        has=lambda key: key == completed,
        outputs=lambda: {"pbi.id": "101"},
        manual_push=lambda *args: False,
    )
    remaining = P.integration_plan(s.context, s.args, progress=progress)
    assert not any(op.kind in ("pbi.create", "integration.push") for op in remaining.operations)
    integration = next(op for op in remaining.operations if op.target.get("head") == s.head_name)
    assert integration.preconditions["head_tip"] == content["commit_id"]
    assert not s.calls


def test_gitlink_or_blob_proof_cannot_be_substituted(conflicted):
    s = conflicted
    content = plan(s)
    content["resolved_conflicts"][s.path]["after"]["object_id"] = "d" * 40
    with pytest.raises(ValueError, match="proof changed"):
        G.push_reviewed(s.root, str(s.root), s.head_name, s.head, content, lambda: None)
    assert git(s.root, "rev-parse", s.head_name) == s.head


def test_real_cas_rejects_concurrent_branch_change(conflicted, monkeypatch):
    s = conflicted
    content, original = plan(s), G._git
    attempted = []
    def race(root, *args, **kwargs):
        if args[0] == "push":
            assert f"--force-with-lease=refs/heads/{s.head_name}:{s.head}" in args
            attempted.append(True)
            git(s.root, "update-ref", "refs/heads/" + s.head_name, s.base, s.head)
        return original(root, *args, **kwargs)
    monkeypatch.setattr(G, "_git", race)
    with pytest.raises(ValueError, match="Git push failed"):
        G.push_reviewed(s.root, str(s.root), s.head_name, s.head, content, lambda: None)
    assert attempted == [True]
    assert git(s.root, "rev-parse", s.head_name) == s.base


@pytest.mark.parametrize("change", ["version", "unknown", "excluded", "duplicate", "versions"])
def test_resolution_file_schema_is_strict(conflicted, change):
    s = conflicted
    value = json.loads(s.file.read_text())
    selected = [s.repo]
    if change == "version":
        value["version"] = True
    elif change == "unknown":
        value["extra"] = {}
    elif change == "excluded":
        selected = []
    elif change == "duplicate":
        s.file.write_text('{"version":1,"version":1,"repos":{}}', encoding="utf-8")
    elif s.repo == "authenticator":
        value["repos"][s.repo]["paths"][s.path]["values"]["broker_msalVersion"] = "0.0.2"
    else:
        value["repos"][s.repo]["paths"][s.path]["extra"] = True
    if change != "duplicate":
        s.file.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        R.load(str(s.file), selected, VERSIONS)


def test_resolutions_cli_argument():
    parser = argparse.ArgumentParser()
    IC.register(parser.add_subparsers())
    args = parser.parse_args(["create-integration-prs", "--release", "r",
                              "--resolutions-file", str(Path.cwd() / "resolutions.json")])
    assert args.resolutions_file.endswith("resolutions.json")
    assert not args.execute
