"""Step: `tag_authenticator` — tag the Auth App release commit (Phase 4, finalize; F6).

Once the release has published, permanently mark the Authenticator app's released build by
creating a git tag on its release commit. Deterministic + idempotent, so it's an `agent` step
the engine runs in-process — there is NO preview/execute: it creates the tag when the step is
reached.

WHAT it tags:
  * repo   — the Authenticator app repo (config/coordinates.yaml repos.authenticator; msazure/One,
             AD-MFA-phonefactor-phoneApp-android).
  * commit — the EXACT commit the release-app build (AndroidBuild-1ES) was built from
             (build.sourceVersion) on the release branch — not merely the branch head.
  * name   — the Auth App version, read from that build's numeric ADO build-tag (e.g.
             '6.2608.5658'). The auth app does NOT use a 'v' prefix.

The release branch is `state.versions.authenticator` ('release/YYYY/MM/DD', set at Phase 2 by
build_verify.orchestrator_health from the AuthenticatorBranch tag). The tag is LIGHTWEIGHT,
matching the repo's existing release tags.

Idempotent: if the tag already exists AT that commit -> Done; if it exists at a DIFFERENT commit
-> Blocked (a human must reconcile). No release-app build on the branch yet -> Blocked.

Mock knobs (mocks.local.yaml / tests):
  version : inject the version / tag name (skip the release-app build lookup).
  commit  : inject the commit to tag (skip the release-app build lookup).
  dry_run : compose the tag but DON'T write it (report what it would do) — safe live testing.
  fail    : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import pipelines as P
from tools.coordinates import coords

ID = "tag_authenticator"
KIND = "agent"

_REPO = coords.repo("authenticator")

MOCKABLE = {
    "version": {"kind": "input", "desc": "Inject the version / tag name (skip the build lookup)."},
    "commit": {"kind": "input", "desc": "Inject the commit to tag (skip the build lookup)."},
    "dry_run": {"kind": "input", "desc": "Compose the tag but DON'T write it (report only)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _auth_release_branch(state):
    """The Auth App RELEASE branch ('release/YYYY/MM/DD') from state.versions, or None."""
    return (getattr(state, "versions", None) or {}).get("authenticator")


def _tag_url(tag_name):
    return (f"{_REPO['org']}/{_REPO['project']}/_git/{_REPO['name']}?version=GT{tag_name}")


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"tag_authenticator: {fail}")

    branch = _auth_release_branch(state)
    if not branch:
        return Blocked("tag_authenticator: no authenticator release branch on state.versions "
                       "('release/YYYY/MM/DD') — run build_verify.orchestrator_health first.")

    # 1) resolve the version + commit (injected, or discovered from the release-app build)
    version = mock_input("version", MISSING)
    commit = mock_input("commit", MISSING)
    if version is MISSING or commit is MISSING:
        ok, info, detail = P.find_auth_release_build(branch)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"tag_authenticator: couldn't resolve the Auth App version "
                           f"({detail}){hint}.")
        if not info:
            return Blocked(f"tag_authenticator: {detail} — the release-app build hasn't run yet.")
        version = info["version"] if version is MISSING else version
        commit = info["commit"] if commit is MISSING else commit

    tag = str(version).strip()
    commit = str(commit).strip()
    links = [{"name": f"Auth tag {tag}", "url": _tag_url(tag)}]

    # 2) dry-run (personal live testing) — compose without writing
    if str(mock_input("dry_run", "")).lower() in ("1", "true", "yes"):
        return Done(f"[dry-run] Would tag {_REPO['name']} commit {commit[:8]} as '{tag}' "
                    f"(no write).", links=links)

    # 3) create the lightweight tag (idempotent)
    ok, res, detail = P.create_lightweight_tag(
        _REPO["org"], _REPO["project"], _REPO["name"], tag, commit)
    if not ok:
        return Blocked(f"tag_authenticator: couldn't create tag '{tag}' on {_REPO['name']} "
                       f"({detail}).", links=links)
    if res.get("created"):
        return Done(f"Tagged the Auth App release: '{tag}' \u2192 commit {commit[:8]} in "
                    f"{_REPO['name']}.", links=links)
    # already existed — idempotent pass ONLY if it points at the same commit
    if res.get("objectId") == commit:
        return Done(f"Auth App release already tagged: '{tag}' \u2192 commit {commit[:8]} "
                    f"(idempotent).", links=links)
    return Blocked(f"tag_authenticator: tag '{tag}' already exists but points at "
                   f"{str(res.get('objectId'))[:8]}, not the release commit {commit[:8]}. "
                   f"A human must reconcile (delete/repoint the tag) before this can pass.",
                   links=links)


run = legacy_run(build)
