"""Cross-host pull-request + git helpers for the Phase-4 `integ_prs` step.

Two hosting worlds, one façade:
  * GitHub.com and GitHub Enterprise (msft.ghe.com) are driven by the `gh` CLI
    (interactive auth already established on this machine — NO saved tokens).
  * Azure DevOps (msazure/One — the authenticator repo) uses `az repos`.

The release repos (common, msal, broker, authenticator) are checked out on disk
under the android-complete root, so BRANCH-level reads/edits use plain git against
those clones, while PR-level operations (list / create / label) go through gh / az.

Everything is best-effort and returns typed tuples (never raises into the engine):
  * branch/PR reads return (ok, value, detail)
  * writes return (ok, detail)

READ helpers are always safe. WRITE helpers (create PR, push RI) are only ever
called by the `create-integration-prs` command when NOT in dry-run — this module
does not decide dry-run, the caller does.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# android-complete root = <root>/release-agent/tools/prs.py -> parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(args, cwd=None, timeout=120):
    """Run a command; return (returncode, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:  # noqa: BLE001 — surface as a failed command, not a crash
        return 1, "", f"{type(e).__name__}: {e}"


def repo_dir(dir_name: str) -> Path:
    return REPO_ROOT / dir_name


# Read-only git queries run with the commit-graph disabled: some of the on-disk
# clones have a corrupt commit-graph cache, which breaks merge-tree/rev-list even
# though the underlying objects are fine. -c core.commitGraph=false sidesteps it
# without mutating the user's repo.
_GITC = ["git", "-c", "core.commitGraph=false"]


# --------------------------------------------------------------------------- git
def git_fetch(dir_name: str, timeout=180):
    """Fetch origin (prune) so ls-remote/rev-list reflect the remote. (ok, detail)."""
    rc, _o, e = _run(["git", "fetch", "--prune", "origin"], cwd=str(repo_dir(dir_name)),
                     timeout=timeout)
    return (rc == 0, e.strip() or "fetched")


def remote_branch_exists(dir_name: str, branch: str, timeout=60):
    """(ok, exists, detail) — is `branch` present on origin?"""
    rc, out, e = _run(["git", "ls-remote", "--heads", "origin", branch],
                      cwd=str(repo_dir(dir_name)), timeout=timeout)
    if rc != 0:
        return (False, False, e.strip() or "ls-remote failed")
    return (True, bool(out.strip()), "")


def behind_count(dir_name: str, head: str, base: str, timeout=60):
    """(ok, n, detail) — number of commits on origin/`base` NOT in origin/`head`
    (i.e. how far `head` is BEHIND `base`). 0 = head already contains base."""
    rc, out, e = _run(
        _GITC + ["rev-list", "--count", f"origin/{head}..origin/{base}"],
        cwd=str(repo_dir(dir_name)), timeout=timeout)
    if rc != 0:
        return (False, None, e.strip() or "rev-list failed")
    try:
        return (True, int(out.strip() or "0"), "")
    except ValueError:
        return (False, None, f"unexpected rev-list output: {out!r}")


def gradle_diff_files(dir_name: str, head: str, base: str, timeout=60):
    """(ok, files, detail) — build.gradle files that DIFFER between origin/base and
    origin/head (three-dot: changes on head relative to the merge-base with base).
    These are the files `integ_prs` reverts so the target stays dynamic."""
    rc, out, e = _run(
        _GITC + ["diff", "--name-only", f"origin/{base}...origin/{head}"],
        cwd=str(repo_dir(dir_name)), timeout=timeout)
    if rc != 0:
        return (False, None, e.strip() or "diff failed")
    files = [ln.strip() for ln in out.splitlines()
             if ln.strip().endswith("build.gradle") or ln.strip().endswith("build.gradle.kts")]
    return (True, files, "")


def merge_conflict_preview(dir_name: str, head: str, base: str, timeout=90):
    """(ok, conflicts, detail) — best-effort list of paths that WOULD conflict when
    merging origin/base INTO origin/head, computed with `git merge-tree` WITHOUT
    touching the working tree. `conflicts` is a list of file paths (possibly empty).

    Uses the modern `git merge-tree --write-tree` form; if the git is too old it
    returns ok=False so the caller can degrade gracefully."""
    rc, out, e = _run(
        _GITC + ["merge-tree", "--write-tree", "--name-only",
                 f"origin/{head}", f"origin/{base}"],
        cwd=str(repo_dir(dir_name)), timeout=timeout)
    # merge-tree exit: 0 = clean, 1 = conflicts (with a conflict list on stdout).
    if rc not in (0, 1):
        return (False, None, e.strip() or "merge-tree unsupported")
    if rc == 0:
        return (True, [], "")
    # stdout (conflict case): line 0 is the written-tree OID, then the conflicted
    # file paths, then a BLANK line, then informational messages. Collect the paths
    # between the OID and that blank line.
    lines = out.splitlines()
    paths = []
    for ln in lines[1:]:
        if not ln.strip():
            break
        paths.append(ln.strip())
    return (True, paths, "")


# ---------------------------------------------------------------------------- gh
def gh_find_open_pr(gh_repo: str, head: str, base: str, timeout=60):
    """(ok, pr|None, detail) — an OPEN PR with this head->base, or None. `pr` is
    {number, url, title}. `gh_repo` is the value passed to gh --repo (either
    'owner/repo' for github.com or 'host/owner/repo' for GHE)."""
    rc, out, e = _run(
        ["gh", "pr", "list", "--repo", gh_repo, "--head", head, "--base", base,
         "--state", "open", "--json", "number,url,title"], timeout=timeout)
    if rc != 0:
        return (False, None, e.strip() or "gh pr list failed")
    try:
        arr = json.loads(out or "[]")
    except json.JSONDecodeError:
        return (False, None, f"unparseable gh output: {out!r}")
    return (True, (arr[0] if arr else None), "")


def gh_create_pr(gh_repo: str, head: str, base: str, title: str, body: str,
                 labels=None, draft=False, timeout=120):
    """(ok, url, detail) — create a PR. WRITE — only call when not dry-run."""
    args = ["gh", "pr", "create", "--repo", gh_repo, "--head", head, "--base", base,
            "--title", title, "--body", body]
    for lb in (labels or []):
        args += ["--label", lb]
    if draft:
        args.append("--draft")
    rc, out, e = _run(args, timeout=timeout)
    if rc != 0:
        return (False, "", e.strip() or "gh pr create failed")
    return (True, out.strip().splitlines()[-1] if out.strip() else "", "")


def gh_ensure_labels(gh_repo: str, number, labels, timeout=60):
    """(ok, detail) — add labels to an existing PR (idempotent). WRITE."""
    if not labels:
        return (True, "no labels")
    args = ["gh", "pr", "edit", str(number), "--repo", gh_repo]
    for lb in labels:
        args += ["--add-label", lb]
    rc, _o, e = _run(args, timeout=timeout)
    return (rc == 0, e.strip() or "labels added")


def gh_release_exists(gh_repo: str, tag: str, timeout=60):
    """(ok, published, info, detail) — is a GitHub release published at `tag` in `gh_repo`?

    READ-ONLY (`gh release view`). `gh_repo` is the value passed to gh --repo ('owner/repo' for
    github.com or 'host/owner/repo' for GitHub Enterprise). `published` is True only for a real,
    non-draft release; a missing tag → (True, False, ...) so the caller can poll; a genuine error
    (auth/network) → (False, ...). `info` is {tag, name, url, draft} when found."""
    rc, out, e = _run(
        ["gh", "release", "view", tag, "--repo", gh_repo,
         "--json", "tagName,name,isDraft,url"], timeout=timeout)
    if rc != 0:
        msg = (e or out or "").strip()
        if "release not found" in msg.lower() or "not found" in msg.lower():
            return (True, False, None, "release not found")
        return (False, False, None, msg or "gh release view failed")
    try:
        d = json.loads(out or "{}")
    except json.JSONDecodeError:
        return (False, False, None, f"unparseable gh output: {out!r}")
    info = {"tag": d.get("tagName"), "name": d.get("name"), "url": d.get("url"),
            "draft": bool(d.get("isDraft"))}
    return (True, not info["draft"], info, "draft release" if info["draft"] else "")


# ------------------------------------------------------------------- Azure DevOps (az)
def az_find_open_pr(org, project, repo, head, base, timeout=60):
    """(ok, pr|None, detail) — an ACTIVE ADO PR head->base, or None. `pr` is
    {number, url, title}. Branch names may be bare (e.g. 'release-integration/x') —
    az accepts them and normalizes to refs/heads/."""
    rc, out, e = _run(
        ["az", "repos", "pr", "list", "--org", org, "--project", project,
         "--repository", repo, "--source-branch", head, "--target-branch", base,
         "--status", "active", "--output", "json"], timeout=timeout)
    if rc != 0:
        return (False, None, e.strip() or "az repos pr list failed")
    try:
        arr = json.loads(out or "[]")
    except json.JSONDecodeError:
        return (False, None, f"unparseable az output: {out!r}")
    if not arr:
        return (True, None, "")
    p = arr[0]
    num = p.get("pullRequestId")
    url = (f"{org.rstrip('/')}/{project}/_git/{repo}/pullrequest/{num}" if num else None)
    return (True, {"number": num, "url": url, "title": p.get("title")}, "")


def az_create_pr(org, project, repo, head, base, title, body, work_items=None, timeout=120):
    """(ok, url, detail) — create an ADO PR. WRITE — only call when not dry-run."""
    args = ["az", "repos", "pr", "create", "--org", org, "--project", project,
            "--repository", repo, "--source-branch", head, "--target-branch", base,
            "--title", title, "--description", body, "--output", "json"]
    if work_items:
        args += ["--work-items", str(work_items)]
    rc, out, e = _run(args, timeout=timeout)
    if rc != 0:
        return (False, "", e.strip() or "az repos pr create failed")
    try:
        d = json.loads(out or "{}")
        num = d.get("pullRequestId")
        return (True, f"{org.rstrip('/')}/{project}/_git/{repo}/pullrequest/{num}", "")
    except json.JSONDecodeError:
        return (True, "", "created (unparseable response)")


# ---------------------------------------------------------------- PBI (Azure Boards)
def create_pbi(org, project, title, area=None, iteration=None, timeout=90):
    """(ok, id, url, detail) — create one Product Backlog Item to link every PR to. WRITE."""
    args = ["az", "boards", "work-item", "create", "--org", org, "--project", project,
            "--type", "Product Backlog Item", "--title", title, "--output", "json"]
    if area:
        args += ["--area", area]
    if iteration:
        args += ["--iteration", iteration]
    rc, out, e = _run(args, timeout=timeout)
    if rc != 0:
        return (False, None, None, e.strip() or "az boards work-item create failed")
    try:
        d = json.loads(out or "{}")
        wid = d.get("id")
        url = ((d.get("_links") or {}).get("html") or {}).get("href")
        return (True, wid, url, "")
    except json.JSONDecodeError:
        return (False, None, None, f"unparseable az output: {out!r}")


# ------------------------------------------------------- RI editing (the careful part)
def prepare_ri_branch(dir_name, ri, target, dry_run=True, timeout=240):
    """Bring the release-integration branch up to date with `target` and revert build.gradle
    so the target stays DYNAMIC, then push. Returns (ok, result, detail).

    result = {behind, gradle_reverted:[...], human_conflicts:[...], pushed:bool, action:str}

    SAFETY: all work happens in a throwaway `git worktree` — the user's checkout is never
    touched. If ANY non-build.gradle conflict would remain, we do NOT write anything and
    return it for a human. We NEVER force-push (the push must fast-forward the RI branch)."""
    import tempfile, shutil, os

    okf, fdetail = git_fetch(dir_name)
    if not okf:
        return (False, {}, f"fetch failed: {fdetail}")

    okb, behind, _ = behind_count(dir_name, ri, target)
    okg, gradle, _ = gradle_diff_files(dir_name, ri, target)
    okm, conflicts, mdetail = merge_conflict_preview(dir_name, ri, target)
    if not okm:
        return (False, {}, f"could not preview the merge: {mdetail}")
    gradle_set = set(gradle or [])
    human = [c for c in conflicts if c not in gradle_set]
    result = {"behind": behind if okb else None,
              "gradle_reverted": sorted(gradle_set), "human_conflicts": human,
              "pushed": False, "action": ""}

    if human:
        result["action"] = "HELD — non-build.gradle conflicts need a human before this PR can open"
        return (True, result, "")   # not a failure: a legitimate human hold
    if dry_run:
        result["action"] = ("DRY-RUN — would merge target, revert "
                             f"{len(gradle_set)} build.gradle file(s), and push")
        return (True, result, "")

    # ---- LIVE: do the edit in an isolated worktree ----
    root = str(repo_dir(dir_name))
    tmp = tempfile.mkdtemp(prefix="scout-ri-")
    wtbranch = "scout/ri-edit"
    try:
        rc, _o, e = _run(_GITC + ["worktree", "add", "--force", "-B", wtbranch, tmp,
                                  f"origin/{ri}"], cwd=root, timeout=timeout)
        if rc != 0:
            return (False, result, f"worktree add failed: {e.strip()}")

        # merge target; build.gradle conflicts are expected and auto-resolved to target.
        rc, _o, me = _run(_GITC + ["merge", "--no-ff", "--no-edit", f"origin/{target}"],
                          cwd=tmp, timeout=timeout)
        if rc != 0:
            # only build.gradle should conflict (pre-checked); take target's side for those.
            for f in gradle_set:
                _run(_GITC + ["checkout", "--theirs", "--", f], cwd=tmp, timeout=60)
                _run(_GITC + ["add", "--", f], cwd=tmp, timeout=60)
            rc2, _o2, e2 = _run(_GITC + ["commit", "--no-edit"], cwd=tmp, timeout=60)
            if rc2 != 0:
                _run(_GITC + ["merge", "--abort"], cwd=tmp, timeout=60)
                return (False, result, f"merge could not be auto-resolved: {e2.strip() or me.strip()}")

        # force ALL build.gradle to the target's version (revert even non-conflicting diffs).
        for f in gradle_set:
            _run(_GITC + ["checkout", f"origin/{target}", "--", f], cwd=tmp, timeout=60)
        _run(_GITC + ["add", "-A"], cwd=tmp, timeout=60)
        # commit the reverts if anything is staged (no-op commit is skipped by --allow-empty guard)
        rcs, so, _ = _run(_GITC + ["status", "--porcelain"], cwd=tmp, timeout=30)
        if so.strip():
            rcc, _o, ec = _run(
                _GITC + ["commit", "-m",
                         f"Scout: sync with {target}; revert build.gradle (keep {target} dynamic)"],
                cwd=tmp, timeout=60)
            if rcc != 0:
                return (False, result, f"commit failed: {ec.strip()}")

        # push RI (fast-forward only — never force).
        rcp, _o, ep = _run(_GITC + ["push", "origin", f"HEAD:refs/heads/{ri}"],
                           cwd=tmp, timeout=timeout)
        if rcp != 0:
            return (False, result, f"push failed (not force-pushing): {ep.strip()}")
        result["pushed"] = True
        result["action"] = (f"merged {target}, reverted {len(gradle_set)} build.gradle file(s), "
                            f"pushed {ri}")
        return (True, result, "")
    finally:
        _run(_GITC + ["worktree", "remove", "--force", tmp], cwd=root, timeout=60)
        try:
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        _run(_GITC + ["branch", "-D", wtbranch], cwd=root, timeout=30)

