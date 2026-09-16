"""Maven Central publication checks for the Phase-4 `verify_pub` step.

The release publishes MSAL / Common / Common4j to Maven Central. Central is public, so we
verify a version is live with an anonymous HEAD on its `.pom` (the definitive per-version
artifact): HTTP 200 = published, 404 = not there yet. New releases can take a few hours to
propagate to repo1.maven.org, so the caller treats 404 as "not yet" (poll), not a failure.
"""
from __future__ import annotations

import urllib.request
import urllib.error

CENTRAL = "https://repo1.maven.org/maven2"

# Artifact coordinates: key -> (group path, artifactId). Version is supplied per-release.
ARTIFACTS = {
    "common4j": ("com/microsoft/identity/common4j", "common4j"),
    "common": ("com/microsoft/identity/common", "common"),
    "msal": ("com/microsoft/identity/client/msal", "msal"),
}


def pom_url(key: str, version: str) -> str:
    group, artifact = ARTIFACTS[key]
    return f"{CENTRAL}/{group}/{version}/{artifact}-{version}.pom"


def is_published(key: str, version: str, timeout: int = 25):
    """(ok, published, detail) — HEAD the artifact's .pom on Maven Central.
      published True  -> HTTP 200 (live)
      published False -> HTTP 404 (not there yet)
      ok False        -> could not check (network/other HTTP) — caller should not conclude 'no'."""
    url = pom_url(key, version)
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (True, resp.status == 200, f"HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return (True, False, "HTTP 404 (not published yet)")
        return (False, False, f"HTTP {e.code}")
    except Exception as e:  # noqa: BLE001 — urllib/URLError/timeout
        return (False, False, f"{type(e).__name__}: {e}")
