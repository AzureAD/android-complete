"""Step: `oneauth_access` — verify write access to the OneAuth repo (Phase 0, after `cg`).

A later phase pushes a PR to the OneAuth repo (https://office.visualstudio.com/OneAuth/_git/
OneAuth), whose branch policy requires branches named 'user/<alias>/<branch-name>'. The only
reliable proof that we can do that is to actually CREATE such a branch — so this step creates a
throwaway 'user/<alias>/scout-oneauth-access-check' branch off master and immediately deletes it.

  * create succeeds  -> we have write access -> DONE (the probe branch is cleaned up);
  * create rejected / 403 -> no access -> BLOCKED, pointing the owner at the myaccess package to
    request OneAuth R/W. The owner requests access, then RERUNS this step once it's granted.

Deterministic (same access => same verdict) and self-cleaning, so it's an `agent` step the
engine runs in-process.

Mock knobs (mocks.local.yaml / tests):
  alias  : override the alias used for the probe branch (skip the az lookup).
  access : inject 'granted' | 'denied' to skip the live branch probe.
  fail   : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import checks

ID = "oneauth_access"
KIND = "agent"

CONFIG = {
    "repo_url": checks.ONEAUTH_REPO_URL,
    "access_package": checks.ONEAUTH_ACCESS_PACKAGE,
    "branch_policy": "user/<alias>/branch-name",
}

MOCKABLE = {
    "alias": {"kind": "input", "desc": "Override the alias used for the probe branch (skip az)."},
    "access": {"kind": "input", "desc": "Inject 'granted' | 'denied' (skip the live branch probe)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _links():
    return [{"name": "OneAuth repo", "url": CONFIG["repo_url"]},
            {"name": "Request OneAuth R/W (access package)", "url": CONFIG["access_package"]}]


def _alias():
    a = mock_input("alias", MISSING)
    if a is not MISSING:
        return a
    user = checks.current_az_user()
    return ((user or "").split("@")[0]) or None


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"oneauth_access: {fail}", links=_links())

    alias = _alias()
    if not alias:
        return Blocked("oneauth_access: couldn't resolve your alias (az account) to build the "
                       "'user/<alias>/…' probe branch — run `az login`.", links=_links())

    access = mock_input("access", MISSING)
    if access is not MISSING:
        granted = str(access).lower() == "granted"
        detail = f"injected access={access}"
    else:
        granted, detail = checks.oneauth_write_access(alias)

    if granted:
        return Done(
            f"OneAuth write access confirmed for '{alias}' — created and deleted a "
            f"'user/{alias}/…' probe branch, so the release PR branch can be created later.",
            links=_links())
    return Blocked(
        f"oneauth_access: no write access to the OneAuth repo for '{alias}' ({detail}). "
        f"Request R/W via the access package (link below), then RERUN this step once access is "
        f"granted — the release can't push its OneAuth PR without it.",
        links=_links())


run = legacy_run(build)
