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

Step defaults (`step_defaults` / offline scenarios):
  versions : dict override {common, msal} (else read from state.versions).
  results  : dict key->'published'|'missing'|'error' to skip the live HEADs.
"""
from __future__ import annotations

from orchestrator.step_context import StepContext

from orchestrator.outcomes import Done, Blocked, InProgress
from orchestrator.step_context import MISSING
from tools import maven as M

ID = "verify_pub"
KIND = "agent"
NAME = 'Verify Maven Central publication (Common4j + Common + MSAL)'
REQUIRED_TOOLS = ()
EFFECT_MODE = "read_only"
RECHECK_WHEN_BLOCKED = True

# What to verify: (artifact key, which state.versions value supplies its version, label).
TARGETS = [
    ("common4j", "common", "Common4j"),
    ("common", "common", "Common"),
    ("msal", "msal", "MSAL"),
]

CONFIG = {"central": M.CENTRAL, "poll_in_min": 60}

from steps.finalize._orchestrator_gate import payload_versions as _versions

def _check(context, key, version):
    """(ok, published, detail) — configured `results` first, else a live Maven Central HEAD."""
    inj = context.input("results", MISSING)
    if inj is not MISSING and isinstance(inj, dict) and key in inj:
        r = str(inj[key]).lower()
        if r == "published":
            return (True, True, "injected published")
        if r == "missing":
            return (True, False, "injected missing")
        return (False, False, "injected error")
    return context.services.assets.is_published(key, version)

def build(context: StepContext):
    versions = _versions(context)
    if not versions.get("common") or not versions.get("msal"):
        return Blocked(
            "verify_pub: missing release versions (need common + msal in state.versions, "
            "populated at Phase 2). Provide `versions` in `step_defaults` for rehearsals.")

    links, published, pending, errors = [], [], [], []
    for key, vkey, label in TARGETS:
        version = versions.get(vkey)
        links.append({"name": f"{label} {version} on Maven Central",
                      "url": M.pom_url(key, version).rsplit("/", 1)[0] + "/"})
        ok, is_pub, detail = _check(context, key, version)
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

KNOWLEDGE = {'summary': 'Verify MSAL / Common / Common4j are live on Maven Central at the release '
            'versions.',
 'what': "After the orchestrator's publish stages, confirm the three externally-published "
         'artifacts appear on Maven Central: common4j and common at the Common version, and '
         'msal at the MSAL version. Versions come from state.versions (populated at Phase 2). '
         'Common4j has no separate version — it publishes at the Common version. All three '
         'present -> done; not-yet-present -> in-progress (poll); a network error that blocks '
         "the check -> blocked (so it's surfaced, not assumed as 'not published').\n",
 'who': 'Scout (an agent step) — read-only anonymous HTTPS to Maven Central; no auth needed.',
 'where': ['Common4j: https://repo1.maven.org/maven2/com/microsoft/identity/common4j/',
           'Common:   https://repo1.maven.org/maven2/com/microsoft/identity/common/',
           'MSAL:     https://repo1.maven.org/maven2/com/microsoft/identity/client/msal/'],
 'how': "Runs automatically and re-checks on a poll. It HEADs each artifact's per-version .pom "
        '(200 = live, 404 = not yet). New releases can take a few HOURS to propagate to '
        "repo1.maven.org, so a 404 is 'not yet' (keep polling), not a failure. The "
        'search.maven.org UI can lag ~a day — the repo1 direct links above update within hours '
        'and are what this step checks. Rehearse via `step_defaults` (`results` and `versions`).\n',
 'links': [{'name': 'Publish to Maven Central (internal checklist)',
            'url': 'https://eng.ms/docs/microsoft-security/identity/entra-developer-application-platform/auth-client/authn-sdk-msal-android/android-auth-libraries/releases/internal-release-checklist/publish-to-maven-central'}],
 'faqs': [{'q': "Why does it stay 'in progress' for a while?",
           'a': 'Maven Central propagation to repo1.maven.org typically takes a few hours '
                'after publish. The step polls the .pom URLs until all three return 200, then '
                "completes. It never marks the release failed just because an artifact isn't "
                'visible yet.'},
          {'q': 'What version is Common4j checked at?',
           'a': 'The Common version. The orchestrator publishes Common4j with Common4jVersion '
                "= NextCommonVersion, so there's no separate Common4j version to track."}]}
