"""Step: `tag_authenticator` — tag the Auth App release commit (Phase 5, rollout_start; F6).

Once the release has published, permanently mark the Authenticator app's released build by
creating a git tag on its release commit. Deterministic + idempotent, so it's an `agent` step
the engine runs in-process — there is NO preview/execute: it creates the tag when the step is
reached.

WHAT it tags:
  * repo   — the Authenticator app repo (config/coordinates.yaml repos.authenticator; msazure/One,
             AD-MFA-phonefactor-phoneApp-android).
  * build  — the exact final Authenticator build captured by orchestrator_finalization.
  * commit — that build's sourceVersion on the release branch, not merely the branch head.
  * name   — the captured Auth App version, verified against that build's numeric ADO
             build-tag (e.g. '6.2608.5658'). The auth app does NOT use a 'v' prefix.

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

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked
from steps.lib.mockctx import MISSING
from tools.coordinates import coords

from orchestrator.authority import WriteOperation

WRITES = (WriteOperation.CREATE_LIGHTWEIGHT_TAG,)
ID = "tag_authenticator"
KIND = "agent"
EFFECT_MODE = "idempotent"
EFFECT_RECOVERY = "frozen"


def prepare_effect(context):
    execution = context.evidence.step("rollout_start", ID).execution or {}
    if isinstance(execution.get("effect_input"), dict):
        return dict(execution["effect_input"])
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"tag_authenticator: {fail}")
    branch = _auth_release_branch(context) or "unresolved"
    if branch == "unresolved":
        return Blocked("tag_authenticator: no authenticator release branch on state.versions "
                       "('release/YYYY/MM/DD') — run build_verify.orchestrator_health first.")
    target = _resolve_target(context, branch)
    if isinstance(target, Blocked):
        return target
    return {
        "release": context.release.release_id,
        "branch": branch,
        "repository": {
            "org": _REPO["org"],
            "project": _REPO["project"],
            "name": _REPO["name"],
        },
        **target,
    }


def execute(context: StepContext):
    return _apply_target(context, context.effect.execution["effect_input"])

_REPO = coords.repo("authenticator")

MOCKABLE = {
    "version": {"kind": "input", "desc": "Inject the version / tag name (skip the build lookup)."},
    "commit": {"kind": "input", "desc": "Inject the commit to tag (skip the build lookup)."},
    "dry_run": {"kind": "input", "desc": "Compose the tag but DON'T write it (report only)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _auth_release_branch(context):
    """The Auth App RELEASE branch ('release/YYYY/MM/DD') from state.versions, or None."""
    return (getattr(context.release, "versions", None) or {}).get("authenticator")


def _tag_url(repository, tag_name):
    return (
        f"{repository['org']}/{repository['project']}/_git/"
        f"{repository['name']}?version=GT{tag_name}"
    )


def build(context: StepContext):
    if context.effect is None:
        raise ValueError("Tagging requires an authorized effect context")
    return execute(context)


def _resolve_target(context, branch):
    # 1) resolve the version + commit (injected, or from the captured final release build)
    version = context.input("version", MISSING)
    commit = context.input("commit", MISSING)
    if version is MISSING or commit is MISSING:
        final = (context.evidence.pipeline_runs or {}).get("final") or {}
        build_id = final.get("authenticator_build_id")
        expected_version = final.get("authenticator_version")
        if not build_id or not expected_version:
            return Blocked(
                "tag_authenticator: final Authenticator build evidence is missing — "
                "run finalize.orchestrator_finalization first."
            )
        ok, info, detail = context.services.pipelines.find_auth_release_build(
            branch, build_id=build_id
        )
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"tag_authenticator: couldn't resolve the Auth App version "
                           f"({detail}){hint}.")
        if not info:
            return Blocked(f"tag_authenticator: {detail} — the release-app build hasn't run yet.")
        if str(info.get("build_id")) != str(build_id):
            return Blocked(
                f"tag_authenticator: resolved build {info.get('build_id')} does not match "
                f"captured final build {build_id}."
            )
        if str(info.get("version")) != str(expected_version):
            return Blocked(
                f"tag_authenticator: captured final version {expected_version} does not match "
                f"build {build_id} tag {info.get('version')}."
            )
        version = info["version"] if version is MISSING else version
        commit = info["commit"] if commit is MISSING else commit

    return {
        "tag": str(version).strip(),
        "commit": str(commit).strip(),
        "dry_run": str(context.input("dry_run", "")).lower() in ("1", "true", "yes"),
    }


def _apply_target(context, target):
    tag = target["tag"]
    commit = target["commit"]
    repository = target.get("repository") or _REPO
    links = [{"name": f"Auth tag {tag}", "url": _tag_url(repository, tag)}]

    # 2) dry-run (personal live testing) — compose without writing
    if target.get("dry_run"):
        return Done(f"[dry-run] Would tag {repository['name']} commit {commit[:8]} as '{tag}' "
                    f"(no write).", links=links)

    # 3) create the lightweight tag (idempotent)
    ok, res, detail = context.effect.services.create_lightweight_tag(
        repository["org"],
        repository["project"],
        repository["name"],
        tag,
        commit,
    )
    if not ok:
        return Blocked(f"tag_authenticator: couldn't create tag '{tag}' on {repository['name']} "
                       f"({detail}).", links=links)
    if res.get("created"):
        return Done(f"Tagged the Auth App release: '{tag}' \u2192 commit {commit[:8]} in "
                    f"{repository['name']}.", links=links)
    # already existed — idempotent pass ONLY if it points at the same commit
    if res.get("objectId") == commit:
        return Done(f"Auth App release already tagged: '{tag}' \u2192 commit {commit[:8]} "
                    f"(idempotent).", links=links)
    return Blocked(f"tag_authenticator: tag '{tag}' already exists but points at "
                   f"{str(res.get('objectId'))[:8]}, not the release commit {commit[:8]}. "
                   f"A human must reconcile (delete/repoint the tag) before this can pass.",
                   links=links)
