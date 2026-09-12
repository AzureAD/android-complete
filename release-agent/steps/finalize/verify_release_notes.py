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

from orchestrator.outcomes import Done, Blocked, InProgress
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import prs as PR
from steps.finalize import integ_prs as IP

ID = "verify_release_notes"
KIND = "agent"

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


def _versions(state):
    v = mock_input("versions", MISSING)
    if v is not MISSING and v:
        return dict(v)
    return dict(getattr(state, "versions", {}) or {})


def _gh_repo(key):
    return IP.CONFIG[key]["gh_repo"]


def _tag(version):
    return f"v{version}"


def _check(key, version):
    """(ok, published, url, detail) — mock-first, else a live `gh release view`."""
    inj = mock_input("results", MISSING)
    if inj is not MISSING and isinstance(inj, dict) and key in inj:
        r = str(inj[key]).lower()
        if r == "published":
            return (True, True, None, "injected published")
        if r == "missing":
            return (True, False, None, "injected missing")
        return (False, False, None, "injected error")
    ok, pub, info, detail = PR.gh_release_exists(_gh_repo(key), _tag(version))
    return (ok, pub, (info or {}).get("url"), detail)


def _release_url(key, version):
    host_owner_repo = _gh_repo(key)
    base = ("https://" + host_owner_repo if "/" in host_owner_repo and "." in host_owner_repo.split("/")[0]
            else "https://github.com/" + host_owner_repo)
    return f"{base}/releases/tag/{_tag(version)}"


def build(state):
    versions = _versions(state)
    missing_v = [lbl for _k, vk, lbl in TARGETS if not versions.get(vk)]
    if missing_v:
        return Blocked(
            f"verify_release_notes: missing release version(s) — need "
            f"{', '.join(missing_v).lower()} in state.versions (populated at Phase 2). "
            f"Provide the `versions` mock for testing.")

    links, published, pending, errors = [], [], [], []
    for key, vkey, label in TARGETS:
        version = versions.get(vkey)
        ok, pub, url, detail = _check(key, version)
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


run = legacy_run(build)
