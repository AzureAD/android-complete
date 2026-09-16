"""OneAuth Common-ingestion helpers (Phase-4 `oneauth_common_pr`).

OneAuth (office.visualstudio.com/OneAuth) is a huge monorepo we do NOT automatically clone.
The checked writer requires an existing clean checkout with the reviewed objects locally:
it merges and bumps in an isolated Git database, reviews the exact commit, and CAS-pushes it.
REST helpers also provide:
  * read file items (raw text),
  * push a single multi-file commit (the version bump) via the Pushes API,
  * open the real `android/common-ingestion -> dev` PR (via tools.prs.az_*).

The bump touches FOUR files (all keyed off the final, published Common version + the MSAL
version, both from state.versions):
  * /sources/android/gradle/gradle/libs.versions.toml  — msIdentityCommon = "<common>"
  * /deps/cgmanifest.json                              — the com.microsoft.identity:common maven version
  * /deps/README.md                                    — the "MSAL Android Common" table row version
  * /CHANGELOG.md                                      — a new "(Android) Ingest AndroidCommon …" bullet

The `edit_*` functions are PURE (text in -> text out); they raise ValueError when their anchor
isn't found so a silent no-op bump can't happen. The REST helpers return typed tuples.
The checked command pins commit reads, explicit repository coordinates, exact file edits
and PR content. Conflicts or missing local objects are held; the legacy merge helper is not used.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from urllib.parse import quote, urlencode, urlsplit

from tools import pipelines as P
from tools.coordinates import coords

_REPO = coords.repo("oneauth")                       # {org, project, name}
ORG, PROJECT, REPO = _REPO["org"], _REPO["project"], _REPO["name"]
_BASE = f"{ORG}/{PROJECT}/_apis/git/repositories/{REPO}"
_ADO_RESOURCE = P._ADO_RESOURCE

INGEST_BRANCH = "android/common-ingestion"
TARGET_BRANCH = "dev"

FILES = {
    "toml": "/sources/android/gradle/gradle/libs.versions.toml",
    "cgmanifest": "/deps/cgmanifest.json",
    "readme": "/deps/README.md",
    "changelog": "/CHANGELOG.md",
}


def add_review_arguments(parser):
    parser.add_argument("--repo-dir", help=(
        "Existing clean OneAuth checkout with both branch tips/history locally; "
        "defaults to OneAuth beneath the configured Android repository root. Never fetched automatically."))


def review_repo_dir(repository, requested=None):
    from pathlib import Path
    from tools.prs import repo_dir
    root = Path(requested).expanduser() if requested else repo_dir(repository["repository"])
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(
            f"HELD: OneAuth needs a local checkout at {root}; provide --repo-dir with a clean checkout "
            "and fetch its complete branch objects separately before requesting a review")
    return root


def repository_remote_url(repository, timeout=60):
    """Read the provider's clone URI, so matching commit IDs alone cannot authorize another repo."""
    ok, data, detail = P._ado_rest_get(_base(repository) + "?api-version=7.1", timeout)
    if not ok or not isinstance(data, dict) or not isinstance(data.get("remoteUrl"), str):
        raise ValueError("Cannot verify OneAuth repository remote URI: " + str(detail))
    remote = data["remoteUrl"]
    parts = urlsplit(remote)
    if (parts.scheme != "https" or not parts.hostname or parts.password
            or parts.query or parts.fragment or any(ord(c) < 32 for c in remote)):
        raise ValueError("Unsupported OneAuth repository remote URI")
    from tools.git_review import remote_identity
    remote_identity(remote)
    return remote


# ============================ pure edit functions ============================
def edit_toml(content: str, common: str) -> str:
    """Set `msIdentityCommon = "<common>"` in libs.versions.toml. Does NOT touch the separate
    msIdentityCommonTest version. Raises ValueError if the anchor is missing."""
    new, n = re.subn(r'(?m)^(\s*msIdentityCommon\s*=\s*")[^"]*(")',
                     lambda m: m.group(1) + common + m.group(2), content, count=1)
    if n != 1:
        raise ValueError("libs.versions.toml: `msIdentityCommon = \"…\"` anchor not found")
    return new


def edit_cgmanifest(content: str, common: str) -> str:
    """Set the version of the com.microsoft.identity:common maven registration in cgmanifest.json
    (targeted raw-text edit — preserves all other formatting). Raises ValueError if not found."""
    pat = re.compile(
        r'("groupId"\s*:\s*"com\.microsoft\.identity"\s*,\s*'
        r'"artifactId"\s*:\s*"common"\s*,\s*"version"\s*:\s*")[^"]*(")')
    new, n = pat.subn(lambda m: m.group(1) + common + m.group(2), content, count=1)
    if n != 1:
        raise ValueError("cgmanifest.json: com.microsoft.identity:common maven version not found")
    return new


def edit_readme(content: str, common: str) -> str:
    """Bump the version column of the '| MSAL Android Common |' table row in deps/README.md.
    Raises ValueError if the row is missing."""
    new, n = re.subn(r'(?m)^(\|\s*MSAL Android Common\s*\|\s*)(\d[\w.\-]*)(\s*\|)',
                     lambda m: m.group(1) + common + m.group(3), content, count=1)
    if n != 1:
        raise ValueError("deps/README.md: '| MSAL Android Common |' row not found")
    return new


def changelog_line(common: str, msal: str) -> str:
    return (f"- (Android) Ingest AndroidCommon {common}. Any apps that still use MSAL.Android "
            f"*MUST* update to {msal}.")


def edit_changelog(content: str, common: str, msal: str) -> str:
    """Insert the ingest bullet as the first item under `## [Unreleased]` -> `### Other Changes`.
    Idempotent: if a bullet already ingests this exact common version there, returns content
    unchanged. Raises ValueError if the Unreleased/Other Changes section is missing."""
    line = changelog_line(common, msal)
    if line in content:
        return content
    lines = content.splitlines(keepends=True)
    # find "## [Unreleased]" then its "### Other Changes" header
    try:
        u = next(i for i, l in enumerate(lines) if l.strip() == "## [Unreleased]")
    except StopIteration:
        raise ValueError("CHANGELOG.md: '## [Unreleased]' section not found")
    oc = None
    for i in range(u + 1, len(lines)):
        if lines[i].startswith("## [") and lines[i].strip() != "## [Unreleased]":
            break                                    # left the Unreleased block
        if lines[i].strip() == "### Other Changes":
            oc = i
            break
    if oc is None:
        raise ValueError("CHANGELOG.md: '### Other Changes' under [Unreleased] not found")
    nl = "\n" if not lines[oc].endswith("\r\n") else "\r\n"
    lines.insert(oc + 1, line + nl)
    return "".join(lines)


def apply_edits(files: dict, common: str, msal: str, *, paths=None) -> dict:
    """Given {key: raw_content} for toml/cgmanifest/readme/changelog, return {path: new_content}
    for every file that CHANGED (skips no-op edits). Raises ValueError on a missing anchor."""
    out = {}
    edited = {
        "toml": edit_toml(files["toml"], common),
        "cgmanifest": edit_cgmanifest(files["cgmanifest"], common),
        "readme": edit_readme(files["readme"], common),
        "changelog": edit_changelog(files["changelog"], common, msal),
    }
    paths = FILES if paths is None else paths
    for key, new in edited.items():
        if new != files[key]:
            out[paths[key]] = new
    return out


# ============================ REST helpers ============================
def _token(timeout=60):
    az = shutil.which("az")
    if az is None:
        return None
    try:
        r = subprocess.run([az, "account", "get-access-token", "--resource", _ADO_RESOURCE,
                            "--query", "accessToken", "-o", "tsv"],
                           capture_output=True, text=True, timeout=timeout, encoding="utf-8")
        return (r.stdout or "").strip() or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _coordinates(repository=None):
    return dict(repository) if repository is not None else {
        "org": ORG, "project": PROJECT, "repository": REPO}


def _base(repository=None):
    r = _coordinates(repository)
    return (f"{r['org'].rstrip('/')}/{quote(r['project'], safe='')}/_apis/git/repositories/"
            f"{quote(r['repository'], safe='')}")


def _pr_url(repository, pid):
    r = _coordinates(repository)
    return (f"{r['org'].rstrip('/')}/{quote(r['project'], safe='')}/_git/"
            f"{quote(r['repository'], safe='')}/pullrequest/{pid}")


def read_text(path: str, ref: str, ref_type: str = "branch", timeout=60, *, repository=None):
    """(ok, text, detail) — raw file content at a branch/commit via the Git items API."""
    tok = _token(timeout)
    if not tok:
        return (False, None, "AUTH: could not mint an ADO token (run `az login`)")
    url = _base(repository) + "/items?" + urlencode({
        "path": path, "includeContent": "true",
        "versionDescriptor.versionType": ref_type, "versionDescriptor.version": ref,
        "api-version": "7.1", "$format": "json"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}",
                                               "Accept": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return (False, None, f"read {path}@{ref} failed: {e}")
    try:
        item = json.loads(raw)
        if not isinstance(item.get("content"), str) or item.get("isFolder"):
            return (False, None, f"Missing text content: {path}@{ref}")
        return (True, item["content"], "")
    except (json.JSONDecodeError, AttributeError):
        return (False, None, f"Invalid item response: {path}@{ref}")


def branch_object_id(branch: str, timeout=60, *, repository=None):
    """(ok, objectId, detail) — the tip commit of a branch."""
    ok, data, d = P._ado_rest_get(
        _base(repository) + "/refs?" + urlencode({"filter": f"heads/{branch}", "api-version": "7.1"}),
        timeout)
    if not ok:
        return (False, None, d)
    for r in (data or {}).get("value", []):
        if r.get("name") == f"refs/heads/{branch}":
            return (True, r.get("objectId"), "")
    return (False, None, f"branch '{branch}' not found")


def ahead_behind(base: str, target: str, timeout=60, *, repository=None, ref_type="branch"):
    """(ok, {ahead, behind}, detail) — target's ahead/behind counts relative to base."""
    url = _base(repository) + "/diffs/commits?" + urlencode({
        "baseVersion": base, "baseVersionType": ref_type, "targetVersion": target,
        "targetVersionType": ref_type, "$top": "0", "api-version": "7.1"})
    ok, d, det = P._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, det)
    return (True, {"ahead": d.get("aheadCount"), "behind": d.get("behindCount")}, "")


def push_edits(branch: str, old_object_id: str, path_to_content: dict, comment: str, timeout=90,
               *, repository=None):
    """(ok, commitId, detail) — push ONE commit editing multiple files onto `branch`. WRITE."""
    changes = [{"changeType": "edit", "item": {"path": path},
                "newContent": {"content": content, "contentType": "rawtext"}}
               for path, content in path_to_content.items()]
    body = {"refUpdates": [{"name": f"refs/heads/{branch}", "oldObjectId": old_object_id}],
            "commits": [{"comment": comment, "changes": changes}]}
    ok, res, d = P._ado_rest_send(f"{_base(repository)}/pushes?api-version=7.1", "POST", body, timeout)
    if not ok:
        return (False, None, d)
    commit = (((res or {}).get("commits") or [{}])[0]).get("commitId")
    return (True, commit, "")


def verify_edit_commit(repository, old_tip, new_tip, edits, comment, timeout=60):
    """Read back the returned output; never accept an unreviewed merge or extra file change."""
    from tools.git_review import object_id
    object_id(old_tip)
    object_id(new_tip)
    base = _base(repository)
    ok, data, detail = P._ado_rest_get(f"{base}/commits/{new_tip}?api-version=7.1", timeout)
    if (not ok or not isinstance(data, dict) or data.get("parents") != [old_tip]
            or str(data.get("comment", "")).rstrip("\n") != comment.rstrip("\n")):
        raise ValueError("OneAuth push ancestry/comment is uncertain: " + str(detail))
    ok, data, detail = P._ado_rest_get(
        f"{base}/commits/{new_tip}/changes?$top={len(edits) + 1}&api-version=7.1", timeout)
    changes = (data or {}).get("changes", []) if ok else []
    if (len(changes) != len(edits)
            or {c.get("item", {}).get("path") for c in changes} != set(edits)
            or any(c.get("changeType") != "edit" for c in changes)):
        raise ValueError("OneAuth push changed unreviewed files or could not be verified: " + str(detail))


# ------------------------------ pull requests / server-side merge ------------------------------
def find_open_pr(source: str, target: str, timeout=60, *, repository=None):
    """(ok, pr|None, detail) — an ACTIVE PR source->target, {id, url, title} or None."""
    url = _base(repository) + "/pullrequests?" + urlencode({
        "searchCriteria.sourceRefName": f"refs/heads/{source}",
        "searchCriteria.targetRefName": f"refs/heads/{target}",
        "searchCriteria.status": "active", "api-version": "7.1"})
    ok, data, d = P._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, d)
    entries = (data or {}).get("value", [])
    if len(entries) > 1:
        return (False, None, "Multiple active PRs for the reviewed branch pair")
    for pr in entries:
        pid = pr.get("pullRequestId")
        return (True, {"id": pid, "title": pr.get("title"),
                       "description": pr.get("description", ""),
                       "url": _pr_url(repository, pid)}, "")
    return (True, None, "")


def create_pr(source: str, target: str, title: str, description: str, timeout=90, *, repository=None):
    """(ok, pr, detail) — open a PR source->target. pr = {id, url, mergeStatus}. WRITE."""
    body = {"sourceRefName": f"refs/heads/{source}", "targetRefName": f"refs/heads/{target}",
            "title": title, "description": description}
    ok, res, d = P._ado_rest_send(f"{_base(repository)}/pullrequests?api-version=7.1", "POST", body, timeout)
    if not ok:
        return (False, None, d)
    pid = (res or {}).get("pullRequestId")
    return (True, {"id": pid, "mergeStatus": (res or {}).get("mergeStatus"),
                   "url": _pr_url(repository, pid)}, "")


def get_pr(pr_id, timeout=60):
    return P._ado_rest_get(f"{_BASE}/pullrequests/{pr_id}?api-version=7.1", timeout)


def _patch_pr(pr_id, body, timeout=90):
    return P._ado_rest_send(f"{_BASE}/pullrequests/{pr_id}?api-version=7.1", "PATCH", body, timeout)


def abandon_pr(pr_id, timeout=60):
    return _patch_pr(pr_id, {"status": "abandoned"}, timeout)


def merge_dev_into_ingestion(dry_run: bool = True, timeout=120):
    """Server-side MERGE of `dev` into `android/common-ingestion` (B1): if the ingestion branch
    is behind dev, open a transient `dev -> android/common-ingestion` PR and COMPLETE it so ADO
    performs the merge. Returns (ok, info, detail) where info is
      {behind, merged: bool, conflict: bool, pr_id, would: <dry-run note>}.
    Conflicts are surfaced (conflict=True, ok=False) — NEVER forced. No-op when already current."""
    import time
    okab, ab, d = ahead_behind(TARGET_BRANCH, INGEST_BRANCH, timeout)
    if not okab:
        return (False, None, f"could not compute ahead/behind ({d})")
    behind = ab.get("behind") or 0
    if behind == 0:
        return (True, {"behind": 0, "merged": False, "conflict": False, "pr_id": None}, "")
    if dry_run:
        return (True, {"behind": behind, "merged": False, "conflict": False, "pr_id": None,
                       "would": f"merge dev into {INGEST_BRANCH} ({behind} commits behind) via a "
                                f"transient PR"}, "")

    title = f"Merge dev into {INGEST_BRANCH} (AndroidCommon ingestion)"
    okc, pr, dc = create_pr(TARGET_BRANCH, INGEST_BRANCH, title,
                            "Automated: bring android/common-ingestion up to date with dev before "
                            "the AndroidCommon version bump.", timeout)
    if not okc:
        return (False, None, f"could not open the dev->{INGEST_BRANCH} merge PR ({dc})")
    pid = pr["id"]
    # wait for ADO to compute mergeStatus (queued -> succeeded | conflicts)
    status, src_commit = None, None
    for _ in range(12):
        okg, full, dg = get_pr(pid, timeout)
        if okg:
            status = full.get("mergeStatus")
            src_commit = (full.get("lastMergeSourceCommit") or {}).get("commitId")
            if status and status != "queued":
                break
        time.sleep(2)
    if status == "conflicts":
        abandon_pr(pid, timeout)
        return (False, {"behind": behind, "merged": False, "conflict": True, "pr_id": pid},
                f"merging dev into {INGEST_BRANCH} has CONFLICTS — a human must resolve them "
                f"(PR {pid} abandoned). Resolve, then re-run.")
    if status != "succeeded":
        return (False, {"behind": behind, "merged": False, "conflict": False, "pr_id": pid},
                f"merge PR {pid} not mergeable (status: {status}).")
    okp, _res, dp = _patch_pr(pid, {"status": "completed",
                                    "lastMergeSourceCommit": {"commitId": src_commit},
                                    "completionOptions": {"mergeStrategy": "noFastForward",
                                                          "deleteSourceBranch": False}}, timeout)
    if not okp:
        return (False, {"behind": behind, "merged": False, "conflict": False, "pr_id": pid},
                f"could not complete the merge PR {pid} ({dp}) — check branch policies on "
                f"{INGEST_BRANCH}.")
    return (True, {"behind": behind, "merged": True, "conflict": False, "pr_id": pid}, "")
