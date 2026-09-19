"""Authenticator app (msazure/One) build+UI discovery, release tagging, payload PRs."""
from __future__ import annotations

from tools.coordinates import coords
from tools import pipelines as _pp
from concurrent.futures import ThreadPoolExecutor
import json as _json
import re as _re_mod
from urllib.parse import quote, urlencode


# ============================================================ Authenticator ECS RC
# The Authenticator RC app build + its post-build UI tests live in a DIFFERENT org —
# msazure/One — and are NOT part of the Engineering release-verification chain above
# (the orchestrator cuts the auth working-branch; the build self-triggers off that cut,
# the test self-triggers off the build). So this leg is discovered independently and
# read cross-org via the same az/REST helpers, then evaluated on its OWN quality bar
# (both Firebase suites >= AUTH_UI_PASS_THRESHOLD) — it does NOT feed the MRWP UI gate.
AUTH_ORG = coords.org_url("one")
AUTH_PROJECT = coords.project("one")
AUTH_BUILD_DEF = coords.pipeline_def("auth_build")   # AndroidBuildBroker1ES — RC auth-app build
AUTH_TEST_DEF = coords.pipeline_def("auth_test")     # Authenticator Post-Build UI Tests
AUTH_RELEASE_APP_DEF = coords.pipeline_def("auth_release_app")  # AndroidBuild-1ES — release-branch app build
AUTH_SIGNOFF_DEF = coords.pipeline_def("auth_signoff")  # Android Build Release — Release Sign Off
AUTH_SIGNOFF_STAGE_NAME = "Release Sign Off"
# The final Auth App version tag format on the release-app build, e.g. '6.2608.5658'.
_AUTH_RELEASE_VERSION = _re_mod.compile(r"^\d+\.\d+\.\d+$")
_ZERO_SHA = "0" * 40                                 # ADO "create ref" sentinel (no old object)
# The two Firebase device suites the auth leg is gated on (both must clear the threshold).
AUTH_UI_SUITES = tuple(coords.gate("auth_ui_suites"))
AUTH_UI_PASS_THRESHOLD = coords.gate("auth_ui_pass_pct")

# adAccountsVersion encodes the RC iteration + flight flavor, e.g. '16.6.0-RC1-ecs'
# (ECS) or '16.6.0-RC1-local-flights' (Local). This is the deterministic key that
# says which RC/flavor an auth build is — no branch/date parsing needed.
_AUTH_RC_VERSION = _re_mod.compile(r"-RC(\d+)-(ecs|local-flights)$", _re_mod.I)
_DATED_RELEASE = _re_mod.compile(r"^release/(\d{4})/(\d{2})/(\d{2})$")
_COMMIT = _re_mod.compile(r"^[0-9a-f]{40}$", _re_mod.I)
_PR_COMMIT = _re_mod.compile(
    r"^(?:Merged PR|Merge pull request)\s+(\d+)(?::\s*(.*))?",
    _re_mod.I,
)
_DID_PATHS = (
    "/PhoneFactor/VerifiableCredential-Wallet/",
    "/PhoneFactor/VerifiableCredential-SDK",
    "/PhoneFactor/WalletLibrary",
    "/PhoneFactor/WalletLibrary-FaceCheck-Extension",
)
_GENERATED_PATHS = ("/Localization/",)
_GENERATED_TITLES = (
    "Localized file check-in by OneLocBuild Task",
    "LEGO: check in to working",
)
_ECS_FLIGHT_PATH = (
    "/PhoneFactor/ExperimentationLibrary/src/main/java/com/microsoft/authenticator/"
    "experimentation/ecs/entities/EcsFlight.kt"
)
_FLIGHT_LINE = _re_mod.compile(
    r'^\s*([A-Za-z_]\w*)\(\s*"([^"]+)"\s*,\s*(.+)\),\s*$',
    _re_mod.M,
)


def _auth_build_ref(auth_branch):
    """The auth build's git ref from the canonical state.versions.authenticator value.

    orchestrator_health stores it as 'release/YYYY/MM/DD' (from the AuthenticatorBranch
    tag); the RC build runs on the WORKING branch 'working-release/YYYY/MM/DD'. Returns the
    full ref 'refs/heads/working-release/YYYY/MM/DD', or None when no branch is known."""
    if not auth_branch:
        return None
    b = str(auth_branch).strip()
    if b.startswith("refs/heads/"):
        b = b[len("refs/heads/"):]
    if not b.startswith("working-"):
        b = "working-" + b
    return f"refs/heads/{b}"


def _auth_release_ref(auth_branch):
    """The release branch ref used by final Authenticator builds."""
    if not auth_branch:
        return None
    b = str(auth_branch).strip()
    if b.startswith("refs/heads/"):
        return b
    if not b.startswith("release/"):
        b = "release/" + b.strip("/")
    return f"refs/heads/{b}"


def _build_number_version(build_number):
    match = _re_mod.search(r"\d+\.\d+\.\d+", str(build_number or ""))
    return match.group(0) if match else None


def find_auth_ecs_build(auth_branch, timeout=90):
    """Discover the CURRENT-RC Authenticator ECS build (def 475778) on the release's auth
    working-branch. Returns (ok, info, detail) where info is
      {build_id, rc, version, build_number, status, result}
      (or None when no ECS build exists yet). version is the broker library version;
      build_number carries the Authenticator APK version.

    Deterministic selection: among builds on `refs/heads/working-<auth_branch>` whose
    adAccountsVersion matches '-RC<N>-ecs', take the HIGHEST N (the current RC iteration,
    mirroring mrwp_run_ids), newest build id within it. `status`/`result` are returned raw
    so the caller can distinguish in-flight (status != 'completed') from a bad result."""
    ref = _pp._auth_build_ref(auth_branch)
    if not ref:
        return (False, None, "no authenticator branch known (run orchestrator_health first)")
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(AUTH_BUILD_DEF),
         "--org", AUTH_ORG, "--project", AUTH_PROJECT, "--branch", ref, "--top", "60"], timeout)
    if not ok:
        return (False, None, detail)
    by_rc = {}                                   # N -> list of {id, version, status, result}
    for b in builds or []:
        ver = ((b.get("templateParameters") or {}).get("adAccountsVersion")) or ""
        m = _AUTH_RC_VERSION.search(ver)
        if not m or m.group(2).lower() != "ecs":
            continue
        by_rc.setdefault(int(m.group(1)), []).append(
            {"id": b.get("id"), "version": ver, "build_number": b.get("buildNumber"),
             "status": b.get("status"), "result": b.get("result")})
    if not by_rc:
        return (True, None, f"no ECS release-candidate auth build found on {ref}")
    n = max(by_rc)                               # highest RC iteration = current
    newest = max(by_rc[n], key=lambda x: x.get("id") or 0)
    return (True, {"build_id": newest["id"], "rc": n, "version": newest["version"],
                   "build_number": newest["build_number"],
                   "status": newest["status"], "result": newest["result"]}, "")


def find_final_auth_build(auth_branch, timeout=90, *, build_id=None):
    """Find/read the final Authenticator build from def 475778 on the release branch.

    This is Phase-5 evidence: the latest run on refs/heads/release/YYYY/MM/DD supplies the
    Authenticator build id, numeric app version (from buildNumber), and built commit.
    """
    ref = _auth_release_ref(auth_branch)
    if not ref:
        return (False, None, "no authenticator release branch known (run orchestrator_health first)")
    if build_id is not None:
        if not _positive_build_id(build_id):
            return (False, None, f"invalid final Authenticator build id: {build_id!r}")
        ok, build, detail = _pp._ado_rest_get(
            f"{AUTH_ORG}/{AUTH_PROJECT}/_apis/build/builds/{build_id}?api-version=7.1",
            timeout,
        )
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return (False, None, f"{detail}{hint}")
        builds = [build or {}]
    else:
        ok, builds, detail = _pp._az_json(
            ["pipelines", "build", "list", "--definition-ids", str(AUTH_BUILD_DEF),
             "--org", AUTH_ORG, "--project", AUTH_PROJECT, "--branch", ref, "--top", "60"], timeout)
        if not ok:
            return (False, None, detail)
    valid = [b for b in (builds or []) if _positive_build_id(b.get("id"))]
    if not valid:
        return (True, None, f"no final Authenticator build found on {ref} in def {AUTH_BUILD_DEF}")
    newest = max(valid, key=lambda item: int(item["id"]))
    if str(newest.get("sourceBranch") or ref) != ref:
        return (False, None, f"final Authenticator build {newest.get('id')} belongs to "
                f"{newest.get('sourceBranch') or 'an unknown branch'}, not {ref}")
    version = _build_number_version(newest.get("buildNumber"))
    if not version:
        return (False, None, f"final Authenticator build {newest.get('id')} has no numeric app "
                             f"version in buildNumber {newest.get('buildNumber')!r}")
    return (True, {
        "build_id": newest.get("id"),
        "version": version,
        "commit": newest.get("sourceVersion"),
        "build_number": newest.get("buildNumber"),
        "status": newest.get("status"),
        "result": newest.get("result"),
    }, "")


def _stage_record(records, stage_name=AUTH_SIGNOFF_STAGE_NAME):
    desired = str(stage_name or "").casefold()
    for record in records or []:
        if record.get("type") != "Stage":
            continue
        names = (record.get("identifier"), record.get("name"))
        if any(str(name or "").casefold() == desired for name in names):
            return record
    return None


def _resource_build_ids(definition_id, run_id, timeout=60):
    """Return build/run ids referenced by a YAML pipeline's pipeline resources.

    Pipeline 397224 currently runs as the legacy Android Build Release pipeline on
    the release branch. It is expected to become resource-linked to
    AndroidBuildBroker1ES; when that happens, the resource id is the safest match.
    """
    if not _positive_build_id(run_id):
        return (False, None, f"invalid pipeline run id: {run_id!r}")
    ok, run, detail = _pp._ado_rest_get(
        f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_apis/pipelines/{definition_id}/runs/{run_id}"
        f"?api-version=7.1",
        timeout,
    )
    if not ok:
        return (False, None, detail)
    resources = (((run or {}).get("resources") or {}).get("pipelines") or {})
    if not isinstance(resources, dict):
        return (False, None, f"run {run_id} has malformed pipeline resources")
    ids = set()
    for res in resources.values():
        if not isinstance(res, dict):
            continue
        candidates = [
            res.get("id"),
            res.get("runId"),
            res.get("runID"),
            res.get("version"),
        ]
        pipeline = res.get("pipeline")
        if isinstance(pipeline, dict):
            candidates.extend([
                pipeline.get("id"),
                pipeline.get("runId"),
                pipeline.get("runID"),
                pipeline.get("version"),
            ])
        for value in candidates:
            if _positive_build_id(value):
                ids.add(str(int(str(value))))
    return (True, sorted(ids), "")


def _signoff_build_url(build_id) -> str:
    return f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_build/results?buildId={build_id}&view=results"


def _signoff_info(build, records, *, match_basis, linked_build_ids=None):
    record = _stage_record(records)
    if record is None:
        return None
    return {
        "build_id": build.get("id"),
        "build_number": build.get("buildNumber"),
        "source_branch": build.get("sourceBranch"),
        "source_version": build.get("sourceVersion"),
        "status": build.get("status"),
        "result": build.get("result"),
        "definition_id": AUTH_SIGNOFF_DEF,
        "stage_name": record.get("name") or AUTH_SIGNOFF_STAGE_NAME,
        "stage_ref": record.get("identifier") or record.get("name") or AUTH_SIGNOFF_STAGE_NAME,
        "stage_id": record.get("id"),
        "stage_state": record.get("state"),
        "stage_result": record.get("result"),
        "linked_auth_build_ids": linked_build_ids or [],
        "match_basis": match_basis,
        "url": _signoff_build_url(build.get("id")),
    }


def signoff_stage_started(info):
    state = str((info or {}).get("stage_state") or "").lower()
    result = str((info or {}).get("stage_result") or "").lower()
    return state in {"pending", "inprogress", "completed"} and result not in {"skipped", "canceled"}


def signoff_stage_running(info):
    state = str((info or {}).get("stage_state") or "").lower()
    result = str((info or {}).get("stage_result") or "").lower()
    return state in {"pending", "inprogress"} and result not in {"failed", "skipped", "canceled"}


def signoff_stage_succeeded(info):
    state = str((info or {}).get("stage_state") or "").lower()
    result = str((info or {}).get("stage_result") or "").lower()
    return state == "completed" and result in {"succeeded", "succeededwithissues"}


def signoff_stage_failed(info):
    state = str((info or {}).get("stage_state") or "").lower()
    result = str((info or {}).get("stage_result") or "").lower()
    return state == "canceled" or result in {"failed", "canceled"}


def read_auth_signoff_run(build_id, timeout=60):
    """Read pipeline-397224 signoff run state for a known build/run id."""
    if not _positive_build_id(build_id):
        return (False, None, f"invalid signoff build id: {build_id!r}")
    ok, build, detail = _pp._ado_rest_get(
        f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_apis/build/builds/{build_id}?api-version=7.1",
        timeout,
    )
    if not ok:
        return (False, None, detail)
    if str(((build or {}).get("definition") or {}).get("id")) != str(AUTH_SIGNOFF_DEF):
        return (False, None, f"build {build_id} is not signoff definition {AUTH_SIGNOFF_DEF}")
    okt, records, timeline_detail = _pp.get_timeline(AUTH_ORG, AUTH_PROJECT, build_id, timeout)
    if not okt:
        return (False, None, timeline_detail)
    info = _signoff_info(build or {}, records, match_basis="build_id")
    if info is None:
        return (True, None, f"Release Sign Off stage not found on build {build_id}")
    return (True, info, "")


def find_auth_signoff_run(auth_branch, timeout=90, *, final_auth_build_id=None, scan=100):
    """Find the pipeline-397224 run that owns the release's Release Sign Off stage.

    Current pipeline 397224 is a legacy Android Build Release run on the release branch.
    When it is re-wired to consume AndroidBuildBroker1ES as a pipeline resource, a matching
    resource build id (the Phase-5 `final_auth` id) takes precedence over the branch-only
    legacy fallback.
    """
    ref = _auth_release_ref(auth_branch)
    if not ref:
        return (False, None, "no authenticator release branch known (run orchestrator_health first)")
    url = (f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_apis/build/builds"
           f"?definitions={AUTH_SIGNOFF_DEF}&branchName={quote(ref, safe='')}"
           f"&queryOrder=queueTimeDescending&$top={int(scan)}&api-version=7.1")
    ok, data, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return (False, None, f"{detail}{hint}")
    builds = [b for b in ((data or {}).get("value") or []) if _positive_build_id(b.get("id"))]
    if not builds:
        return (True, None, f"no Android Build Release run (def {AUTH_SIGNOFF_DEF}) on {ref}")
    ordered = sorted(builds, key=lambda item: int(item["id"]), reverse=True)

    resource_reads, resource_matches, resource_mismatches = 0, [], []
    expected = str(int(str(final_auth_build_id))) if _positive_build_id(final_auth_build_id) else None
    if expected:
        for build in ordered:
            okr, linked_ids, resource_detail = _resource_build_ids(AUTH_SIGNOFF_DEF, build.get("id"), timeout)
            if not okr:
                return (False, None, f"could not read pipeline resources for signoff build "
                                     f"{build.get('id')} ({resource_detail})")
            if not linked_ids:
                continue
            resource_reads += 1
            if expected in linked_ids:
                resource_matches.append((build, linked_ids))
            else:
                resource_mismatches.append((build.get("id"), linked_ids))
        if resource_matches:
            build, linked_ids = resource_matches[0]
            okt, records, timeline_detail = _pp.get_timeline(AUTH_ORG, AUTH_PROJECT, build.get("id"), timeout)
            if not okt:
                return (False, None, timeline_detail)
            info = _signoff_info(build, records, match_basis="pipeline_resource",
                                 linked_build_ids=linked_ids)
            if info is None:
                return (True, None, f"Release Sign Off stage not found on signoff build {build.get('id')}")
            return (True, info, "")
        if resource_reads:
            sample = ", ".join(f"{bid}->{ids}" for bid, ids in resource_mismatches[:5])
            return (True, None, f"no signoff run resource-linked to final Authenticator build "
                                f"{expected}; scanned {resource_reads} resource-linked run(s): {sample}")

    build = ordered[0]
    okt, records, timeline_detail = _pp.get_timeline(AUTH_ORG, AUTH_PROJECT, build.get("id"), timeout)
    if not okt:
        return (False, None, timeline_detail)
    info = _signoff_info(build, records, match_basis="release_branch")
    if info is None:
        return (True, None, f"Release Sign Off stage not found on latest signoff build {build.get('id')}")
    return (True, info, "")


def start_auth_signoff_stage(build_id, stage_ref, timeout=60):
    """Start the Release Sign Off stage for a pipeline-397224 build/run.

    Azure DevOps exposes this as the build-stage update endpoint; setting the stage
    state to `pending` is the documented "Run" operation used by the UI.
    """
    if not _positive_build_id(build_id):
        return (False, f"invalid signoff build id: {build_id!r}")
    stage = str(stage_ref or "").strip()
    if not stage:
        return (False, "missing Release Sign Off stage reference")
    url = (
        f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_apis/build/builds/"
        f"{quote(str(build_id), safe='')}/stages/{quote(stage, safe='')}"
        "?api-version=7.1"
    )
    ok, _response, detail = _pp._ado_rest_send(url, "PATCH", {"state": "pending"}, timeout)
    if not ok:
        return (False, detail)
    return (True, f"started stage {stage} on build {build_id}")


def _auth_test_source_build_id(build_id, timeout=60):
    """The auth BUILD id a given post-build-UI-test run consumed — read from its pipeline
    resource `resources.pipelines.authenticatorBuild.pipeline.id` (the completion-trigger
    link PR 16976328 wires up). Returns the int id or None."""
    ok, run, _d = _pp._ado_rest_get(
        f"{AUTH_ORG.rstrip('/')}/{AUTH_PROJECT}/_apis/pipelines/{AUTH_TEST_DEF}/runs/{build_id}"
        f"?api-version=7.1", timeout)
    if not ok:
        return None
    res = (((run or {}).get("resources") or {}).get("pipelines") or {}).get("authenticatorBuild") or {}
    return ((res.get("pipeline") or {}).get("id"))


def find_auth_ui_test_build(auth_build_id, timeout=90, scan=25):
    """Find the post-build UI-test run (def 444678) that tested `auth_build_id`, via the
    deterministic build->test resource link. Returns (ok, test_build_id|None, detail).

    Scans the most-recent `scan` runs of def 444678 (newest first) and returns the first
    whose consumed authenticatorBuild == auth_build_id. None = the test hasn't run yet
    (e.g. still in-flight, or the completion trigger hasn't fired)."""
    if not auth_build_id:
        return (False, None, "no auth build id to match a test against")
    ok, builds, detail = _pp._az_json(
        ["pipelines", "build", "list", "--definition-ids", str(AUTH_TEST_DEF),
         "--org", AUTH_ORG, "--project", AUTH_PROJECT, "--top", str(scan)], timeout)
    if not ok:
        return (False, None, detail)
    ordered = sorted(builds or [], key=lambda b: b.get("id") or 0, reverse=True)
    for b in ordered:
        if _pp._auth_test_source_build_id(b.get("id"), timeout) == int(auth_build_id):
            return (True, b.get("id"), "")
    return (True, None, f"no post-build UI-test run found for auth build {auth_build_id} yet")


def auth_ui_suite_rates(test_build_id, timeout=90):
    """Per-suite pass rates for the auth UI gate. Returns (ok, suites, detail) where
    `suites` maps each AUTH_UI_SUITES name -> {present, passed, failed, total, pct}
    (pct = passed/(passed+failed)*100, excluding not-applicable; None when the suite has no
    executed result). Keeps the separate Firebase gate's ADO aggregate policy; MRWP's
    per-title pass-any policy does not change this gate or its selected build."""
    ok, runs, detail = _pp._test_runs(AUTH_ORG, AUTH_PROJECT, test_build_id, timeout)
    if not ok:
        return (False, None, detail)
    by_name = {r.get("name"): r for r in runs}
    out = {}
    for name in AUTH_UI_SUITES:
        r = by_name.get(name)
        if not r:
            out[name] = {"present": False, "passed": 0, "failed": 0, "total": 0, "pct": None}
            continue
        passed, total = r.get("passedTests") or 0, r.get("totalTests") or 0
        failed = max(total - passed - (r.get("notApplicableTests") or 0), 0)
        denom = passed + failed
        out[name] = {"present": True, "passed": passed, "failed": failed,
                     "total": total,
                     "pct": (round(passed * 100.0 / denom, 1) if denom else None)}
    return (True, out, "")


# ---------------------------------------------------------------- Auth release tag (Phase 4)
def _release_ref(release_branch):
    """Full ref for the Auth App RELEASE branch. state.versions.authenticator is stored as
    'release/YYYY/MM/DD' (the release branch — the working branch is 'working-release/…')."""
    if not release_branch:
        return None
    b = str(release_branch).strip()
    return b if b.startswith("refs/heads/") else f"refs/heads/{b}"


def _positive_build_id(value):
    return (not isinstance(value, bool) and isinstance(value, (int, str))
            and str(value).isdigit() and int(value) > 0)


def find_auth_release_build(release_branch, timeout=90, *, build_id=None, require_latest=True):
    """Read an Auth App release build (def AUTH_RELEASE_APP_DEF = AndroidBuild-1ES).

    When build_id is supplied, require that exact successful build on the release branch.
    Otherwise discover the newest successful build. Returns (ok, info, detail) where info is
      {build_id, version, commit}   (or None when no succeeded build exists on the branch yet).

    The release-app build carries the final Auth App version as an ADO build TAG matching
    _AUTH_RELEASE_VERSION (e.g. '6.2608.5658'); `commit` is the exact commit it was built from
    (build.sourceVersion) — the commit Phase-5 `tag_authenticator` tags with that version."""
    from urllib.parse import quote
    ref = _pp._release_ref(release_branch)
    if not ref:
        return (False, None, "no authenticator release branch known (run orchestrator_health first)")
    if build_id is not None:
        if not _positive_build_id(build_id):
            return (False, None, f"invalid release-app build id: {build_id!r}")
        url = (
            f"{AUTH_ORG}/{AUTH_PROJECT}/_apis/build/builds/"
            f"{quote(str(build_id), safe='')}?api-version=7.1"
        )
        ok, b, detail = _pp._ado_rest_get(url, timeout)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return (False, None, f"{detail}{hint}")
        b = b or {}
        if str(b.get("id")) != str(build_id):
            return (False, None, f"release-app build response did not match build {build_id}")
        if str((b.get("definition") or {}).get("id")) != str(AUTH_RELEASE_APP_DEF):
            return (False, None, f"build {build_id} is not release-app definition {AUTH_RELEASE_APP_DEF}")
        if b.get("sourceBranch") != ref:
            return (False, None, f"release-app build {build_id} belongs to "
                    f"{b.get('sourceBranch') or 'an unknown branch'}, not {ref}")
        if str(b.get("result") or "").lower() != "succeeded":
            return (False, None, f"release-app build {build_id} is not successful "
                    f"(result {b.get('result')!r})")
    else:
        result_filter = "" if require_latest else "&resultFilter=succeeded"
        url = (f"{AUTH_ORG}/{AUTH_PROJECT}/_apis/build/builds"
               f"?definitions={AUTH_RELEASE_APP_DEF}&branchName={quote(ref, safe='')}"
               f"{result_filter}&queryOrder=queueTimeDescending&$top=100&api-version=7.1")
        ok, data, detail = _pp._ado_rest_get(url, timeout)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return (False, None, f"{detail}{hint}")
        builds = (data or {}).get("value") or []
        if not builds:
            noun = "succeeded release-app build" if not require_latest else "release-app build"
            return (True, None, f"no {noun} (def {AUTH_RELEASE_APP_DEF}) on {ref}")
        valid_builds = [item for item in builds if _positive_build_id(item.get("id"))]
        if not valid_builds:
            return (False, None, f"release-app build collection on {ref} has no valid build ids")
        ordered = sorted(valid_builds, key=lambda item: int(item["id"]), reverse=True)
        if require_latest:
            b = ordered[0]
            if b.get("status") != "completed":
                return (True, None, f"latest release-app build {b.get('id')} is still "
                        f"{b.get('status') or 'in progress'} on {ref}")
            if b.get("result") != "succeeded":
                return (True, None, f"latest release-app build {b.get('id')} did not succeed "
                        f"(result: {b.get('result') or 'unknown'}) on {ref}")
        else:
            b = next((item for item in ordered
                      if item.get("status") == "completed" and item.get("result") == "succeeded"), None)
            if b is None:
                return (True, None, f"no succeeded release-app build "
                        f"(def {AUTH_RELEASE_APP_DEF}) on {ref}")
    commit = b.get("sourceVersion")
    if not commit:
        return (False, None, f"release-app build {b.get('id')} has no sourceVersion (built commit)")
    okt, tags_data, dt = _pp._ado_rest_get(
        f"{AUTH_ORG}/{AUTH_PROJECT}/_apis/build/builds/{b.get('id')}/tags?api-version=7.1", timeout)
    if not okt:
        return (False, None, f"could not read tags for build {b.get('id')} ({dt})")
    tags = (tags_data or {}).get("value") or []
    version = next((t for t in tags if _AUTH_RELEASE_VERSION.match(str(t).strip())), None)
    if not version:
        return (False, None, f"release-app build {b.get('id')} has no version tag "
                             f"(expected \\d+.\\d+.\\d+); tags: {', '.join(map(str, tags)) or 'none'}")
    return (True, {"build_id": b.get("id"), "version": str(version).strip(),
                   "commit": commit, "build_number": b.get("buildNumber")}, "")


def auth_build_url(build_id) -> str:
    """Browser URL for an Authenticator (msazure/One) build results page."""
    return f"{AUTH_ORG}/{AUTH_PROJECT}/_build/results?buildId={build_id}&view=results" if build_id else ""


def auth_branch_url(branch, commit=None) -> str:
    """Browser URL for an Authenticator branch or the exact built commit."""
    repo = coords.repo("authenticator")
    base = f"{repo['org'].rstrip('/')}/{repo['project']}/_git/{repo['name']}"
    if commit:
        return f"{base}/commit/{commit}"
    return (
        f"{base}?path=%2F&version=GB{quote(str(branch or ''), safe='')}"
        "&_a=contents"
    )


def _release_branch(value):
    branch = str(value or "").strip()
    if branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    return branch if _DATED_RELEASE.fullmatch(branch) else None


def _repo_base():
    repo = coords.repo("authenticator")
    return (
        repo,
        f"{repo['org'].rstrip('/')}/{repo['project']}/_apis/git/"
        f"repositories/{repo['name']}",
    )


def _auth_file_text(base, path, commit, timeout):
    url = base + "/items?" + urlencode({
        "path": path,
        "includeContent": "true",
        "versionDescriptor.versionType": "commit",
        "versionDescriptor.version": commit,
        "api-version": "7.1",
        "$format": "json",
    })
    ok, raw, detail = _pp._ado_rest_get_text(url, timeout)
    if not ok:
        return (False, None, detail)
    try:
        item = _json.loads(raw)
    except (TypeError, ValueError) as exc:
        return (False, None, f"invalid item response for {path}@{commit}: {exc}")
    content = item.get("content") if isinstance(item, dict) else None
    if not isinstance(content, str):
        return (False, None, f"missing text content for {path}@{commit}")
    return (True, content, "")


def parse_ecs_flights(source):
    """Exact enum name/key/default triples from EcsFlight.kt; no rollout inference."""
    flights = {}
    for match in _FLIGHT_LINE.finditer(str(source or "")):
        name, key, default = (part.strip() for part in match.groups())
        if key in flights and flights[key] != {"name": name, "key": key, "default": default}:
            raise ValueError(f"duplicate EcsFlight key with different definitions: {key}")
        flights[key] = {"name": name, "key": key, "default": default}
    if not flights:
        raise ValueError("no EcsFlight declarations parsed")
    return flights


def ecs_flight_changes(before, after):
    old, new = parse_ecs_flights(before), parse_ecs_flights(after)
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    changed = [
        {**new[key], "previous_default": old[key]["default"]}
        for key in sorted(new.keys() & old.keys())
        if old[key]["default"] != new[key]["default"]
    ]
    return {"added": added, "default_changed": changed}


def _commit_pr(comment):
    first = str(comment or "").splitlines()[0].strip()
    match = _PR_COMMIT.match(first)
    if not match:
        return (None, first or "(no commit title)")
    title = (match.group(2) or first).strip()
    return (int(match.group(1)), title)


def classify_release_commits(commits, changes_by_commit, pr_titles=None):
    """Classify exact reachable commits and use canonical PR titles when supplied."""
    repo = coords.repo("authenticator")
    web = f"{repo['org'].rstrip('/')}/{repo['project']}/_git/{repo['name']}"
    entries = {}
    omitted = []
    for commit in commits or []:
        sha = str(commit.get("commitId") or "").lower()
        if not _COMMIT.fullmatch(sha):
            raise ValueError("release commit collection contains an invalid commit id")
        paths = sorted(set(changes_by_commit.get(sha) or []))
        if not paths:
            raise ValueError(f"release commit {sha} has no complete changed-path evidence")
        pr_id, title = _commit_pr(commit.get("comment"))
        if pr_id is not None and pr_titles is not None:
            canonical = pr_titles.get(pr_id)
            if not isinstance(canonical, str) or not canonical.strip():
                raise ValueError(f"release PR {pr_id} has no canonical title")
            title = canonical.strip()
        generated = (
            any(title.startswith(prefix) for prefix in _GENERATED_TITLES)
            or all(any(path.startswith(prefix) for prefix in _GENERATED_PATHS) for path in paths)
        )
        key = f"pr:{pr_id}" if pr_id else f"commit:{sha}"
        url = f"{web}/pullrequest/{pr_id}" if pr_id else f"{web}/commit/{sha}"
        did = any(any(path == prefix or path.startswith(prefix) for prefix in _DID_PATHS)
                  for path in paths)
        general = any(
            not any(path.startswith(prefix) for prefix in _GENERATED_PATHS)
            and not any(path == prefix or path.startswith(prefix) for prefix in _DID_PATHS)
            for path in paths
        )
        if generated:
            omitted.append({"commit": sha, "id": pr_id, "title": title})
            continue
        existing = entries.get(key)
        if existing:
            paths = sorted(set(existing["paths"]) | set(paths))
            did = did or "DID" in existing["components"]
            general = general or "Authenticator" in existing["components"]
        components = [
            component for component, present in (("Authenticator", general), ("DID", did))
            if present
        ]
        entries[key] = {
            "id": pr_id,
            "commit": sha,
            "title": title,
            "url": url,
            "date": ((commit.get("author") or {}).get("date")
                     or (commit.get("committer") or {}).get("date")),
            "paths": paths,
            "components": components or ["Authenticator"],
            "mixed": general and did,
        }
    ordered = sorted(
        entries.values(),
        key=lambda item: (item.get("date") or "", item.get("id") or 0, item["commit"]),
        reverse=True,
    )
    return {
        "general": [
            item for item in ordered
            if "Authenticator" in item["components"] and "DID" not in item["components"]
        ],
        "did": [item for item in ordered if "DID" in item["components"]],
        "generated_omitted": omitted,
    }


def _pull_request_titles(base, commits, timeout):
    pr_ids = sorted({
        pr_id
        for commit in commits
        for pr_id, _ in [_commit_pr(commit.get("comment"))]
        if pr_id is not None
    })
    titles = {}
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = {
            pr_id: workers.submit(
                _pp._ado_rest_get,
                f"{base}/pullRequests/{pr_id}?api-version=7.1",
                timeout,
            )
            for pr_id in pr_ids
        }
        for pr_id, future in futures.items():
            ok, data, detail = future.result()
            title = (data or {}).get("title") if isinstance(data, dict) else None
            if not ok or not isinstance(title, str) or not title.strip():
                return (False, None, f"could not read canonical title for PR {pr_id} "
                        f"({detail or 'missing title'})")
            titles[pr_id] = title.strip()
    return (True, titles, "")


def _commit_changes(base, commit, timeout):
    items, token, seen = [], None, set()
    for _ in range(60):
        url = f"{base}/commits/{commit}/changes?$top=2000&api-version=7.1"
        if token:
            url += f"&continuationToken={quote(str(token), safe='')}"
        ok, data, headers, detail = _pp._ado_rest_get_h(url, timeout)
        if not ok:
            return (False, None, detail)
        page = (data or {}).get("changes")
        if not isinstance(page, list):
            return (False, None, "malformed Authenticator commit changes page")
        items.extend(page)
        token = headers.get("x-ms-continuationtoken")
        if not token:
            paths = []
            for change in items:
                item = change.get("item") or {}
                if not item.get("isFolder"):
                    paths.append(item.get("path"))
                if "rename" in str(change.get("changeType") or "").lower():
                    paths.extend((
                        change.get("originalPath"),
                        change.get("sourceServerItem"),
                        item.get("originalPath"),
                    ))
            paths = [path for path in paths if path is not None]
            if any(not isinstance(path, str) or not path.startswith("/") for path in paths):
                return (False, None, "invalid Authenticator changed path")
            return (True, sorted(set(paths)), "")
        if token in seen:
            return (False, None, "repeated Authenticator changes continuation token")
        seen.add(token)
    return (False, None, "Authenticator commit changes exceeded page limit")


def release_change_manifest(release_branch, target_commit, timeout=90):
    """Exact source manifest for Phase-5 Authenticator/DID release communication."""
    branch = _release_branch(release_branch)
    if not branch:
        return (False, None, f"not a dated Authenticator release branch: {release_branch!r}")
    target = str(target_commit or "").lower()
    if not _COMMIT.fullmatch(target):
        return (False, None, "final Authenticator build has no valid 40-character commit")
    repo, base = _repo_base()
    refs_url = f"{base}/refs?filter=heads/release/20&$top=100&api-version=7.1"
    ok, refs, detail = _pp._ado_rest_get_all(refs_url, timeout)
    if not ok:
        return (False, None, f"could not list Authenticator release refs ({detail})")
    dated = []
    for ref in refs:
        name = str(ref.get("name") or "")
        short = name[len("refs/heads/"):] if name.startswith("refs/heads/") else name
        if _DATED_RELEASE.fullmatch(short):
            dated.append((short, ref.get("objectId")))
    earlier = sorted((name, sha) for name, sha in dated if name < branch)
    if not earlier:
        return (False, None, f"no previous dated release branch before {branch}")
    previous_branch = previous_build = None
    build_detail = ""
    for candidate, _ in reversed(earlier):
        ok_build, info, build_detail = find_auth_release_build(
            candidate, timeout, require_latest=False)
        if not ok_build:
            return (False, None, f"could not resolve previous Authenticator build ({build_detail})")
        if info:
            previous_branch, previous_build = candidate, info
            break
    if not previous_build:
        return (False, None, f"no previous successful Authenticator release build ({build_detail})")
    baseline = str(previous_build.get("commit") or "").lower()
    if not _COMMIT.fullmatch(baseline):
        return (False, None, "previous Authenticator release build has no valid commit")
    diff_url = (
        f"{base}/diffs/commits?baseVersion={baseline}&baseVersionType=commit"
        f"&targetVersion={target}&targetVersionType=commit&$top=2000"
        f"&api-version=7.1-preview.1"
    )
    ok_diff, diff, diff_detail = _pp._ado_rest_get(diff_url, timeout)
    if not ok_diff:
        return (False, None, f"could not compare Authenticator release commits ({diff_detail})")
    if (not isinstance(diff, dict) or diff.get("allChangesIncluded") is not True
            or not _COMMIT.fullmatch(str(diff.get("commonCommit") or ""))):
        return (False, None, "Authenticator release diff is incomplete or has no merge-base")
    common = diff["commonCommit"]
    ahead = diff.get("aheadCount")
    if type(ahead) is not int or ahead < 0:
        return (False, None, "Authenticator release diff has invalid aheadCount")
    commits_url = (
        f"{base}/commits?searchCriteria.itemVersion.version={common}"
        f"&searchCriteria.itemVersion.versionType=commit"
        f"&searchCriteria.compareVersion.version={target}"
        f"&searchCriteria.compareVersion.versionType=commit&$top=200&api-version=7.1"
    )
    ok_commits, commits, commit_detail = _pp._ado_rest_get_all(commits_url, timeout)
    if not ok_commits:
        return (False, None, f"could not enumerate reachable Authenticator commits ({commit_detail})")
    if len(commits) != ahead:
        return (False, None, f"reachable Authenticator commit count mismatch ({len(commits)} != {ahead})")
    changes = {}
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = {
            str(commit.get("commitId") or "").lower():
            workers.submit(_commit_changes, base, commit.get("commitId"), timeout)
            for commit in commits
        }
        for sha, future in futures.items():
            ok_changes, paths, changed_detail = future.result()
            if not ok_changes:
                return (False, None, f"could not read changes for {sha} ({changed_detail})")
            changes[sha] = paths
    ok_titles, pr_titles, title_detail = _pull_request_titles(base, commits, timeout)
    if not ok_titles:
        return (False, None, title_detail)
    try:
        classified = classify_release_commits(commits, changes, pr_titles)
    except ValueError as exc:
        return (False, None, str(exc))
    ok_old, old_flights, old_detail = _auth_file_text(
        base, _ECS_FLIGHT_PATH, baseline, timeout)
    ok_new, new_flights, new_detail = _auth_file_text(
        base, _ECS_FLIGHT_PATH, target, timeout)
    if not ok_old or not ok_new:
        return (False, None, "could not read EcsFlight.kt at exact release commits "
                f"({old_detail or new_detail})")
    try:
        flight_changes = ecs_flight_changes(old_flights, new_flights)
    except ValueError as exc:
        return (False, None, f"could not compare EcsFlight.kt ({exc})")
    return (True, {
        "version": 1,
        "branch": branch,
        "target_commit": target,
        "baseline_branch": previous_branch,
        "baseline_commit": baseline,
        "merge_base": common,
        "reachable_commit_count": len(commits),
        **classified,
        "flight_changes": flight_changes,
    }, "")


def merged_release_prs(release_branch, timeout=90):
    """Derive the release PAYLOAD PR list for the Authenticator app — the merged PRs that make
    up this month's release. Returns (ok, prs, detail) where `prs` is an ordered, de-duplicated
    list of {id, title} newest-first.

    The auth branch model: feature work lands on the `working` mainline during the cycle, and the
    release branch (`release/YYYY/MM/DD`) additionally carries the RC/final version-bump + cherry-
    pick PRs. So the payload = completed PRs into `working` within the cycle window (bounded by the
    PREVIOUS dated release branch and this one), PLUS completed PRs into the release branch itself.
    Deterministic, read-only; the skill previews the list before it writes the wiki page."""
    import re as _re2
    from urllib.parse import quote
    from tools.coordinates import coords
    rel = str(release_branch or "").strip()
    if rel.startswith("refs/heads/"):
        rel = rel[len("refs/heads/"):]
    m = _re2.match(r"release/(\d{4})/(\d{2})/(\d{2})$", rel)
    if not m:
        return (False, None, f"not a dated auth release branch: {release_branch!r}")
    cur_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    r = coords.repo("authenticator")
    base = f"{r['org']}/{r['project']}/_apis/git/repositories/{r['name']}"

    # Lower bound = the PREVIOUS dated release branch (release/YYYY/MM/DD). Fall back to
    # ~35 days before this release when no earlier branch is found.
    ok, data, det = _pp._ado_rest_get(f"{base}/refs?filter=heads/release/20&api-version=7.1", timeout)
    dated = []
    for ref in ((data or {}).get("value") or []):
        rm = _re2.match(r"refs/heads/release/(\d{4})/(\d{2})/(\d{2})$", ref.get("name") or "")
        if rm:
            dated.append(f"{rm.group(1)}-{rm.group(2)}-{rm.group(3)}")
    earlier = sorted(d for d in dated if d < cur_date)
    if earlier:
        min_time = earlier[-1] + "T00:00:00Z"
    else:
        from datetime import datetime, timedelta
        min_time = (datetime.strptime(cur_date, "%Y-%m-%d") - timedelta(days=35)).strftime("%Y-%m-%dT00:00:00Z")
    # Upper bound = a week past the release-branch date (captures late RC/final bumps).
    from datetime import datetime as _dt, timedelta as _td
    max_time = (_dt.strptime(cur_date, "%Y-%m-%d") + _td(days=7)).strftime("%Y-%m-%dT00:00:00Z")

    def _completed(target_ref, windowed):
        u = (f"{base}/pullrequests?searchCriteria.status=completed"
             f"&searchCriteria.targetRefName={quote('refs/heads/' + target_ref, safe='')}"
             f"&$top=200&api-version=7.1")
        if windowed:
            u += (f"&searchCriteria.queryTimeRangeType=closed"
                  f"&searchCriteria.minTime={min_time}&searchCriteria.maxTime={max_time}")
        ok2, d2, det2 = _pp._ado_rest_get(u, timeout)
        return ((d2 or {}).get("value") or []) if ok2 else []

    rows = _completed("working", windowed=True) + _completed(rel, windowed=False)
    if not rows:
        return (False, None, f"no completed PRs found for the {rel} cycle (det: {det})")
    seen, out = set(), []
    # Order newest-first by closedDate (release-branch bumps interleave naturally).
    for p in sorted(rows, key=lambda p: (p.get("closedDate") or ""), reverse=True):
        pid = p.get("pullRequestId")
        if pid in seen:
            continue
        seen.add(pid)
        out.append({"id": pid, "title": (p.get("title") or "").strip()})
    return (True, out, "")


def create_lightweight_tag(org, project, repo, tag_name, commit, timeout=60):
    """Create a LIGHTWEIGHT git tag `tag_name` pointing at `commit` in an ADO git repo.
    Returns (ok, info, detail) where info is {created: bool, objectId: <commit the tag points at>}.

    Idempotent: if the tag already exists it is NOT recreated — `created` is False and objectId
    is the existing target (the caller decides whether that matches the intended commit). `repo`
    may be the repository name or id."""
    base = f"{org}/{project}/_apis/git/repositories/{repo}"
    ref = f"refs/tags/{tag_name}"
    okx, ex, _dx = _pp._ado_rest_get(f"{base}/refs?filter=tags/{tag_name}&api-version=7.1", timeout)
    if okx:
        for r in ((ex or {}).get("value") or []):
            if r.get("name") == ref:                 # exact match (filter is a prefix)
                return (True, {"created": False, "objectId": r.get("objectId")}, "")
    ok, res, d = _pp._ado_rest_send(f"{base}/refs?api-version=7.1", "POST",
                                [{"name": ref, "oldObjectId": _ZERO_SHA, "newObjectId": commit}],
                                timeout)
    entry = ((res or {}).get("value") or [{}])[0] if isinstance(res, dict) else {}
    if ok and entry.get("success"):
        return (True, {"created": True, "objectId": commit}, "")
    why = entry.get("customMessage") or d or "tag ref create rejected"
    return (False, None, why)

__all__ = ['AUTH_BUILD_DEF', 'AUTH_ORG', 'AUTH_PROJECT', 'AUTH_RELEASE_APP_DEF', 'AUTH_SIGNOFF_DEF', 'AUTH_SIGNOFF_STAGE_NAME', 'AUTH_TEST_DEF', 'AUTH_UI_PASS_THRESHOLD', 'AUTH_UI_SUITES', '_AUTH_RC_VERSION', '_AUTH_RELEASE_VERSION', '_ZERO_SHA', '_auth_build_ref', '_auth_test_source_build_id', '_release_ref', 'auth_branch_url', 'auth_build_url', 'auth_ui_suite_rates', 'classify_release_commits', 'create_lightweight_tag', 'ecs_flight_changes', 'find_auth_ecs_build', 'find_auth_release_build', 'find_final_auth_build', 'find_auth_signoff_run', 'find_auth_ui_test_build', 'merged_release_prs', 'parse_ecs_flights', 'read_auth_signoff_run', 'release_change_manifest', 'signoff_stage_failed', 'signoff_stage_running', 'signoff_stage_started', 'signoff_stage_succeeded', 'start_auth_signoff_stage']
