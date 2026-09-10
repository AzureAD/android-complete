"""Step: `telemetry_verify` — confirm bug-bash telemetry is reaching Kusto (Phase 2, after
`auth_ecs`; checklist Phase 3.3 Step 9, relocated to run right after the Authenticator ECS
build is verified).

The Authenticator ECS RC build captured by auth_ecs supplies the tested APK version,
not the separate release-app/NGMS build. This step checks that telemetry for that version is landing in the ADX release
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
import re

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.lib.mockctx import mock_input, MISSING

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
                        "verified Authenticator ECS RC build (a REAL Kusto query on your version)."},
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
    """Use the current verified ECS RC APK, stripping its build-number RC suffix. A `version`
    mock overrides discovery. Returns (version, detail) — version is None on failure."""
    ov = mock_input("version", MISSING)
    if ov is not MISSING and ov:
        return (str(ov).strip(), "")
    from steps.build_verify._common import latest_rc
    build = (latest_rc(state).get("auth") or {}).get("build") or {}
    if not build.get("complete") or build.get("result") not in ("succeeded", "partiallySucceeded"):
        return (None, "no successful Authenticator ECS RC build recorded for the current RC — "
                      "run auth_ecs first")
    number = build.get("build_number") or ""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-rc(\d+)", number, re.IGNORECASE)
    if not match or match.group(2) != str(build.get("run_id")):
        return (None, f"Authenticator ECS build {build.get('run_id')} has a missing or invalid "
                      f"APK build number ({number!r}) — re-run auth_ecs to capture it")
    return (match.group(1), "")


def build(state):
    version, detail = _version(state)
    if not version:
        return Blocked(f"telemetry_verify: {detail}.")
    cluster_uri, database = _adx_target()
    if not cluster_uri:
        return Blocked("telemetry_verify: could not read the ADX cluster coordinates from "
                       "config/readiness.yaml (adx_access item).")

    from steps.build_verify._common import latest_rc
    from tools.coordinates import coords
    from tools.pipelines import auth_build_url
    rc = latest_rc(state)
    auth = rc.get("auth") or {}
    apk = auth.get("build") or {}
    pipeline = coords.pipeline("auth_build")
    source = {
        "rc": rc.get("rc"),
        "org": pipeline["org"], "project": pipeline["project"],
        "pipeline_id": pipeline["def"],
        "build_id": apk.get("run_id"), "build_number": apk.get("build_number"),
        "ui_test_build_id": (auth.get("test") or {}).get("run_id"),
        "version_source": "ADO buildNumber: <AppInfo_Version>-rc<build_id>",
    }
    if mock_input("version", MISSING) is not MISSING:
        source = {"version_source": "explicit test override"}
    return NeedsSkill(
        tool="kusto_query",
        payload={
            "cluster_uri": cluster_uri,
            "database": database,
            "query": _query(version),
            "version": version,
            "source": source,
            "links": ([{"name": "Authenticator ECS APK build",
                        "url": auth_build_url(source["build_id"])}]
                      if source.get("build_id") else []),
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
