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
import re
from pathlib import Path
from urllib.parse import quote

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
    from tools import git_review as G
    try:
        root = G.clean_repository(repo_dir(dir_name))
        tips = [G.object_id(G._git(root, "rev-parse", f"refs/remotes/origin/{G.branch(name)}")
                            .decode().strip()) for name in (head, base)]
        with G.scratch(root) as (work, env):
            result = subprocess.run(
                _GITC + ["-c", "core.longpaths=true", "merge-tree", "--write-tree", "--name-only", *tips],
                cwd=work, env=env, capture_output=True, timeout=timeout)
            rc, out, e = (result.returncode, result.stdout.decode("utf-8"),
                          result.stderr.decode("utf-8", "replace"))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        return False, None, str(exc)
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
         "--state", "open", "--json", "number,url,title,body,labels"], timeout=timeout)
    if rc != 0:
        return (False, None, e.strip() or "gh pr list failed")
    try:
        arr = json.loads(out or "[]")
    except json.JSONDecodeError:
        return (False, None, f"unparseable gh output: {out!r}")
    if not isinstance(arr, list) or len(arr) > 1:
        return (False, None, "Ambiguous open PR lookup")
    if arr:
        arr[0]["labels"] = sorted(x["name"] for x in arr[0].get("labels", []))
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


_CHANGE_LEVEL_RE = re.compile(r"^\s*\[(PATCH|MINOR|MAJOR)\]\s*(.*?)\s*$", re.IGNORECASE)
_PR_SUFFIX_RE = re.compile(r"\s*\(#(\d+)\)\s*$")


def _gh_repo_parts(gh_repo: str):
    """(hostname|None, 'owner/repo') from a gh_repo that is either 'owner/repo' (github.com)
    or 'host/owner/repo' (GitHub Enterprise, e.g. 'msft.ghe.com/security/ad-accounts-for-android')."""
    parts = [p for p in str(gh_repo or "").split("/") if p]
    if len(parts) >= 3 and "." in parts[0]:
        return (parts[0], "/".join(parts[1:3]))
    return (None, "/".join(parts[:2]))


def _current_version_block(text: str, version: str) -> str:
    """The lines under this release's 'Version <version>' header in changes.txt, up to the next
    'Version <…>' header or 'vNext'. '' when the section isn't found."""
    out, capture = [], False
    ver_re = re.compile(rf"^Version\s+{re.escape(str(version))}\b")
    next_re = re.compile(r"^Version\s+\S")
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not capture and ver_re.match(s):
            capture = True
            continue
        if capture and (next_re.match(s) or s == "vNext"):
            break
        if capture:
            out.append(ln)
    return "\n".join(out)


def broker_change_list(gh_repo: str, version: str, ref_candidates=None, timeout=60):
    """(ok, changes, detail) — the broker release change list, read from the AUTHORITATIVE
    `changes.txt` on the broker's release branch for `version`.

    The release branch is `working/release/<version>` (integ_prs.WORKING_PREFIX). `release/<version>`
    carries the same changes.txt and is used as an equivalent fallback (override both with
    `ref_candidates`). changes.txt has one section per version ('Version <X.Y.Z>' + dashes, then
    '- [LEVEL] text (#PR)' bullets); we return THIS release's section only. Each change is
    {level, text, pr}: `level` is PATCH|MINOR|MAJOR from the '[LEVEL]' prefix (None when a bullet
    has none — never fabricated), `pr` is the trailing '(#N)'. Read-only (`gh api contents`)."""
    if not (gh_repo and version):
        return (False, [], "missing broker gh_repo/version")
    host, ownerrepo = _gh_repo_parts(gh_repo)
    refs = ref_candidates or [f"working/release/{version}", f"release/{version}"]
    text, last_err = None, ""
    for ref in refs:
        args = ["gh", "api", f"repos/{ownerrepo}/contents/changes.txt?ref={ref}", "--jq", ".content"]
        if host:
            args += ["--hostname", host]
        rc, out, e = _run(args, timeout=timeout)
        if rc == 0 and (out or "").strip():
            import base64
            try:
                text = base64.b64decode(out).decode("utf-8", "replace")
                break
            except (ValueError, TypeError):
                last_err = "could not decode changes.txt content"
                continue
        last_err = (e or "changes.txt not found").strip()
    if text is None:
        return (False, [], f"changes.txt not found on any of {refs} ({last_err})")

    block = _current_version_block(text, version)
    if not block.strip():
        return (True, [], f"no 'Version {version}' section in changes.txt")
    # Split into bullets on lines beginning with '- '. A bullet may wrap across lines, so join
    # each entry's continuation lines back into one logical entry (collapse whitespace).
    entries, cur = [], None
    for ln in block.splitlines():
        if re.match(r"^\s*-\s+", ln):
            if cur is not None:
                entries.append(cur)
            cur = re.sub(r"^\s*-\s+", "", ln)
        elif cur is not None and ln.strip() and not re.match(r"^-{3,}$", ln.strip()):
            cur += " " + ln.strip()
    if cur is not None:
        entries.append(cur)

    changes = []
    for raw in entries:
        text_e = " ".join(raw.split())                     # collapse wrapped whitespace
        if not text_e:
            continue
        m = _PR_SUFFIX_RE.search(text_e)
        pr = int(m.group(1)) if m else None
        text_e = _PR_SUFFIX_RE.sub("", text_e).strip()
        lm = _CHANGE_LEVEL_RE.match(text_e)
        level = lm.group(1).upper() if lm else None
        if lm:
            text_e = lm.group(2).strip()
        changes.append({"level": level, "text": text_e, "pr": pr})
    return (True, changes, "")


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
    if not isinstance(arr, list) or len(arr) > 1:
        return (False, None, "Ambiguous active PR lookup")
    p = arr[0]
    num = p.get("pullRequestId")
    url = (f"{org.rstrip('/')}/{project}/_git/{repo}/pullrequest/{num}" if num else None)
    return (True, {"number": num, "url": url, "title": p.get("title"),
                   "body": p.get("description", "")}, "")


def provider_repository_urls(repository, timeout=60):
    """Resolve the reviewed hosting repository itself, not merely matching commit IDs."""
    if repository["tool"] == "gh":
        host, slug = _gh_repo_parts(repository["gh_repo"])
        args = ["gh", "api", f"repos/{slug}"]
        if host:
            args += ["--hostname", host]
        names = ("clone_url", "ssh_url")
    else:
        target = repository["ado"]
        args = ["az", "repos", "show", "--org", target["org"], "--project", target["project"],
                "--repository", target["repository"], "--output", "json"]
        names = ("remoteUrl", "sshUrl")
    rc, out, error = _run(args, timeout=timeout)
    if rc:
        raise ValueError(error.strip() or "Hosting repository identity lookup failed")
    try:
        value = json.loads(out)
    except (ValueError, TypeError) as exc:
        raise ValueError("Hosting repository identity was unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("Hosting repository identity was not an object")
    urls = [value[key] for key in names if isinstance(value.get(key), str) and value[key].strip()]
    if not urls:
        raise ValueError("Hosting repository did not provide a clone URI")
    return urls


def provider_branch_object_id(repository, name, timeout=60):
    """Read a tip from the exact reviewed hosting target, not local tracking refs."""
    from tools.git_review import object_id, branch
    branch(name)
    if repository["tool"] == "gh":
        host, slug = _gh_repo_parts(repository["gh_repo"])
        args = ["gh", "api", f"repos/{slug}/git/ref/heads/{quote(name, safe='')}",
                "--jq", ".object.sha"]
        if host:
            args += ["--hostname", host]
        rc, out, err = _run(args, timeout=timeout)
        if rc:
            raise ValueError(err.strip() or "Provider branch lookup failed")
        return object_id(out.strip())
    a = repository["ado"]
    rc, out, err = _run(
        ["az", "repos", "ref", "list", "--org", a["org"], "--project", a["project"],
         "--repository", a["repository"], "--filter", "heads/" + name, "--output", "json"],
        timeout=timeout)
    if rc:
        raise ValueError(err.strip() or "Provider branch lookup failed")
    found = [r["objectId"] for r in json.loads(out)
             if r.get("name") == "refs/heads/" + name]
    if len(found) != 1:
        raise ValueError("Missing or ambiguous provider branch: " + name)
    return object_id(found[0])


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
    """Compatibility read-only preview. Writes require the checked command's captured plan."""
    from tools import git_review as G
    if not dry_run:
        return False, {}, "Use create-integration-prs --execute --review-hash --approved-by"
    try:
        root = G.clean_repository(repo_dir(dir_name))
        remote = G.remote_url(root)
        plan = G.plan_ri(root, G.remote_tip(root, remote, ri),
                         G.remote_tip(root, remote, target), target)
        return True, {"behind": plan["behind"], "gradle_reverted": plan["gradle_reverted"],
                      "human_conflicts": [], "pushed": False, "action": "read-only exact preview",
                      "plan": plan}, ""
    except ValueError as exc:
        return False, {}, str(exc)
