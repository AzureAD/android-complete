"""Step: `verify_release_notes` — verify the GitHub release notes are published (Phase 4, finalize).

The orchestrator's 'Publish GitHub Release Notes' stage (approved via `publish_notes_gate`)
publishes a GitHub release for each of the three libraries. This step confirms all three exist at
the release versions:
  * broker @ state.versions.broker  — GitHub Enterprise (msft.ghe.com/security/ad-accounts-for-android)
  * msal   @ state.versions.msal    — github.com/AzureAD/microsoft-authentication-library-for-android
  * common @ state.versions.common  — github.com/AzureAD/microsoft-authentication-library-common-for-android

Each release is tagged `v<version>` (e.g. v16.5.0 / v8.4.2 / v24.6.0). Complements `verify_pub`,
which checks the Maven Central artifacts — this one checks the GitHub releases.

The repo identities are REUSED from `integ_prs.CONFIG[<key>].gh_repo` (the single source for the
release repos' GitHub/GHE slugs) — not duplicated here.

A not-yet-published release is IN-PROGRESS (poll again), NOT a failure. All three present -> Done.
A draft release, or an error that prevents checking (auth/network) -> surfaced (Blocked / pending)
rather than assumed. Read-only `gh release view` -> `agent` step.

Mock knobs (mocks.local.yaml / tests):
  versions : dict override {broker, msal, common} (else read from state.versions).
  results  : dict key->'published'|'missing'|'error' to skip the live gh calls (tests).
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import Done, Blocked, InProgress
from steps.lib.mockctx import MISSING
from steps.finalize import integ_prs as IP

ID = "verify_release_notes"
KIND = "agent"
NAME = 'Verify GitHub release notes (Broker + MSAL + Common)'
REQUIRED_TOOLS = ('gh',)
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True

# (repo key in integ_prs.CONFIG, which state.versions value supplies its version, label).
TARGETS = [
    ("broker", "broker", "Broker"),
    ("msal", "msal", "MSAL"),
    ("common", "common", "Common"),
]

CONFIG = {"poll_in_min": 30}

MOCKABLE = {
    "versions": {"kind": "input", "desc": "dict override {broker, msal, common} (else state.versions)."},
    "results": {"kind": "input",
                "desc": "dict repoKey->'published'|'missing'|'error' (skip the live gh calls)."},
}


from steps.finalize._orchestrator_gate import payload_versions as _versions


def _gh_repo(key):
    return IP.CONFIG[key]["gh_repo"]


def _tag(version):
    return f"v{version}"


def _check(context, key, version):
    """(ok, published, url, detail) — mock-first, else a live `gh release view`."""
    inj = context.input("results", MISSING)
    if inj is not MISSING and isinstance(inj, dict) and key in inj:
        r = str(inj[key]).lower()
        if r == "published":
            return (True, True, None, "injected published")
        if r == "missing":
            return (True, False, None, "injected missing")
        return (False, False, None, "injected error")
    ok, pub, info, detail = context.services.repositories.gh_release_exists(_gh_repo(key), _tag(version))
    return (ok, pub, (info or {}).get("url"), detail)


def _release_url(key, version):
    host_owner_repo = _gh_repo(key)
    base = ("https://" + host_owner_repo if "/" in host_owner_repo and "." in host_owner_repo.split("/")[0]
            else "https://github.com/" + host_owner_repo)
    return f"{base}/releases/tag/{_tag(version)}"


def build(context: StepContext):
    versions = _versions(context)
    missing_v = [lbl for _k, vk, lbl in TARGETS if not versions.get(vk)]
    if missing_v:
        return Blocked(
            f"verify_release_notes: missing release version(s) — need "
            f"{', '.join(missing_v).lower()} in state.versions (populated at Phase 2). "
            f"Provide the `versions` mock for testing.")

    links, published, pending, errors = [], [], [], []
    for key, vkey, label in TARGETS:
        version = versions.get(vkey)
        ok, pub, url, detail = _check(context, key, version)
        links.append({"name": f"{label} {version} GitHub release",
                      "url": url or _release_url(key, version)})
        tag = f"{label} {version}"
        if not ok:
            errors.append(f"{tag} ({detail})")
        elif pub:
            published.append(tag)
        else:
            pending.append(f"{tag} ({detail})" if detail and detail != "release not found" else tag)

    if errors:
        return Blocked(
            "verify_release_notes: couldn't verify the GitHub release(s) for " + "; ".join(errors) +
            ". GitHub may be unreachable (or a draft) — retry; do not assume the release failed.",
            links=links)
    if pending:
        return InProgress(
            "verify_release_notes: waiting on GitHub release notes — not published yet: "
            f"{', '.join(pending)}"
            + (f" (published: {', '.join(published)})" if published else "")
            + ". Approve/complete the 'Publish GitHub Release Notes' gate; re-checking.",
            links=links, poll_in_min=CONFIG["poll_in_min"])
    return Done(
        f"All three GitHub release notes are published: {', '.join(published)}.", links=links)

KNOWLEDGE = {'summary': 'Verify the GitHub release notes are published for Broker, MSAL, and Common.',
 'what': "After the 'Publish GitHub Release Notes' gate (publish_notes_gate), the orchestrator "
         'publishes a GitHub release for each library. This step confirms all three exist at '
         "the release versions — it's the GitHub complement to verify_pub (which checks Maven "
         "Central). Each release is tagged 'v<version>': Broker @ state.versions.broker, MSAL "
         '@ state.versions.msal, Common @ state.versions.common.\n',
 'who': "Scout (an agent step) — read-only `gh release view` (uses the machine's gh auth).",
 'where': ['Broker: msft.ghe.com/security/ad-accounts-for-android (GitHub Enterprise) — '
           'v<broker>',
           'MSAL:   github.com/AzureAD/microsoft-authentication-library-for-android — v<msal>',
           'Common: github.com/AzureAD/microsoft-authentication-library-common-for-android — '
           'v<common>'],
 'how': 'Runs automatically and re-checks on a poll. For each repo it runs `gh release view '
        'v<version>`; only a matching tag with a boolean non-draft flag and release URL counts '
        'as published. An explicit release-not-found response or a draft stays pending; '
        'repository access errors, malformed evidence and mismatched tags block rather than '
        'pass. The repo slugs are REUSED from integ_prs.CONFIG (the single source for the '
        'release repos). Test with the `results` mock (repoKey -> published|missing|error) and '
        '`versions`.\n',
 'faqs': [{'q': 'How is this different from verify_pub?',
           'a': 'verify_pub checks the Maven Central ARTIFACTS (MSAL/Common/Common4j .pom); '
                'verify_release_notes checks the GitHub RELEASES (Broker/MSAL/Common, tagged '
                'v<version>). Both must be true for the release to be fully published.'},
          {'q': "Why does it include Broker but verify_pub doesn't?",
           'a': "Broker isn't published to Maven Central (it's a private GHE repo), but it "
                'DOES get a GitHub Enterprise release. So the GitHub-notes check covers all '
                'three libraries; the Maven check covers only the two public artifacts (plus '
                'Common4j).'},
          {'q': "What if a release isn't there yet?",
           'a': 'It stays in-progress and re-checks — publishing the notes happens right after '
                'the publish_notes_gate is approved. A draft release or a gh auth error is '
                'surfaced instead of silently passing.'}]}
