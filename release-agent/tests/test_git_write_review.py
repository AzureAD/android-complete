"""Checked Git writer contracts, plus explicit local-Git integration coverage."""
from copy import deepcopy
from datetime import date
import base64
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from orchestrator import cli_common as C, git_write_plans as P, revision, write_review as W
from orchestrator.commands import integ_prs_cmd as IC, oneauth_pr_cmd as OC
from orchestrator.engine import Orchestrator
from orchestrator.state import ReleaseState
from steps.finalize import integ_prs as I
from steps.lib.mockctx import MISSING
from tools import git_review as G, oneauth as OA, prs as PR
from test_oneauth_merge_review import (
    commit as oneauth_commit, git as oneauth_git, oneauth as git_oneauth, write as oneauth_write,
)


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.longpaths=true", *args], cwd=root, env=G._environment(),
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit(root, message):
    git(root, "add", ".")
    git(root, "-c", "core.hooksPath=", "commit", "--quiet", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "--quiet", "-b", "dev", "--template=")
    git(root, "config", "user.name", "Offline Reviewer")
    git(root, "config", "user.email", "reviewer@example.test")
    git(root, "config", "core.autocrlf", "false")
    (root / "build.gradle").write_bytes(b'version = "dynamic"\n')
    (root / "source.txt").write_bytes(b"base\n")
    initial = commit(root, "initial")
    git(root, "checkout", "--quiet", "-b", "release-integration/1")
    (root / "build.gradle").write_bytes(b'version = "1"\n')
    (root / "version.txt").write_bytes(b"1\n")
    head = commit(root, "release")
    git(root, "branch", "working/release/1", head)
    git(root, "branch", "release/1", initial)
    git(root, "checkout", "--quiet", "dev")
    (root / "upstream.txt").write_bytes(b"upstream\n")
    base = commit(root, "upstream")
    git(root, "remote", "add", "origin", str(root))
    return SimpleNamespace(root=root, head=head, base=base, initial=initial)


def context(versions=None, **inputs):
    return SimpleNamespace(
        release=SimpleNamespace(versions=versions or {"common": "1", "msal": "2"}, release_id="r"),
        input=lambda key, default=MISSING: inputs.get(key, default))


def arguments(tmp_path=None):
    return SimpleNamespace(release="r", repos=["common"], pbi=None, pbi_title=None,
                           execute=False, reserve=False, execution_id=None, review_hash=None,
                           approved_by="reviewer", executor="offline-session", as_of=None,
                           runs_root=str(tmp_path) if tmp_path else "", config="")


class Permit:
    def __init__(self, plan):
        self.plan = plan
        self.validations = 0

    def validate(self):
        self.validations += 1


@pytest.fixture
def integration(request, tmp_path, monkeypatch):
    if getattr(request, "param", "memory") == "git":
        checkout = request.getfixturevalue("checkout")
        content = None
    else:
        checkout = SimpleNamespace(root=tmp_path / "checkout",
                                   head="a" * 40, base="b" * 40, initial="c" * 40)
        content = {
            "tree": "d" * 40, "parents": [checkout.head, checkout.base],
            "behind": 1, "merge": "merge-tree", "resolved_gradle_conflicts": [],
            "gradle_reverted": ["build.gradle"], "edits": {},
            "commit": "reviewed commit bytes\n", "commit_id": "e" * 40,
        }
        branch_tips = {
            "dev": checkout.base, "release-integration/1": checkout.head,
            "working/release/1": checkout.head, "release/1": checkout.initial,
        }

        def remote_tip(root, remote, name):
            assert Path(root) == checkout.root and remote == str(checkout.root)
            if name not in branch_tips:
                raise ValueError("Missing remote branch: " + name)
            return branch_tips[name]

        def plan_ri(root, head, base, target):
            assert Path(root) == checkout.root
            assert (head, base, target) == (checkout.head, checkout.base, "dev")
            return deepcopy(content)

        def no_process(*args, **kwargs):
            pytest.fail("Coordinator unit tests must use Git ports, not subprocesses")

        # Git's tree/transport behavior is covered by checkout and oneauth tests.
        # These cases exercise the coordinator, hashes and write fences instead.
        monkeypatch.setattr(subprocess, "Popen", no_process)
        monkeypatch.setattr(G, "branch", lambda name: name)
        monkeypatch.setattr(G, "clean_repository", lambda root: Path(root))
        monkeypatch.setattr(G, "remote_url", lambda root: str(checkout.root))
        monkeypatch.setattr(G, "remote_tip", remote_tip)
        monkeypatch.setattr(G, "plan_ri", plan_ri)

    cfg = dict(I.CONFIG["common"], dir=str(checkout.root), gh_repo="offline/review")
    monkeypatch.setitem(I.CONFIG, "common", cfg)
    states, tips, calls = {}, {}, []
    original_tip = G.remote_tip

    def remote_tip(root, remote, name):
        return tips[name] if name in tips else original_tip(root, remote, name)

    monkeypatch.setattr(G, "remote_tip", remote_tip)
    monkeypatch.setattr(PR, "provider_branch_object_id",
                        lambda target, name: remote_tip(target["root"], target["remote"], name))
    expected_remote = G.remote_url(checkout.root)
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: [expected_remote])
    monkeypatch.setattr(PR, "gh_find_open_pr",
                        lambda repo, head, base: (True, deepcopy(states.get((repo, head, base))), ""))

    def create(repo, head, base, title, body, labels):
        calls.append(("create", repo, head, base, title, body, labels))
        number = len(states) + 1
        states[(repo, head, base)] = dict(number=number, title=title, body=body, labels=labels,
                                         url=f"https://example.test/pr/{number}")
        return True, states[(repo, head, base)]["url"], ""

    def labels(repo, number, labels):
        calls.append(("labels", repo, number, labels))
        for pr in states.values():
            if pr["number"] == number:
                pr["labels"] = sorted(set(pr["labels"]) | set(labels))
        return True, ""

    def pbi(org, project, title):
        calls.append(("pbi", org, project, title))
        return True, 101, "https://example.test/pbi/101", ""

    def push(root, remote, name, expected, content, validate):
        validate()
        assert remote_tip(root, remote, name) == expected
        calls.append(("push", name, deepcopy(content)))
        tips[name] = content["commit_id"]
        return content["commit_id"]

    monkeypatch.setattr(PR, "gh_create_pr", create)
    monkeypatch.setattr(PR, "gh_ensure_labels", labels)
    monkeypatch.setattr(PR, "create_pbi", pbi)
    monkeypatch.setattr(G, "push_reviewed", push)
    return SimpleNamespace(context=context(repos=["common"]), args=arguments(), calls=calls,
                           states=states, tips=tips, checkout=checkout, content=content,
                           branch_tips=branch_tips if content is not None else None)


@pytest.fixture
def oneauth(git_oneauth, monkeypatch):
    state = git_oneauth
    state.context = context()
    # Keep the original non-merge coverage; the shared suite exercises divergent merges.
    oneauth_git(state.remote, "update-ref", "refs/heads/" + state.base_name, state.initial)
    oneauth_git(state.root, "checkout", "--quiet", state.head_name)
    state.base = state.initial

    def blocked(*args, **kwargs):
        pytest.fail("Network/token/server-side merge or unreviewed REST push is forbidden")

    for name in ("_token", "push_edits", "merge_dev_into_ingestion"):
        monkeypatch.setattr(OA, name, blocked)
    return state


def test_real_ri_plan_exact_merge_and_reverts_without_checkout_writes(checkout):
    root = checkout.root
    index = (root / ".git" / "index").read_bytes()
    refs = git(root, "show-ref")
    objects = sorted(str(p.relative_to(root)) for p in (root / ".git" / "objects").rglob("*"))
    plan = G.plan_ri(root, checkout.head, checkout.base, "dev")
    assert plan == G.plan_ri(root, checkout.head, checkout.base, "dev")
    assert plan["parents"] == [checkout.head, checkout.base]
    assert plan["merge"] == "merge-tree" and plan["behind"] == 1
    assert set(plan["edits"]) == {"build.gradle", "upstream.txt"}
    assert base64.b64decode(plan["edits"]["build.gradle"]["after"]["base64"]) == b'version = "dynamic"\n'
    assert base64.b64decode(plan["edits"]["upstream.txt"]["after"]["base64"]) == b"upstream\n"
    hashed = subprocess.run(["git", "hash-object", "-t", "commit", "--stdin"], cwd=root,
                            env=G._environment(), input=plan["commit"].encode(), capture_output=True)
    assert hashed.returncode == 0 and hashed.stdout.decode().strip() == plan["commit_id"]
    assert git(root, "show-ref") == refs
    assert (root / ".git" / "index").read_bytes() == index
    assert objects == sorted(str(p.relative_to(root)) for p in (root / ".git" / "objects").rglob("*"))
    assert not list(root.glob(".write-review-*"))
    assert not git(root, "status", "--porcelain")


@pytest.mark.parametrize("delete_gradle", [False, True])
def test_reviewed_git_executor_rehydrates_exact_commit_without_recomputing_merge(checkout, monkeypatch, delete_gradle):
    if delete_gradle:
        git(checkout.root, "rm", "build.gradle")
        checkout.base = commit(checkout.root, "delete Gradle on target")
    plan = G.plan_ri(checkout.root, checkout.head, checkout.base, "dev")
    original_git = G._git
    observed, current = [], [checkout.head]

    def run(root, *args, **kw):
        if args[0] == "push":
            observed.append((args, original_git(root, "cat-file", "commit", plan["commit_id"], env=kw["env"])))
            current[0] = plan["commit_id"]
            return b"offline push accepted"
        assert args[0] != "merge-tree"
        return original_git(root, *args, **kw)

    monkeypatch.setattr(G, "_git", run)
    monkeypatch.setattr(G, "remote_tip", lambda *args: current[0])
    validations = []
    assert G.push_reviewed(checkout.root, str(checkout.root), "release-integration/1",
                           checkout.head, plan, lambda: validations.append(True)) == plan["commit_id"]
    assert observed[0][1] == plan["commit"].encode()
    assert f"--force-with-lease=refs/heads/release-integration/1:{checkout.head}" in observed[0][0]
    assert validations == [True]
    assert not list(checkout.root.glob(".write-review-*"))
    assert git(checkout.root, "rev-parse", "release-integration/1") == checkout.head


@pytest.mark.parametrize("problem", ["dirty", "conflict", "missing-object", "symlink"])
def test_ri_holds_dirty_conflicts_missing_objects_unsupported_paths(checkout, problem):
    root, head, base = checkout.root, checkout.head, checkout.base
    if problem == "dirty":
        (root / "source.txt").write_text("dirty\n", encoding="utf-8")
    elif problem == "missing-object":
        head = "f" * 40
    elif problem == "symlink":
        blob = git(root, "rev-parse", base + ":source.txt")
        git(root, "update-index", "--add", "--cacheinfo", "120000", blob, "link")
        git(root, "commit", "--quiet", "-m", "link fixture")
        git(root, "reset", "--hard", "HEAD")
        base = git(root, "rev-parse", "HEAD")
    else:
        (root / "source.txt").write_text("dev edit\n", encoding="utf-8")
        base = commit(root, "conflicting dev")
        git(root, "checkout", "--quiet", "release-integration/1")
        (root / "source.txt").write_text("release edit\n", encoding="utf-8")
        head = commit(root, "conflicting release")
    with pytest.raises(ValueError):
        G.plan_ri(root, head, base, "dev")
    assert not list(root.glob(".write-review-*"))


def test_gradle_only_conflict_is_exactly_resolved_to_target(checkout):
    (checkout.root / "build.gradle").write_bytes(b'version = "new-dynamic"\n')
    base = commit(checkout.root, "changed dynamic")
    plan = G.plan_ri(checkout.root, checkout.head, base, "dev")
    assert plan["resolved_gradle_conflicts"] == ["build.gradle"]
    assert base64.b64decode(plan["edits"]["build.gradle"]["after"]["base64"]) == b'version = "new-dynamic"\n'


def test_gradle_deleted_on_target_is_reviewed_as_deletion(checkout):
    git(checkout.root, "rm", "build.gradle")
    base = commit(checkout.root, "remove target gradle")
    plan = G.plan_ri(checkout.root, checkout.head, base, "dev")
    assert plan["edits"]["build.gradle"]["after"] is None
    assert plan["resolved_gradle_conflicts"] == ["build.gradle"]


def test_legacy_conflict_preview_uses_isolated_objects(checkout):
    root = checkout.root
    git(root, "update-ref", "refs/remotes/origin/ri", checkout.head)
    git(root, "update-ref", "refs/remotes/origin/dev", checkout.base)
    before = sorted(str(p) for p in (root / ".git" / "objects").rglob("*"))
    assert PR.merge_conflict_preview(str(root), "ri", "dev") == (True, [], "")
    assert before == sorted(str(p) for p in (root / ".git" / "objects").rglob("*"))
    assert not list(root.glob(".write-review-*"))


def test_transport_preserves_credential_configuration_but_tree_planning_is_isolated(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "unreviewed")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "unreviewed")
    env = G._environment(isolated=False)
    assert "GIT_DIR" not in env and "GIT_CONFIG_GLOBAL" not in env
    assert "GIT_CONFIG_NOSYSTEM" not in env
    isolated = G._environment()
    assert isolated["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == isolated["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("integration", [
    "memory", pytest.param("git", marks=pytest.mark.git_integration),
], indirect=True)
def test_integration_plan_full_normalized_inputs_and_exact_payloads(integration):
    s = integration
    plan = P.integration_plan(s.context, s.args)
    value = plan.as_dict()
    assert value["parameters"]["repos"] == ["common"]
    assert value["parameters"]["pbi"]["title"] == "Android r release — integration PRs"
    assert [op.kind for op in plan.operations] == [
        "pbi.create", "integration.create", "integration.push", "integration.create"]
    assert plan.operations[0].target["org"] == I.PL.ENGINEERING_ORG
    assert plan.operations[2].preconditions["head_tip"] == s.checkout.head
    assert plan.operations[3].preconditions["head_tip"] == plan.operations[2].content["commit_id"]
    assert plan.operations[1].content["body"][1]["output"] == "pbi.id"
    assert s.calls == []
    with pytest.raises(TypeError):
        plan.parameters["pbi"]["title"] = "mutated"
    s.args.repos = ["common", "common"]
    assert P.integration_plan(s.context, s.args) == plan


@pytest.mark.parametrize("change", ["pbi-mode", "pbi-id", "pbi-title", "org", "project",
                                  "labels", "hosting", "title", "body", "existing", "content", "branches"])
def test_integration_review_hash_changes_for_every_write_input(integration, monkeypatch, change):
    s = integration
    original = revision.digest(P.integration_plan(s.context, s.args).as_dict())
    if change == "pbi-mode":
        s.context = context(repos=["common"], pbi="skip")
    elif change == "pbi-id":
        s.args.pbi = "99"
    elif change == "pbi-title":
        s.args.pbi_title = "Another reviewed title"
    elif change in ("org", "project"):
        monkeypatch.setattr(I.PL, "ENGINEERING_" + change.upper(), "different")
    elif change == "labels":
        monkeypatch.setitem(I.CONFIG["common"], "labels", ["different"])
    elif change == "hosting":
        monkeypatch.setitem(I.CONFIG["common"], "gh_repo", "different/repo")
    elif change == "branches":
        s.context = context(repos=["common"], branches={"common": {"r": "dev"}})
    elif change == "title":
        monkeypatch.setattr(I, "_title", lambda *args: "different title")
    elif change == "body":
        previous = I.pr_body
        monkeypatch.setattr(I, "pr_body", lambda *args: previous(*args) + "different body")
    elif change == "existing":
        s.states[("offline/review", "working/release/1", "release/1")] = {
            "number": 4, "url": "https://example.test/4", "title": "existing", "body": "old", "labels": []}
    else:
        s.content["tree"] = "f" * 40
    assert revision.digest(P.integration_plan(s.context, s.args).as_dict()) != original
    assert s.calls == []


@pytest.mark.parametrize("problem", ["unknown-repo", "unknown-tip", "lookup-error", "missing-branch"])
def test_integration_incomplete_preview_cannot_receive_a_hash(integration, monkeypatch, problem):
    s = integration
    if problem == "unknown-repo":
        s.args.repos = ["does-not-exist"]
    elif problem == "unknown-tip":
        monkeypatch.setattr(PR, "provider_branch_object_id", lambda *args: None)
    elif problem == "lookup-error":
        monkeypatch.setattr(PR, "gh_find_open_pr", lambda *args: (False, None, "unknown"))
    else:
        del s.branch_tips["release/1"]
    with pytest.raises(ValueError):
        P.integration_plan(s.context, s.args)
    assert s.calls == []


def test_integration_executor_uses_captured_payloads_not_late_config(integration, monkeypatch):
    s = integration
    auth = Permit(P.integration_plan(s.context, s.args))
    monkeypatch.setitem(I.CONFIG["common"], "gh_repo", "unreviewed/repo")
    monkeypatch.setitem(I.CONFIG["common"], "labels", ["unreviewed"])
    monkeypatch.setattr(I.PL, "ENGINEERING_ORG", "unreviewed")
    s.args.pbi_title = "unreviewed"
    summary = P.execute_integration(auth)
    assert [c[0] for c in s.calls] == ["pbi", "create", "push", "create"]
    assert all(c[1] == "offline/review" and "AB#101" in c[5]
               for c in s.calls if c[0] == "create")
    assert "unreviewed" not in repr(s.calls)
    assert auth.validations >= len(auth.plan.operations)
    assert "integ_prs:" in summary


def test_integration_drift_before_first_write_stops_entire_plan(integration):
    s = integration
    auth = Permit(P.integration_plan(s.context, s.args))
    s.tips["dev"] = "f" * 40
    with pytest.raises(ValueError, match="Branch changed"):
        P.execute_integration(auth)
    assert s.calls == []


def test_integration_matching_tips_do_not_authorize_a_different_hosting_repository(integration, monkeypatch):
    s = integration
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: ["https://example.test/another.git"])
    with pytest.raises(ValueError, match="hosting repository"):
        P.integration_plan(s.context, s.args)
    assert s.calls == []


def test_integration_hosting_identity_drift_rejects_before_writes(integration, monkeypatch):
    s = integration
    authorization = Permit(P.integration_plan(s.context, s.args))
    monkeypatch.setattr(PR, "provider_repository_urls", lambda target: ["https://example.test/another.git"])
    with pytest.raises(ValueError, match="hosting repository"):
        P.execute_integration(authorization)
    assert s.calls == []


def test_integration_created_pr_receipt_must_match_exact_readback(integration, monkeypatch):
    s = integration
    authorization = Permit(P.integration_plan(s.context, s.args))
    original = PR.gh_create_pr

    def wrong_receipt(*args, **kwargs):
        ok, url, detail = original(*args, **kwargs)
        return ok, url + "-wrong", detail

    monkeypatch.setattr(PR, "gh_create_pr", wrong_receipt)
    with pytest.raises(ValueError, match="identity/content uncertain"):
        P.execute_integration(authorization)
    assert [call[0] for call in s.calls] == ["pbi", "create"]


@pytest.mark.parametrize("url", [
    "https://org@dev.azure.com/org/Project/_git/Repo",
    "https://org.visualstudio.com/DefaultCollection/Project/_git/Repo",
    "https://org.visualstudio.com/Project/_git/Repo",
])
def test_public_ado_remote_aliases_have_one_provider_identity(url):
    assert G.remote_identity(url) == G.remote_identity(
        "https://dev.azure.com/org/Project/_git/Repo")


@pytest.mark.parametrize("url", [
    "https://token@dev.azure.com/org/Project/_git/Repo",
    "https://org:password@dev.azure.com/org/Project/_git/Repo",
    "https://token@example.test/repo", "http://example.test/repo",
])
def test_repository_credentials_never_enter_review(url):
    with pytest.raises(ValueError):
        G.remote_identity(url)


@pytest.mark.parametrize("tool", ["gh", "ado"])
def test_provider_repository_lookup_uses_reviewed_target(monkeypatch, tool):
    calls = []
    target = {"tool": tool, "gh_repo": "host.example.test/org/repo",
              "ado": {"org": "https://dev.azure.com/org", "project": "project", "repository": "repo"}}
    response = ({"clone_url": "https://host.example.test/org/repo.git", "ssh_url": "git@host:org/repo.git"}
                if tool == "gh" else {"remoteUrl": "https://dev.azure.com/org/project/_git/repo"})
    monkeypatch.setattr(PR, "_run", lambda args, **kw: (
        calls.append(args) or 0, json.dumps(response), ""))
    assert PR.provider_repository_urls(target) == list(response.values())
    assert "host.example.test" in calls[0] if tool == "gh" else "--repository" in calls[0]


def test_integration_existing_pr_choice_drift_stops_remaining_writes(integration, monkeypatch):
    s = integration
    auth = Permit(P.integration_plan(s.context, s.args))
    original = PR.create_pbi

    def create(*args):
        result = original(*args)
        s.states[("offline/review", "working/release/1", "release/1")] = {
            "number": 9, "title": "concurrent", "body": "", "labels": [], "url": "https://example.test/9"}
        return result

    monkeypatch.setattr(PR, "create_pbi", create)
    with pytest.raises(ValueError, match="choice"):
        P.execute_integration(auth)
    assert [c[0] for c in s.calls] == ["pbi"]


def test_existing_integration_prs_only_ensure_reviewed_labels(integration):
    s = integration
    for number, head, base in ((1, "working/release/1", "release/1"),
                               (2, "release-integration/1", "dev")):
        s.states[("offline/review", head, base)] = {
            "number": number, "title": "kept title", "body": "kept body",
            "labels": ["existing-label"], "url": f"https://example.test/{number}"}
    auth = Permit(P.integration_plan(s.context, s.args))
    assert [op.kind for op in auth.plan.operations] == ["integration.reuse", "integration.reuse"]
    P.execute_integration(auth)
    assert [c[0] for c in s.calls] == ["labels", "labels"]
    assert all(p["body"] == "kept body" and p["title"] == "kept title" for p in s.states.values())


def test_ado_integration_uses_exact_repository_and_work_item(integration, monkeypatch):
    s = integration
    target = {"org": "https://example.test/ado", "project": "Approved", "repository": "Repo"}
    monkeypatch.setitem(I.CONFIG["common"], "tool", "ado")
    monkeypatch.setitem(I.CONFIG["common"], "ado", target)
    s.args.pbi = "00042"
    created = {}

    def find(org, project, repo, head, base):
        assert (org, project, repo) == tuple(target.values())
        return True, deepcopy(created.get((head, base))), ""

    def create(org, project, repo, head, base, title, body, work_items):
        assert (org, project, repo) == tuple(target.values()) and work_items == "42"
        assert "AB#42" in body
        s.calls.append(("ado", head, base))
        created[(head, base)] = {"number": len(created) + 1, "title": title, "body": body,
                                 "url": "https://example.test/ado/pr"}
        return True, created[(head, base)]["url"], ""

    monkeypatch.setattr(PR, "az_find_open_pr", find)
    monkeypatch.setattr(PR, "az_create_pr", create)
    plan = P.integration_plan(s.context, s.args)
    assert plan.parameters["pbi"]["id"] == "42"
    monkeypatch.setitem(I.CONFIG["common"], "ado", {"org": "unreviewed"})
    P.execute_integration(Permit(plan))
    assert [c[0] for c in s.calls] == ["ado", "push", "ado"]


def test_oneauth_plan_exact_content_and_no_server_merge(oneauth):
    s = oneauth
    plan = P.oneauth_plan(s.context, s.args)
    assert plan == P.oneauth_plan(s.context, s.args)
    assert plan.parameters["merge"] == "none"
    assert plan.operations[0].preconditions["head_tip"] == s.head
    content = plan.as_dict()["operations"][0]["content"]
    assert len(content["edits"]) == len(content["version_edits"]) == 4
    assert OA.changelog_line("1", "2") in content["version_edits"]["CHANGELOG.md"]
    assert content["parents"] == [s.head]
    assert content["commit"].startswith(f"tree {content['tree']}\nparent {s.head}\n")
    assert plan.operations[1].preconditions["head_tip"] == content["commit_id"]
    assert G.object_id(content["commit_id"])
    assert s.calls == []


@pytest.mark.parametrize("change", ["common", "msal", "head", "base", "content", "existing"])
def test_oneauth_plan_hash_binds_versions_branches_and_content(oneauth, change):
    s = oneauth
    original = revision.digest(P.oneauth_plan(s.context, s.args).as_dict())
    if change in ("common", "msal"):
        s.context = context(**{change: "9"})
    elif change == "head":
        oneauth_git(s.root, "commit", "--quiet", "--allow-empty", "-m", "new ingestion tip")
        oneauth_git(s.root, "push", "--quiet", "origin", s.head_name)
    elif change == "base":
        oneauth_git(s.remote, "update-ref", "refs/heads/" + s.base_name, s.head)
    elif change == "content":
        changelog = (s.root / s.paths["changelog"][1:]).read_text(encoding="utf-8")
        oneauth_write(s.root, s.paths["changelog"], changelog + "An independent change.\n")
        oneauth_commit(s.root, "changed changelog")
        oneauth_git(s.root, "push", "--quiet", "origin", s.head_name)
    else:
        s.pr = {"id": 30, "title": "old", "description": "old", "url": "https://example.test/30"}
    assert revision.digest(P.oneauth_plan(s.context, s.args).as_dict()) != original
    assert s.calls == []


@pytest.mark.parametrize("change", ["org", "project", "repository", "head", "base", "path"])
def test_oneauth_hash_binds_provider_config_and_file_mapping(oneauth, monkeypatch, change):
    s = oneauth
    if change == "path":
        oneauth_write(s.root, "/deps/NEW.md",
                      (s.root / s.paths["readme"][1:]).read_text(encoding="utf-8"))
        oneauth_commit(s.root, "add equivalent dependency readme")
        oneauth_git(s.root, "push", "--quiet", "origin", s.head_name)
    original = revision.digest(P.oneauth_plan(s.context, s.args).as_dict())
    if change in ("org", "project", "repository"):
        value = "https://example.test/changed" if change == "org" else "changed"
        s.repository[change] = value
        monkeypatch.setattr(OA, {"org": "ORG", "project": "PROJECT", "repository": "REPO"}[change], value)
    elif change in ("head", "base"):
        oneauth_git(s.remote, "update-ref", "refs/heads/changed",
                    s.head if change == "head" else s.base)
        setattr(s, change + "_name", "changed")
        monkeypatch.setattr(OA, "INGEST_BRANCH" if change == "head" else "TARGET_BRANCH", "changed")
    else:
        monkeypatch.setattr(OA, "FILES", {**OA.FILES, "readme": "/deps/NEW.md"})
    assert revision.digest(P.oneauth_plan(s.context, s.args).as_dict()) != original
    assert s.calls == []


@pytest.mark.parametrize("problem", ["conflict", "unknown-ancestry", "unknown-tip", "missing-anchor",
                                    "lookup-error", "same-branch"])
def test_oneauth_incomplete_preview_is_held_without_a_review_hash(oneauth, monkeypatch, problem):
    s = oneauth
    if problem == "conflict":
        oneauth_git(s.root, "checkout", "--quiet", s.base_name)
        oneauth_write(s.root, s.paths["readme"], "conflicting base documentation\n")
        oneauth_commit(s.root, "base conflict")
        oneauth_git(s.root, "checkout", "--quiet", s.head_name)
        oneauth_write(s.root, s.paths["readme"], "conflicting ingestion documentation\n")
        oneauth_commit(s.root, "ingestion conflict")
        oneauth_git(s.root, "push", "--quiet", "origin", s.head_name, s.base_name)
    elif problem == "unknown-ancestry":
        monkeypatch.setattr(OA, "ahead_behind",
                            lambda *a, **kw: (True, {"ahead": 1, "behind": None}, ""))
    elif problem == "unknown-tip":
        monkeypatch.setattr(OA, "branch_object_id", lambda *a, **kw: (True, None, ""))
    elif problem == "missing-anchor":
        oneauth_write(s.root, s.paths["changelog"], "no anchor")
        oneauth_commit(s.root, "remove changelog anchors")
        oneauth_git(s.root, "push", "--quiet", "origin", s.head_name)
    elif problem == "same-branch":
        monkeypatch.setattr(OA, "INGEST_BRANCH", OA.TARGET_BRANCH)
    else:
        monkeypatch.setattr(OA, "find_open_pr", lambda *a, **kw: (False, None, "unknown"))
    messages = {"conflict": "Merge conflicts", "unknown-ancestry": "Unknown OneAuth ancestry",
                "unknown-tip": "Missing or invalid Git object ID", "missing-anchor": "Unreleased",
                "lookup-error": "OneAuth PR lookup", "same-branch": "branches must differ"}
    with pytest.raises(ValueError, match=messages[problem]):
        P.oneauth_plan(s.context, s.args)
    assert s.calls == []


def test_oneauth_execution_uses_captured_coordinates_and_exact_edits(oneauth, monkeypatch):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    monkeypatch.setattr(OA, "ORG", "unreviewed")
    monkeypatch.setattr(OA, "PROJECT", "unreviewed")
    monkeypatch.setattr(OA, "REPO", "unreviewed")
    assert "PR https://example.test/pr/12" in P.execute_oneauth(auth)
    assert [c[0] for c in s.calls] == ["push", "create"]
    content = auth.plan.as_dict()["operations"][0]["content"]
    assert s.calls[0][1:5] == (str(s.root), str(s.remote), s.head_name, s.head)
    assert s.calls[0][-1] == content
    assert s.calls[1][1] == s.repository
    assert oneauth_git(s.remote, "rev-parse", s.head_name) == content["commit_id"]
    assert oneauth_git(s.remote, "cat-file", "commit", content["commit_id"]) == content["commit"].rstrip("\n")


@pytest.mark.parametrize("drift", ["base", "head", "content", "pr"])
def test_oneauth_provider_preconditions_stop_all_writes(oneauth, monkeypatch, drift):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    if drift == "base":
        oneauth_git(s.remote, "update-ref", "refs/heads/" + s.base_name, s.head)
    elif drift == "head":
        oneauth_git(s.remote, "update-ref", "refs/heads/" + s.head_name, s.initial)
    elif drift == "content":
        read = OA.read_text

        def changed_read(path, *args, **kwargs):
            ok, text, error = read(path, *args, **kwargs)
            return ok, text + "unexpected" if path == s.paths["readme"] else text, error

        monkeypatch.setattr(OA, "read_text", changed_read)
    else:
        s.pr = {"id": 10, "title": "other", "description": "other", "url": "https://example.test/10"}
    with pytest.raises(ValueError, match="changed"):
        P.execute_oneauth(auth)
    assert s.calls == []


def test_oneauth_partial_failure_is_not_silently_retried(oneauth):
    s = oneauth
    auth = Permit(P.oneauth_plan(s.context, s.args))
    s.fail_create = True
    with pytest.raises(ValueError, match="offline PR failure"):
        P.execute_oneauth(auth)
    assert [c[0] for c in s.calls] == ["push", "create"]
    assert oneauth_git(s.remote, "rev-parse", s.head_name) == auth.plan.operations[0].content["commit_id"]
    with pytest.raises(ValueError, match="branch changed"):
        P.execute_oneauth(auth)
    assert [c[0] for c in s.calls] == ["push", "create"]


def test_oneauth_noop_reuses_exact_reviewed_pr(oneauth):
    s = oneauth
    files = {key: (s.root / path[1:]).read_text(encoding="utf-8") for key, path in s.paths.items()}
    for path, text in OA.apply_edits(files, "1", "2", paths=s.paths).items():
        oneauth_write(s.root, path, text)
    head = oneauth_commit(s.root, "already ingested")
    oneauth_git(s.root, "push", "--quiet", "origin", s.head_name)
    s.pr = {"id": 10, "title": "old title", "description": "old body", "url": "https://example.test/10"}
    auth = Permit(P.oneauth_plan(s.context, s.args))
    assert [op.kind for op in auth.plan.operations] == ["oneauth.reuse"]
    assert auth.plan.operations[0].content["title"] == "old title"
    assert auth.plan.operations[0].content["body"] == "old body"
    assert "https://example.test/10" in P.execute_oneauth(auth)
    assert oneauth_git(s.remote, "rev-parse", s.head_name) == head
    assert s.calls == []


def bound_command(tmp_path, monkeypatch, step):
    command = IC if step == "integ_prs" else OC
    write_command = "create-integration-prs" if step == "integ_prs" else "create-oneauth-common-pr"
    config = tmp_path / "phases.yaml"
    config.write_text(yaml.safe_dump({"phases": [{"id": "finalize", "name": "Finalize", "steps": [
        {"id": step, "name": step, "kind": "external", "write_command": write_command}]}]}),
        encoding="utf-8")
    state = ReleaseState(release_id="r", ccd="2026-07-08", timezone="UTC",
                         versions={"common": "1", "msal": "2"})
    orch = Orchestrator(str(config), state, mocks={}, as_of=date(2026, 7, 8))
    revision.bind_initial(orch)
    path = tmp_path / "r" / "release-state.json"
    state.save(str(path))
    args = arguments(tmp_path)
    args.config = str(config)
    def load(*args):
        def checkpoint():
            assert C._LOCKED_STATE.get() is not None
            state.save(str(path))
        state._checkpoint = checkpoint
        return state, orch
    monkeypatch.setattr(C, "load_orch", load)
    monkeypatch.setattr(C, "emit", lambda *a, **kw: None)
    return command, args, orch, path


def test_oneauth_checked_command_hash_and_partial_ownership(oneauth, tmp_path, monkeypatch):
    s = oneauth
    _, args, orch, path = bound_command(tmp_path, monkeypatch, "oneauth_common_pr")
    args.repo_dir = str(s.root)
    plan = P.oneauth_plan(orch.context("finalize", "oneauth_common_pr"), args)
    args.review_hash = W.review_hash(orch, "finalize", "oneauth_common_pr", plan)
    args.execute = True
    s.fail_create = True
    with C.state_lock(args.runs_root, args.release):
        assert OC.cmd_create_oneauth_common_pr(args) == 2
    saved = ReleaseState.load(str(path))
    record = saved.get_step("finalize", "oneauth_common_pr")
    assert record.execution["id"] == args.execution_id
    assert record.execution["write_review"] == {"hash": args.review_hash, "approved_by": "reviewer"}
    assert record.status != "done"
    assert "edits" not in record.execution
    calls = list(s.calls)
    with C.state_lock(args.runs_root, args.release):
        assert OC.cmd_create_oneauth_common_pr(args) == 1
    assert s.calls == calls


def test_integration_checked_command_success_checkpoint_and_stale_hash(integration, tmp_path, monkeypatch):
    s = integration
    _, args, orch, path = bound_command(tmp_path, monkeypatch, "integ_prs")
    args.pbi = "42"
    plan = P.integration_plan(orch.context("finalize", "integ_prs"), args)
    args.review_hash = W.review_hash(orch, "finalize", "integ_prs", plan)
    args.execute = True
    args.pbi = "43"
    with C.state_lock(args.runs_root, args.release):
        assert IC.cmd_create_integration_prs(args) == 1
    assert s.calls == [] and not orch.state.get_step("finalize", "integ_prs").execution
    args.pbi = "42"
    with C.state_lock(args.runs_root, args.release):
        assert IC.cmd_create_integration_prs(args) == 0
    assert ReleaseState.load(str(path)).get_step("finalize", "integ_prs").status == "done"


def test_commands_preview_never_save_or_write(oneauth, tmp_path, monkeypatch, capsys):
    _, args, orch, path = bound_command(tmp_path, monkeypatch, "oneauth_common_pr")
    args.repo_dir = str(oneauth.root)
    before = path.read_bytes()
    monkeypatch.setattr(C, "save_state", lambda *a: pytest.fail("Preview saved state"))
    assert OC.cmd_create_oneauth_common_pr(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["permission_to_execute"] is False
    assert revision.is_hash(result["review_hash"])
    assert not orch.state.get_step("finalize", "oneauth_common_pr").execution
    assert path.read_bytes() == before and oneauth.calls == []


def test_provider_urls_and_push_parent_are_explicit(monkeypatch):
    from tools import pipelines as pipelines
    target = {"org": "https://example.test/approved", "project": "A B", "repository": "R"}
    calls = []

    def send(url, method, body, timeout):
        calls.append((url, method, body))
        return True, {"commits": [{"commitId": "c" * 40}], "pullRequestId": 77}, ""

    monkeypatch.setattr(pipelines, "_ado_rest_send", send)
    monkeypatch.setattr(OA, "_BASE", "https://example.test/unreviewed")
    assert OA.push_edits("ingest", "a" * 40, {"/path": "exact"}, "reviewed", repository=target)[0]
    assert OA.create_pr("ingest", "dev", "title", "body", repository=target)[0]
    assert all(url.startswith("https://example.test/approved/A%20B/_apis/git/repositories/R/")
               for url, _, _ in calls)
    assert calls[0][2]["refUpdates"] == [{"name": "refs/heads/ingest", "oldObjectId": "a" * 40}]
    assert calls[0][2]["commits"][0]["changes"][0]["newContent"]["content"] == "exact"


@pytest.mark.parametrize("problem", ["merge", "extra-file", "wrong-comment", "unreadable", "none"])
def test_oneauth_returned_commit_must_match_reviewed_semantics(monkeypatch, problem):
    from tools import pipelines as pipelines
    target = {"org": "https://example.test/approved", "project": "P", "repository": "R"}
    old, new = "a" * 40, "c" * 40
    calls = []

    def get(url, timeout):
        calls.append(url)
        if problem == "unreadable":
            return False, None, "unknown"
        if "/changes?" in url:
            changes = [{"changeType": "edit", "item": {"path": "/path"}}]
            if problem == "extra-file":
                changes.append({"changeType": "edit", "item": {"path": "/extra"}})
            return True, {"changes": changes}, ""
        return True, {"parents": [old, "b" * 40] if problem == "merge" else [old],
                      "comment": "other" if problem == "wrong-comment" else "reviewed"}, ""

    monkeypatch.setattr(pipelines, "_ado_rest_get", get)
    if problem == "none":
        OA.verify_edit_commit(target, old, new, {"/path": "exact"}, "reviewed")
        assert len(calls) == 2
    else:
        with pytest.raises(ValueError):
            OA.verify_edit_commit(target, old, new, {"/path": "exact"}, "reviewed")
    assert all(url.startswith("https://example.test/approved/P/_apis/git/repositories/R/")
               for url in calls)
