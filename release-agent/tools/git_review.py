"""Isolated Git tree planning: never checks out, fetches, or edits a user's refs."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import uuid
from urllib.parse import unquote, urlsplit


def object_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError("Missing or invalid Git object ID")
    return value


def branch(value):
    if not isinstance(value, str) or not value or value.startswith("-"):
        raise ValueError("Invalid Git branch")
    result = subprocess.run(["git", "check-ref-format", "refs/heads/" + value],
                            capture_output=True, env=_environment())
    if result.returncode:
        raise ValueError("Invalid Git branch: " + value)
    return value


def _environment(*, isolated=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    if isolated:
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def _git(root, *args, env=None, data=None):
    result = subprocess.run(
        ["git", "-c", "core.commitGraph=false", "-c", "core.longpaths=true", "-c", "core.hooksPath=",
         "-c", "gc.auto=0", *args],
        cwd=str(root), env=env or _environment(), input=data, capture_output=True,
        timeout=240)
    if result.returncode:
        raise ValueError(f"Git {args[0]} failed: " +
                         result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def clean_repository(root, *, scratch_dir=None):
    root = Path(root).resolve()
    top = _git(root, "rev-parse", "--show-toplevel").decode().strip()
    if Path(top).resolve() != root:
        raise ValueError("Repository directory must be its own checkout root")
    status_args = ["status", "--porcelain", "--untracked-files=all"]
    if scratch_dir is not None:
        scratch_dir = Path(scratch_dir).resolve()
        if (scratch_dir.parent != root or not re.fullmatch(r"\.write-review-[0-9a-f]{32}", scratch_dir.name)
                or _git(root, "ls-files", "-z", "--", scratch_dir.name)):
            raise ValueError("Invalid isolated Git scratch directory")
        status_args += ["--", ".", ":(exclude,literal)" + scratch_dir.name]
    if _git(root, *status_args):
        raise ValueError(f"Dirty/conflicted checkout is held: {root}")
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
                   "rebase-apply", "BISECT_LOG"):
        path = _git(root, "rev-parse", "--git-path", marker).decode().strip()
        if (root / path).exists():
            raise ValueError(f"In-progress Git operation is held: {marker}")
    return root


def remote_identity(remote):
    """Normalize ADO's public collection aliases, never credentials or arbitrary userinfo."""
    if not isinstance(remote, str) or not remote or remote.startswith("-") or any(ord(c) < 32 for c in remote):
        raise ValueError("Invalid repository remote URI")
    parts = urlsplit(remote)
    if parts.scheme in ("http", "https"):
        if (parts.scheme != "https" or not parts.hostname or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("Unsupported repository remote URI")
        host = parts.hostname.casefold()
        path = unquote(parts.path).strip("/")
        if any(ord(c) < 32 for c in path):
            raise ValueError("Invalid repository remote path")
        if host == "dev.azure.com":
            org, separator, path = path.partition("/")
            if not separator or not org:
                raise ValueError("Invalid Azure DevOps repository remote")
        elif host.endswith(".visualstudio.com"):
            org = host[:-len(".visualstudio.com")]
            if path.lower().startswith("defaultcollection/"):
                path = path[len("defaultcollection/"):]
        else:
            if parts.username is not None:
                raise ValueError("Repository credentials must not enter a review")
            return ("https", host, parts.port or 443, path)
        if not org or (parts.username is not None and unquote(parts.username).casefold() != org.casefold()):
            raise ValueError("Repository credentials must not enter a review")
        return ("ado", org.casefold(), parts.port or 443, path.casefold())
    return ("literal", remote)


def remote_url(root):
    env = _environment(isolated=False)
    url = _git(root, "remote", "get-url", "--push", "origin", env=env).decode().strip()
    fetch_url = _git(root, "remote", "get-url", "origin", env=env).decode().strip()
    all_urls = _git(root, "remote", "get-url", "--push", "--all", "origin", env=env).decode().splitlines()
    if not url or url != fetch_url or all_urls != [url]:
        raise ValueError("Origin must have one matching fetch/push URL")
    remote_identity(url)
    return url


def remote_tip(root, remote, name):
    ref = "refs/heads/" + branch(name)
    out = _git(root, "ls-remote", "--exit-code", "--refs", remote, ref,
               env=_environment(isolated=False)).decode().splitlines()
    if len(out) != 1 or out[0].split()[1] != ref:
        raise ValueError(f"Missing or ambiguous remote branch: {name}")
    return object_id(out[0].split()[0])


@contextmanager
def scratch(root):
    """New object database/index beneath the repo; existing objects are read-only alternates."""
    root = Path(root).resolve()
    path = root / (".write-review-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        fmt = _git(root, "rev-parse", "--show-object-format").decode().strip()
        _git(path, "init", "--quiet", "--bare", "--template=", "--object-format=" + fmt)
        objects = _git(root, "rev-parse", "--git-path", "objects").decode().strip()
        env = _environment()
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str((root / objects).resolve())
        env["GIT_INDEX_FILE"] = str(path / "review-index")
        yield path, env
    finally:
        def remove_readonly(function, filename, error):
            if not isinstance(error[1], PermissionError):
                raise error[1]
            os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(path, onerror=remove_readonly)


def _path(value):
    p = PurePosixPath(value)
    if (p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/"))
            or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or ".git" in (x.lower() for x in p.parts)):
        raise ValueError("Unsupported Git path: " + repr(value))
    return value


def _tree(root, tree, env):
    out = {}
    for record in _git(root, "ls-tree", "-rz", tree, env=env).split(b"\0"):
        if not record:
            continue
        info, path = record.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        out[_path(path.decode("utf-8"))] = (mode, kind, object_id(oid))
    return out


def _file(root, entry, env):
    if entry is None:
        return None
    mode, kind, oid = entry
    if kind != "blob" or mode not in ("100644", "100755"):
        raise ValueError("Symlink/submodule/non-regular changed files require human review")
    data = _git(root, "cat-file", "blob", oid, env=env)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    return {"mode": mode, "object_id": oid, "base64": base64.b64encode(data).decode(),
            "text": text}


def _remove_from_index(root, path, env, oid_length):
    _git(root, "update-index", "-z", "--index-info", env=env,
         data=("0 " + "0" * oid_length + "\t" + path + "\0").encode("utf-8"))


def plan_ri(root, head, base, target):
    """Exact merged tree, Gradle reverts, file bytes and canonical commit metadata."""
    root = clean_repository(root)
    object_id(head)
    object_id(base)
    with scratch(root) as (work, env):
        old = _tree(work, head, env)
        upstream = _tree(work, base, env)
        behind = int(_git(work, "rev-list", "--count", f"{head}..{base}", env=env))
        parents = [head]
        conflicts = []
        if behind:
            result = subprocess.run(
                ["git", "-c", "core.commitGraph=false", "-c", "core.longpaths=true",
                 "merge-tree", "--write-tree",
                 "--name-only", head, base],
                cwd=work, env=env, capture_output=True, timeout=240)
            lines = result.stdout.decode("utf-8").splitlines()
            if result.returncode not in (0, 1) or not lines:
                raise ValueError("Cannot compute complete merge tree; fetch objects or resolve manually: "
                                 + result.stderr.decode("utf-8", "replace").strip())
            tree = object_id(lines[0])
            if result.returncode:
                for line in lines[1:]:
                    if not line:
                        break
                    conflicts.append(_path(line))
                if not conflicts or any(
                        PurePosixPath(p).name not in ("build.gradle", "build.gradle.kts")
                        for p in conflicts):
                    raise ValueError("Merge conflicts require human resolution: " + ", ".join(conflicts))
            parents.append(base)
        else:
            tree = _git(work, "rev-parse", head + "^{tree}", env=env).decode().strip()
        merged = _tree(work, tree, env)
        reverts = sorted(p for p in set(merged) | set(upstream)
                         if PurePosixPath(p).name in ("build.gradle", "build.gradle.kts")
                         and merged.get(p) != upstream.get(p))
        _git(work, "read-tree", tree, env=env)
        for path in reverts:
            entry = upstream.get(path)
            _file(work, merged.get(path), env)
            _file(work, entry, env)
            if entry is None:
                _remove_from_index(work, path, env, len(head))
            else:
                mode, _, oid = entry
                _git(work, "update-index", "--add", "--cacheinfo", mode, oid, path, env=env)
        tree = object_id(_git(work, "write-tree", env=env).decode().strip())
        final = _tree(work, tree, env)
        edits = {p: {"before": _file(work, old.get(p), env),
                     "after": _file(work, final.get(p), env)}
                 for p in sorted(set(old) | set(final)) if old.get(p) != final.get(p)}
        content = {"tree": tree, "parents": parents, "behind": behind,
                   "merge": "merge-tree" if behind else "none",
                   "resolved_gradle_conflicts": conflicts, "gradle_reverted": reverts,
                   "edits": edits, "commit": None, "commit_id": head}
        if edits or behind:
            # Dates are derived from the reviewed parents, not the execution wall clock.
            timestamp = max(int(_git(work, "show", "-s", "--format=%ct", p, env=env))
                            for p in parents) + 1
            name = _git(root, "config", "user.name", env=_environment(isolated=False)).decode().strip()
            email = _git(root, "config", "user.email", env=_environment(isolated=False)).decode().strip()
            if not name or not email or any(c in name + email for c in "\r\n<>"):
                raise ValueError("Configure a valid local Git author before review")
            message = f"Scout: sync with {target}; revert build.gradle (keep {target} dynamic)"
            ident = f"{name} <{email}> {timestamp} +0000"
            raw = (f"tree {tree}\n" + "".join(f"parent {p}\n" for p in parents)
                   + f"author {ident}\ncommitter {ident}\n\n{message}\n")
            encoded = raw.encode("utf-8")
            fmt = "sha1" if len(head) == 40 else "sha256"
            oid = hashlib.new(fmt, b"commit " + str(len(encoded)).encode() + b"\0" + encoded).hexdigest()
            content.update(commit=raw, commit_id=oid)
        return content


def _merge_tree(root, head, base, env):
    result = subprocess.run(
        ["git", "-c", "core.commitGraph=false", "-c", "core.longpaths=true", "-c", "core.hooksPath=",
         "merge-tree", "--write-tree", "--name-only", "-z", head, base],
        cwd=root, env=env, capture_output=True, timeout=240)
    records = result.stdout.decode("utf-8").split("\0")
    if result.returncode == 1:
        conflicts = []
        for path in records[1:]:
            if not path:
                break
            conflicts.append(_path(path))
        raise ValueError("Merge conflicts require separate human resolution and a fresh review: "
                         + (", ".join(conflicts) or "unresolved tree"))
    if result.returncode:
        raise ValueError("Incomplete/unsupported merge history; fetch or resolve separately and "
                         "review again: " + result.stderr.decode("utf-8", "replace").strip())
    return object_id(records[0])


def plan_merge_edits(root, head, base, paths, edit_files, message):
    """Merge pinned commits, then edit text files, entirely in an isolated object database."""
    root = clean_repository(root)
    object_id(head)
    object_id(base)
    if _git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise ValueError("Shallow history cannot prove a complete merge; fetch separately and review again")
    if not isinstance(message, str) or not message or "\0" in message:
        raise ValueError("Invalid reviewed commit message")
    paths = {key: _path(path) for key, path in paths.items()}
    if len(set(paths.values())) != len(paths):
        raise ValueError("Overlapping reviewed file paths")
    with scratch(root) as (work, env):
        try:
            for oid in (head, base):
                _git(work, "cat-file", "-e", oid + "^{commit}", env=env)
            counts = {
                "ahead": int(_git(work, "rev-list", "--count", f"{base}..{head}", env=env)),
                "behind": int(_git(work, "rev-list", "--count", f"{head}..{base}", env=env)),
            }
            old = _tree(work, head, env)
        except ValueError as exc:
            raise ValueError("Complete local Git objects are required; fetch separately and review again: "
                             + str(exc)) from exc
        parents = [head]
        if counts["behind"]:
            merged_tree = _merge_tree(work, head, base, env)
            parents.append(base)
        else:
            merged_tree = object_id(_git(work, "rev-parse", head + "^{tree}", env=env).decode().strip())
        merged = _tree(work, merged_tree, env)

        def texts(tree):
            files = {}
            for key, path in paths.items():
                entry = _file(work, tree.get(path), env)
                if entry is None or entry["text"] is None:
                    raise ValueError("Missing regular UTF-8 reviewed file: " + path)
                files[key] = entry["text"]
            return files

        before, merged_files = texts(old), texts(merged)
        changes = edit_files(dict(merged_files))
        if not isinstance(changes, dict) or set(changes) - set(paths.values()):
            raise ValueError("Editor returned unreviewed paths")
        _git(work, "read-tree", merged_tree, env=env)
        for path, text in sorted(changes.items()):
            if not isinstance(text, str):
                raise ValueError("Editor must return exact UTF-8 file content")
            mode, _, _ = merged[path]
            oid = _git(work, "hash-object", "-w", "--stdin", env=env,
                       data=text.encode("utf-8")).decode().strip()
            _git(work, "update-index", "--add", "--cacheinfo", mode, oid, path, env=env)
        tree = object_id(_git(work, "write-tree", env=env).decode().strip())
        final = _tree(work, tree, env)
        edits = {p: {"before": _file(work, old.get(p), env),
                     "after": _file(work, final.get(p), env)}
                 for p in sorted(set(old) | set(final)) if old.get(p) != final.get(p)}
        content = {"tree": tree, "merged_tree": merged_tree, "parents": parents,
                   **counts, "merge": "merge-tree" if counts["behind"] else "none",
                   "files_before": before, "merged_files": merged_files, "final_files": texts(final),
                   "version_edits": changes, "edits": edits, "comment": message,
                   "commit": None, "commit_id": head}
        if edits or counts["behind"]:
            timestamp = max(int(_git(work, "show", "-s", "--format=%ct", p, env=env))
                            for p in parents) + 1
            name = _git(root, "config", "user.name", env=_environment(isolated=False)).decode().strip()
            email = _git(root, "config", "user.email", env=_environment(isolated=False)).decode().strip()
            if not name or not email or any(ord(c) < 32 or c in "<>" for c in name + email):
                raise ValueError("Configure a valid local Git author before review")
            ident = f"{name} <{email}> {timestamp} +0000"
            raw = (f"tree {tree}\n" + "".join(f"parent {p}\n" for p in parents)
                   + f"author {ident}\ncommitter {ident}\n\n{message}\n")
            oid = object_id(_git(work, "hash-object", "-t", "commit", "-w", "--stdin",
                                 env=env, data=raw.encode("utf-8")).decode().strip())
            content.update(commit=raw, commit_id=oid)
        return content


def push_reviewed(root, remote, name, expected_tip, content, validate):
    """Rehydrate reviewed bytes only, then CAS-push a descendant of the reviewed head."""
    root = clean_repository(root)
    if remote_url(root) != remote or remote_tip(root, remote, name) != expected_tip:
        raise ValueError("Origin/head changed since review")
    if content["parents"][0] != expected_tip or not content["commit"]:
        raise ValueError("Invalid reviewed commit ancestry")
    with scratch(root) as (work, env):
        _git(work, "read-tree", expected_tip, env=env)
        # Remove replaced paths first, including file/directory transitions.
        for path in content["edits"]:
            _path(path)
            _remove_from_index(work, path, env, len(expected_tip))
        for path, edit in content["edits"].items():
            _path(path)
            after = edit["after"]
            if after is not None:
                oid = _git(work, "hash-object", "-w", "--stdin", env=env,
                           data=base64.b64decode(after["base64"], validate=True)).decode().strip()
                if oid != after["object_id"]:
                    raise ValueError("Reviewed blob mismatch")
                _git(work, "update-index", "--add", "--cacheinfo",
                     after["mode"], oid, path, env=env)
        tree = _git(work, "write-tree", env=env).decode().strip()
        if tree != content["tree"]:
            raise ValueError("Reviewed tree mismatch")
        oid = _git(work, "hash-object", "-t", "commit", "-w", "--stdin", env=env,
                   data=content["commit"].encode("utf-8")).decode().strip()
        if oid != content["commit_id"]:
            raise ValueError("Reviewed commit mismatch")
        clean_repository(root, scratch_dir=work)
        validate()
        if remote_tip(root, remote, name) != expected_tip:
            raise ValueError("Head drift immediately before push")
        ref = "refs/heads/" + branch(name)
        # The lease is a compare-and-swap, not permission to rewrite history:
        # the new commit's first parent is exactly expected_tip.
        transport = _environment(isolated=False)
        transport.update({key: env[key] for key in ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE")})
        _git(work, "push", "--porcelain", f"--force-with-lease={ref}:{expected_tip}",
             remote, f"{oid}:{ref}", env=transport)
    if remote_tip(root, remote, name) != oid:
        raise ValueError("Push result uncertain; owner must inspect the remote")
    return oid
