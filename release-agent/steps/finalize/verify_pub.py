"""Step: `verify_pub` — verify Maven Central publication (Phase 4, finalize, F3).

After the orchestrator's publish stages, MSAL / Common / Common4j should appear on Maven
Central. This step confirms all three are live at the release versions:
  * common4j @ the Common version,
  * common   @ the Common version,
  * msal     @ the MSAL version.
Versions come from `state.versions` (populated at Phase 2). Common4j has no separate version —
it's published at the Common version (the orchestrator sets Common4jVersion = NextCommonVersion).

New releases can take a few HOURS to propagate to repo1.maven.org, so a not-yet-present
artifact is IN-PROGRESS (poll again), NOT a failure. All three present -> Done. A network
error that prevents checking -> Blocked (can't verify), so it's surfaced rather than assumed.

Read-only anonymous HTTPS (Maven Central is public) -> `agent` step.

Mock knobs (mocks.local.yaml / tests):
  versions : dict override {common, msal} (else read from state.versions).
  results  : dict key->'published'|'missing'|'error' to skip the live HEADs (tests).
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked, InProgress
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import maven as M

ID = "verify_pub"
KIND = "agent"

# What to verify: (artifact key, which state.versions value supplies its version, label).
TARGETS = [
    ("common4j", "common", "Common4j"),
    ("common", "common", "Common"),
    ("msal", "msal", "MSAL"),
]

CONFIG = {"central": M.CENTRAL, "poll_in_min": 60}

MOCKABLE = {
    "versions": {"kind": "input", "desc": "dict override {common, msal} (else state.versions)."},
    "results": {"kind": "input",
                "desc": "dict artifactKey->'published'|'missing'|'error' (skip the live HEADs)."},
}


def _versions(state):
    v = mock_input("versions", MISSING)
    if v is not MISSING and v:
        return dict(v)
    return dict(getattr(state, "versions", {}) or {})


def _check(key, version):
    """(ok, published, detail) — mock-first, else a live Maven Central HEAD."""
    inj = mock_input("results", MISSING)
    if inj is not MISSING and isinstance(inj, dict) and key in inj:
        r = str(inj[key]).lower()
        if r == "published":
            return (True, True, "injected published")
        if r == "missing":
            return (True, False, "injected missing")
        return (False, False, "injected error")
    return M.is_published(key, version)


def build(state):
    versions = _versions(state)
    if not versions.get("common") or not versions.get("msal"):
        return Blocked(
            "verify_pub: missing release versions (need common + msal in state.versions, "
            "populated at Phase 2). Provide the `versions` mock for testing.")

    links, published, pending, errors = [], [], [], []
    for key, vkey, label in TARGETS:
        version = versions.get(vkey)
        links.append({"name": f"{label} {version} on Maven Central",
                      "url": M.pom_url(key, version).rsplit("/", 1)[0] + "/"})
        ok, is_pub, detail = _check(key, version)
        tag = f"{label} {version}"
        if not ok:
            errors.append(f"{tag} ({detail})")
        elif is_pub:
            published.append(tag)
        else:
            pending.append(tag)

    if errors:
        return Blocked(
            "verify_pub: couldn't verify Maven Central for " + "; ".join(errors) +
            ". Central may be unreachable — retry; do not assume the release failed.", links=links)
    if pending:
        return InProgress(
            "verify_pub: waiting on Maven Central publication — not live yet: "
            f"{', '.join(pending)}"
            + (f" (published: {', '.join(published)})" if published else "")
            + ". New releases can take a few hours to propagate; re-checking.",
            links=links, poll_in_min=CONFIG["poll_in_min"])
    return Done(
        f"All three artifacts are live on Maven Central: {', '.join(published)}.", links=links)


run = legacy_run(build)
