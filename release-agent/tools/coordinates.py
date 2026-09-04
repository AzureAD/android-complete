"""Loader for config/coordinates.yaml — the single source of truth for the release
toolchain's deployment identity (ADO org/project coordinates, pipeline definition ids,
repo identifiers, test-plan ids, Teams channel ids, quality-gate thresholds).

Loaded ONCE (cached) at first import. Consumers read named constants whose VALUES come
from here, e.g. `tools.pipelines.CHECKER_DEF = coords.pipeline_def("checker")`, so the
constant names (and every downstream import) stay unchanged while the values live in YAML.

All accessors FAIL FAST: a missing/misspelled key raises a KeyError naming the exact path,
so a typo surfaces at import time instead of silently yielding None deep in a call.
"""
from __future__ import annotations

import os
import yaml

_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "coordinates.yaml")


class _Coords:
    """Typed, fail-fast accessors over the parsed coordinates.yaml document."""

    def __init__(self, path=_CONFIG_PATH):
        self._path = path
        with open(path, encoding="utf-8") as fh:
            self._d = yaml.safe_load(fh) or {}

    # -- internal: walk a key path, raising a clear error on any missing segment --
    def _get(self, *path):
        cur = self._d
        walked = []
        for key in path:
            walked.append(key)
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(
                    f"coordinates.yaml: missing '{'.'.join(map(str, walked))}' "
                    f"(in {self._path})")
            cur = cur[key]
        return cur

    # -- ADO orgs / projects --
    def org_url(self, key) -> str:
        """The collection URL for a named org (e.g. 'engineering' -> identitydivision url)."""
        return self._get("orgs", key, "url")

    def project(self, key) -> str:
        """The project name inside a named org (e.g. 'engineering' -> 'Engineering')."""
        return self._get("orgs", key, "project")

    def resource_id(self) -> str:
        """The Azure DevOps resource GUID used to mint REST bearer tokens."""
        return self._get("ado_resource_id")

    def host(self, key) -> str:
        """A non-ADO host URL (e.g. 'cg_governance')."""
        return self._get("hosts", key)

    # -- pipelines --
    def pipeline_def(self, key) -> int:
        """A pipeline's definition id (e.g. 'checker' -> 3038)."""
        return self._get("pipelines", key, "def")

    def pipeline(self, key) -> dict:
        """A pipeline resolved to {org, project, def} (org/project are the URL + name)."""
        p = self._get("pipelines", key)
        org = p["org"]
        return {"org": self.org_url(org), "project": self.project(org), "def": p["def"]}

    # -- repos --
    def repo(self, key) -> dict:
        """A repo with its org resolved: {org, project, name, ...extra ids}."""
        r = dict(self._get("repos", key))
        org = r.pop("org")
        r["org"] = self.org_url(org)
        r["project"] = self.project(org)
        return r

    # -- test plans --
    def testplan(self, key) -> dict:
        """The raw test-plan block (plan/root_suite/configs/area/…)."""
        return dict(self._get("testplans", key))

    # -- teams --
    def team(self, key) -> dict:
        """A Teams target block ({team,channel,name} or {chat,name})."""
        return dict(self._get("teams", key))

    # -- quality gates --
    def gate(self, key):
        """A quality-gate value (threshold float or the auth suite-name list)."""
        return self._get("quality_gates", key)


# The process-wide singleton (loaded once at first import).
coords = _Coords()
