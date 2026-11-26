"""Step: `oneauth_access` — verify write access to the OneAuth repo (Phase 0).

A later phase pushes a PR to the OneAuth repo (https://office.visualstudio.com/OneAuth/_git/
OneAuth), whose branch policy requires branches named 'user/<alias>/<branch-name>'. The only
reliable proof that we can do that is to actually CREATE such a branch — so this step creates a
throwaway 'user/<alias>/scout-oneauth-access-check' branch off master and immediately deletes it.

  * create AND cleanup acknowledged -> DONE (the probe branch is cleaned up);
  * create rejected / 403 -> no access -> BLOCKED, pointing the owner at the myaccess package to
    request OneAuth R/W. Cleanup failure instead requires review of the leftover probe.

Deterministic (same access => same verdict) and self-cleaning, so it's an `agent` step the
engine runs in-process.

Step defaults (`step_defaults` / offline scenarios):
  alias  : override the alias used for the probe branch (skip the az lookup).
  access : inject 'granted' | 'denied' to skip the live branch probe.
  fail   : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import Done, Blocked
from orchestrator.step_context import MISSING
from tools import checks
import re

from orchestrator.authority import WriteOperation

WRITES = (WriteOperation.ONEAUTH_WRITE_ACCESS,)
ID = "oneauth_access"
KIND = "agent"
NAME = 'Verify OneAuth write access (create a probe branch)'
REQUIRED_TOOLS = ('az',)
EFFECT_MODE = "idempotent"
EFFECT_RECOVERY = "frozen"

def prepare_effect(context):
    execution = context.evidence.step("preflight", ID).execution or {}
    if isinstance(execution.get("effect_input"), dict):
        return dict(execution["effect_input"])
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"oneauth_access: {fail}", links=_links())
    alias = _alias(context)
    if not alias:
        return Blocked("oneauth_access: couldn't resolve your alias (az account) to build the "
                       "'user/<alias>/…' probe branch — run `az login`.", links=_links())
    if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", alias) or ".." in alias:
        return Blocked("oneauth_access: invalid alias for the probe branch.", links=_links())
    return {
        "alias": alias,
        "org": checks.ONEAUTH_ORG,
        "project": checks.ONEAUTH_PROJECT,
        "repo": checks.ONEAUTH_REPO,
    }

def execute(context: StepContext):
    frozen = context.effect.execution["effect_input"]
    return _check_access(context,
        frozen["alias"],
        org=frozen["org"],
        project=frozen["project"],
        repo=frozen["repo"],
    )

CONFIG = {
    "repo_url": checks.ONEAUTH_REPO_URL,
    "access_package": checks.ONEAUTH_ACCESS_PACKAGE,
    "branch_policy": "user/<alias>/branch-name",
}

def _links():
    return [{"name": "OneAuth repo", "url": CONFIG["repo_url"]},
            {"name": "Request OneAuth R/W (access package)", "url": CONFIG["access_package"]}]

def _alias(context):
    a = context.input("alias", MISSING)
    if a is not MISSING:
        return a
    user = context.services.identities.current_az_user()
    return ((user or "").split("@")[0]) or None

def build(context: StepContext):
    if context.effect is None:
        raise ValueError("Access probing requires an authorized effect context")
    return execute(context)

def _check_access(context,
    alias,
    *,
    org=checks.ONEAUTH_ORG,
    project=checks.ONEAUTH_PROJECT,
    repo=checks.ONEAUTH_REPO,
):
    access = context.input("access", MISSING)
    if access is not MISSING:
        granted = str(access).lower() == "granted"
        detail = f"injected access={access}"
    else:
        granted, detail = context.effect.services.oneauth_write_access(
            alias, org=org, project=project, repo=repo
        )

    if granted:
        return Done(
            f"OneAuth write access confirmed for '{alias}' — created and deleted a "
            f"'user/{alias}/…' probe branch, so the release PR branch can be created later.",
            links=_links())
    return Blocked(
        f"oneauth_access: no write access confirmed for '{alias}' ({detail}). "
        f"Resolve the probe error or leftover probe branch before rerunning; request R/W "
        f"via the access package if access was denied. RERUN only after resolving the issue.",
        links=_links())

KNOWLEDGE = {'summary': 'Verify you can create a branch in the OneAuth repo (needed for the later OneAuth '
            'PR).',
 'what': 'A later phase pushes a PR to the OneAuth repo '
         '(office.visualstudio.com/OneAuth/_git/OneAuth), whose branch policy requires '
         "branches named 'user/<alias>/<branch-name>'. This step proves you can do that NOW by "
         "actually creating a throwaway 'user/<alias>/scout-oneauth-access-check' branch off "
         'the exact master ref and immediately deleting it. Only acknowledged creation AND '
         'cleanup let the step pass; if either is rejected or uncertain the step BLOCKS so the '
         'missing access is caught at pre-flight rather than when the release needs to open '
         'its PR.\n',
 'who': "The RELEASE ENGINEER (release owner) needs OneAuth R/W. If you don't have it, request "
        "the access package yourself and rerun once it's granted — don't hand the release "
        'forward with this unresolved.\n',
 'how': 'Scout runs it automatically during `next` (an agent step) — it creates and deletes '
        "the probe branch via the Git refs API. If BLOCKED: request 'R/W (repo) external "
        "contributors' via the myaccess package (link below), wait for approval, then RERUN "
        'this step to re-check. Access grants can take time to propagate; re-evaluate once you '
        'can see the repo in the browser. Cleanup is checked, not assumed. A failed cleanup or '
        'existing probe with a different commit requires owner review; do not blindly delete '
        'unknown content.\n',
 'faqs': [{'q': 'Why create a branch instead of just checking a permission?',
           'a': "Creating the exact 'user/<alias>/…' branch the policy requires is the only "
                'reliable proof — a permissions read can lie about effective access. The probe '
                'branch is deleted immediately.'},
          {'q': 'It blocked — what do I do?',
           'a': 'Read the exact error first. For denied access, request OneAuth R/W and rerun '
                'once granted. A cleanup failure or existing probe with unverified content '
                'needs owner review before retry; it is not proof that create permission is '
                'missing.'}],
 'links': [{'name': 'OneAuth repo',
            'url': 'https://office.visualstudio.com/OneAuth/_git/OneAuth'},
           {'name': 'Request OneAuth R/W (access package)',
            'url': 'https://myaccess.microsoft.com/@microsoft.onmicrosoft.com#/access-packages/09fdec6b-eafa-4905-a7c0-b5e514bba368'}]}
