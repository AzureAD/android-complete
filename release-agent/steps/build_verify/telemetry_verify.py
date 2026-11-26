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
from dataclasses import dataclass

from orchestrator.step_context import StepContext

import os as _os
import re

from orchestrator.outcomes import NeedsSkill, Blocked
from orchestrator.step_context import MISSING

ID = "telemetry_verify"
KIND = "scout"
NAME = 'Verify bug-bash telemetry in Kusto'
REQUIRED_TOOLS = ('kusto',)
REQUIRES_APPROVAL = False
REPEATABLE = True

def should_recheck_blocked(record) -> bool:
    """Re-query Kusto on scheduled drain ticks only when blocked due to 0 ingested rows."""
    return (getattr(record, "data", None) or {}).get("rows") == 0

@dataclass(frozen=True)
class BuildParameters:
    version: str | None = None

    def __post_init__(self):
        if self.version is not None and not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError("Version must be the numeric three-part APK version")

PARAMETERS = {"build": BuildParameters}

# The telemetry table + version column are this step's QUERY CONTRACT (coupled 1:1 to the
# parsing below), so they live here. The CLUSTER + database are the same ADX target the
# `adx_access` readiness item already owns — read from config/readiness.yaml so there's a
# single source of truth for the cluster coordinates.
TABLE = "loadaccountsoperations"
VERSION_COLUMN = "AppInfo_Version"

def _adx_target(context):
    """(cluster_uri, database) for the ADX release cluster — read from the `adx_access` readiness
    item so the cluster coordinates have one home. Returns (None, None) if it can't be read."""
    path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                         "config", "readiness.yaml")
    try:
        import yaml
        data = yaml.safe_load(context.services.assets.text_file(path)) or {}
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

def _version(context):
    """Use the current verified ECS RC APK, stripping its build-number RC suffix. A `version`
    override skips discovery. Returns (version, detail) — version is None on failure."""
    ov = getattr(context.parameters, "version", None)
    if ov is None:
        ov = context.input("version", MISSING)
    if ov is not MISSING and ov:
        return (str(ov).strip(), "")
    from steps.build_verify._common import latest_rc
    build = (latest_rc(context).get("auth") or {}).get("build") or {}
    if not build.get("complete") or build.get("result") not in ("succeeded", "partiallySucceeded"):
        return (None, "no successful Authenticator ECS RC build recorded for the current RC — "
                      "run auth_ecs first")
    number = (build.get("build_number") or "").strip()
    # Accept `<X.Y.Z>-rc<build_id>` (with optional flavor suffix like `-ecs`) OR a bare 4-digit-middle
    # Authenticator APK version `<X.YYYY.Z>` (never a 3-part Broker version like `16.6.0-RC1-ecs` or mismatched `-rc<other>`).
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-rc(\d+)(?:-[A-Za-z0-9._-]+)?", number, re.IGNORECASE)
    if match:
        if match.group(2) != str(build.get("run_id")):
            return (None, f"Authenticator ECS build {build.get('run_id')} has a mismatched "
                          f"APK build number ({number!r}) — re-run auth_ecs to capture it")
        return (match.group(1), "")
    bare_apk = re.fullmatch(r"(\d+\.\d{4}\.\d+)", number)
    if bare_apk:
        return (bare_apk.group(1), "")
    return (None, f"Authenticator ECS build {build.get('run_id')} has a missing or invalid "
                  f"APK build number ({number!r}) — re-run auth_ecs to capture it")

def build(context: StepContext):
    version, detail = _version(context)
    if not version:
        return Blocked(f"telemetry_verify: {detail}.")
    cluster_uri, database = _adx_target(context)
    if not cluster_uri:
        return Blocked("telemetry_verify: could not read the ADX cluster coordinates from "
                       "config/readiness.yaml (adx_access item).")

    from steps.build_verify._common import latest_rc
    from tools.coordinates import coords
    from tools.pipelines import auth_build_url
    rc = latest_rc(context)
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
    if getattr(context.parameters, "version", None) is not None:
        source = {"version_source": "explicit owner selection"}
    elif context.input("version", MISSING) is not MISSING:
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

KNOWLEDGE = {'summary': "Confirm the bug-bash APK version's telemetry is reaching the ADX/Kusto cluster.",
 'what': 'Once the Authenticator release-app build (AndroidBuild-1ES) exists, its version IS '
         "the bug-bash APK version. This step runs the checklist's telemetry query — "
         '`loadaccountsoperations | where AppInfo_Version == "<version>" | count` — against '
         "the ADX release cluster and passes only when the count is > 0 (proof the build's "
         'instrumentation is reaching Kusto from at least one device). This is checklist Phase '
         '3.3 Step 9, relocated to run right after the Authenticator ECS build is verified.\n',
 'who': "Scout runs it automatically (a scout-assisted check) — the deterministic engine can't "
        'reach the Kusto MCP. The version is discovered from the release-app build; no user '
        'action unless it comes back empty.\n',
 'where': ['ADX release-telemetry cluster + database (same as the adx_access readiness item).',
           'Table: loadaccountsoperations · version column: AppInfo_Version.'],
 'how': "Runs via step-action → the kusto_query MCP with the step's "
        'cluster_uri/database/query. Read the returned Count, then run the follow-up '
        '`record-telemetry --release <id> --rows <N> --version <ver>`: rows > 0 records pass; '
        'rows == 0 records `attention` — the owner posts a heads-up in Android Core Team '
        'before declaring the bug bash complete, then re-runs the check. This owner task is '
        'not permission for an ad-hoc automatic send. For rehearsals, configure `version` in '
        '`step_defaults` (`config/environment.yaml`) or pass `--param version=<v>`.\n',
 'faqs': [{'q': 'Why is a Phase-3 checklist step (3.3 S9) run in Phase 2?',
           'a': 'Because the check only needs the built APK version to exist, and running it '
                "right after auth_ecs gives an early smoke-signal that the build's telemetry "
                'pipeline works — before the go/hold at rc_report. Placement was the release '
                "owner's call."},
          {'q': 'What if telemetry is sparse right after the build?',
           'a': 'The bar is just non-zero. As soon as the owner or any tester installs the '
                "build, rows appear. Zero rows means telemetry isn't flowing at all — worth "
                'surfacing before the bash closes.'}]}
