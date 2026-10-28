"""Explicit, pinned inputs for the two reviewed integration conflict choices."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import git_review as G


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate resolution JSON key")
        result[key] = value
    return result


def load(path, selected, versions):
    source = Path(path).expanduser()
    if not source.is_absolute() or source.is_symlink():
        raise ValueError("Resolutions file must be an absolute regular file")
    source = source.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 65536:
        raise ValueError("Invalid resolutions file")
    raw = source.read_bytes()
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    if (not isinstance(document, dict) or set(document) != {"version", "repos"}
            or type(document["version"]) is not int or document["version"] != 1
            or not isinstance(document["repos"], dict) or not document["repos"]
            or set(document["repos"]) - set(selected)):
        raise ValueError("Invalid version-1 resolution document or excluded repository")
    specs = {}
    for repo, value in document["repos"].items():
        if not isinstance(value, dict) or "repo" in value:
            raise ValueError("Invalid repository resolution")
        spec = G.ri_resolution_spec({**value, "repo": repo})
        if repo == "authenticator":
            expected = {"broker_adAccountsVersion": versions.get("broker"),
                        "broker_commonVersion": versions.get("common"),
                        "broker_msalVersion": versions.get("msal")}
            if spec["paths"]["PhoneFactor/gradle.properties"]["values"] != expected:
                raise ValueError("Resolution versions must match the exact release versions")
        specs[repo] = spec
    return {"file": str(source), "sha256": hashlib.sha256(raw).hexdigest(), "repos": specs}


def recheck(reviewed, selected, versions):
    if load(reviewed["file"], selected, versions) != reviewed:
        raise ValueError("Resolutions file changed after review")
