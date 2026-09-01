"""Step: `telemetry_verify` — confirm bug-bash telemetry is reaching Kusto (Phase 2, after
`auth_ecs`; checklist Phase 3.3 Step 9, relocated to run right after the Authenticator ECS
build is verified).

Once the Authenticator release-app build (AndroidBuild-1ES) exists, its version IS the bug-bash
APK version. This step checks that telemetry for that version is landing in the ADX release
cluster — proof that the build's instrumentation reaches Kusto from at least one device. The
query is the checklist's own:

    loadaccountsoperations | where AppInfo_Version == "<BUGBASH_APP_VERSION>" | count

Pass criterion: the count is > 0. If it's zero, the bug bash should NOT be declared complete
until telemetry is flowing — the owner posts a heads-up in the Android Core Team channel.

Kusto is reached through the MCP the deterministic engine can't call, so this is a `scout` step:
`build()` resolves the version + composes the query and returns NeedsSkill(kusto_query); the
skill runs it, reads the row count, and calls the `record-telemetry` follow-up (pass if > 0,
else `attention` — which surfaces the Android-Core-Team heads-up as a blocked task).
"""
from __future__ import annotations

import os as _os

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.mockctx import mock_input, MISSING
from tools.coordinates import coords

ID = "telemetry_verify"
KIND = "scout"

# The telemetry table + version column are this step's QUERY CONTRACT (coupled 1:1 to the
# parsing below), so they live here. The CLUSTER + database are the same ADX target the
# `adx_access` readiness item already owns — read from config/readiness.yaml so there's a
# single source of truth for the cluster coordinates.
TABLE = "loadaccountsoperations"
VERSION_COLUMN = "AppInfo_Version"

MOCKABLE = {
    "version": {"kind": "input",
                "desc": "Use this bug-bash APK version instead of discovering it from the "
                        "Authenticator release-app build (a REAL Kusto query on your version)."},
}


def _adx_target():
    """(cluster_uri, database) for the ADX release cluster — read from the `adx_access` readiness
    item so the cluster coordinates have one home. Returns (None, None) if it can't be read."""
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                         "config", "readiness.yaml")
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return (None, None)

    def _walk(node):
        if isinstance(node, dict):
            if node.get("id") == "adx_access" and node.get("cluster_uri"):
                return (node.get("cluster_uri"), node.get("database"))
            for v in node.values():
                r = _walk(v)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = _walk(v)
                if r:
                    return r
        return None

    return _walk(data) or (None, None)


def _query(version: str) -> str:
    return (f"{TABLE}\n"
            f'| where {VERSION_COLUMN} == "{version}"\n'
            f"| count")


def _version(state):
    """The bug-bash APK version = the Authenticator release-app build's version. A `version`
    mock overrides discovery. Returns (version, detail) — version is None on failure."""
    ov = mock_input("version", MISSING)
    if ov is not MISSING and ov:
        return (str(ov).strip(), "")
    branch = (getattr(state, "versions", None) or {}).get("authenticator")
    if not branch:
        return (None, "no Authenticator release branch on record yet — Phase-2 "
                      "orchestrator_health populates state.versions.authenticator")
    from tools.pipelines import find_auth_release_build
    ok, info, detail = find_auth_release_build(branch)
    if not ok:
        return (None, f"could not read the Authenticator release-app build ({detail})")
    if not info:
        return (None, "no succeeded Authenticator release-app build on the release branch yet")
    return (info.get("version"), "")


def build(state):
    version, detail = _version(state)
    if not version:
        return Blocked(f"telemetry_verify: {detail}.")
    cluster_uri, database = _adx_target()
    if not cluster_uri:
        return Blocked("telemetry_verify: could not read the ADX cluster coordinates from "
                       "config/readiness.yaml (adx_access item).")

    return NeedsSkill(
        tool="kusto_query",
        payload={
            "cluster_uri": cluster_uri,
            "database": database,
            "query": _query(version),
            "version": version,
            # After running the query, DON'T blind-record pass: read the Count and run this
            # follow-up with it — it passes only when telemetry is flowing (rows > 0), else it
            # records `attention` (post the heads-up in the Android Core Team channel).
            "followup_command": "record-telemetry",
        },
        record_as=ID,
        summary=f"Verify bug-bash telemetry for {version} in Kusto (pass if any rows return)",
        note=f"queried {TABLE} for {VERSION_COLUMN} == {version}",
        outbound=False,
    )
