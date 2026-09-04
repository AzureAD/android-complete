"""Authenticator app (msazure/One) build+UI discovery, release tagging, payload PRs."""
from __future__ import annotations

from tools.coordinates import coords
from tools import pipelines as _pp
import re as _re_mod


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
# The final Auth App version tag format on the release-app build, e.g. '6.2608.5658'.
_AUTH_RELEASE_VERSION = _re_mod.compile(r"^\d+\.\d+\.\d+$")
_ZERO_SHA = "0" * 40                                 # ADO "create ref" sentinel (no old object)
# The two Firebase device suites the auth leg is gated on (both must clear the threshold).
AUTH_UI_SUITES = tuple(coords.gate("auth_ui_suites"))
AUTH_UI_PASS_THRESHOLD = coords.gate("auth_ui_pass_pct")

# adAccountsVersion encodes the RC iteration + flight flavor, e.g. '0.0.02468-rc-RC1-ecs'
# (ECS) or '0.0.02468-rc-RC1-local-flights' (Local). This is the deterministic key that
# says which RC/flavor an auth build is — no branch/date parsing needed.
_AUTH_RC_VERSION = _re_mod.compile(r"-rc-RC(\d+)-(ecs|local-flights)$", _re_mod.I)


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


def find_auth_ecs_build(auth_branch, timeout=90):
    """Discover the CURRENT-RC Authenticator ECS build (def 475778) on the release's auth
    working-branch. Returns (ok, info, detail) where info is
      {build_id, rc, version, status, result}  (or None when no ECS build exists yet).

    Deterministic selection: among builds on `refs/heads/working-<auth_branch>` whose
    adAccountsVersion matches '-rc-RC<N>-ecs', take the HIGHEST N (the current RC iteration,
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
            {"id": b.get("id"), "version": ver, "status": b.get("status"), "result": b.get("result")})
    if not by_rc:
        return (True, None, f"no ECS release-candidate auth build found on {ref}")
    n = max(by_rc)                               # highest RC iteration = current
    newest = max(by_rc[n], key=lambda x: x.get("id") or 0)
    return (True, {"build_id": newest["id"], "rc": n, "version": newest["version"],
                   "status": newest["status"], "result": newest["result"]}, "")


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
    executed result). Reuses get_test_summary's per-run breakdown (single Test-Runs read)."""
    ok, summ, detail = _pp.get_test_summary(AUTH_ORG, AUTH_PROJECT, test_build_id, timeout)
    if not ok:
        return (False, None, detail)
    by_name = {r.get("name"): r for r in (summ or {}).get("runs", [])}
    out = {}
    for name in AUTH_UI_SUITES:
        r = by_name.get(name)
        if not r:
            out[name] = {"present": False, "passed": 0, "failed": 0, "total": 0, "pct": None}
            continue
        passed, failed = r.get("passed") or 0, r.get("failed") or 0
        denom = passed + failed
        out[name] = {"present": True, "passed": passed, "failed": failed,
                     "total": r.get("total") or 0,
                     "pct": (round(passed * 100.0 / denom, 1) if denom else None)}
    return (True, out, "")


def auth_ui_case_results(build_id, timeout=120):
    """Per-CASE automated outcome AND a representative test title from the auth ECS post-build
    UI-test build. Returns (ok, {case_id: {"outcome": 'Passed'|'Failed', "title": str|None}},
    detail).

    Same scan/aggregation as `auth_ui_case_outcomes` (this is its superset): pulls each
    result's test-CASE id from its automated name (`test_<caseId>_...`), aggregates per case
    ('Passed' if it passed in >=1 run, else 'Failed' if it ran but never passed; skip-only
    cases omitted), and additionally keeps the automation test title per case for display."""
    base = AUTH_ORG.rstrip("/")
    url = (f"{base}/{AUTH_PROJECT}/_apis/test/runs"
           f"?buildUri=vstfs:///Build/Build/{build_id}&api-version=7.1")
    ok, data, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        return (False, None, detail)
    runs = (data or {}).get("value", []) or []
    passed_any, ran_any, titles = set(), set(), {}
    for run in runs:
        okr, results, dr = _pp._run_results(AUTH_ORG, AUTH_PROJECT, run.get("id"), timeout)
        if not okr:
            return (False, None, dr)
        for res in results:
            cid = _pp._ui_case_id_from_result(res)
            if cid is None:
                continue
            if (res.get("outcome") or "") == "NotApplicable":
                continue
            ran_any.add(cid)
            t = (res.get("testCaseTitle") or res.get("automatedTestName") or "").strip()
            if t and cid not in titles:
                titles[cid] = t
            if res.get("outcome") == "Passed":
                passed_any.add(cid)
    out = {cid: {"outcome": ("Passed" if cid in passed_any else "Failed"),
                 "title": titles.get(cid)} for cid in ran_any}
    return (True, out, "")


def auth_ui_case_outcomes(build_id, timeout=120):
    """Per-CASE automated outcomes from the auth ECS post-build UI-test BUILD — the join
    key Phase 3 needs. Returns (ok, {case_id: 'Passed'|'Failed'}, detail). Thin projection of
    `auth_ui_case_results` (see it for the scan/aggregation rules). The KEYS are exactly the
    release's automated auth case ids (what distribute_tests excludes + ui_test_status fills)."""
    ok, res, detail = _pp.auth_ui_case_results(build_id, timeout)
    if not ok:
        return (False, None, detail)
    return (True, {cid: v["outcome"] for cid, v in res.items()}, "")


# ---------------------------------------------------------------- Auth release tag (Phase 4)
def _release_ref(release_branch):
    """Full ref for the Auth App RELEASE branch. state.versions.authenticator is stored as
    'release/YYYY/MM/DD' (the release branch — the working branch is 'working-release/…')."""
    if not release_branch:
        return None
    b = str(release_branch).strip()
    return b if b.startswith("refs/heads/") else f"refs/heads/{b}"


def find_auth_release_build(release_branch, timeout=90):
    """Discover the Auth App release build (def AUTH_RELEASE_APP_DEF = AndroidBuild-1ES) on the
    release branch and read the version it produced. Returns (ok, info, detail) where info is
      {build_id, version, commit}   (or None when no succeeded build exists on the branch yet).

    The release-app build carries the final Auth App version as an ADO build TAG matching
    _AUTH_RELEASE_VERSION (e.g. '6.2608.5658'); `commit` is the exact commit it was built from
    (build.sourceVersion) — the commit Phase-4 `tag_authenticator` tags with that version."""
    from urllib.parse import quote
    ref = _pp._release_ref(release_branch)
    if not ref:
        return (False, None, "no authenticator release branch known (run orchestrator_health first)")
    url = (f"{AUTH_ORG}/{AUTH_PROJECT}/_apis/build/builds"
           f"?definitions={AUTH_RELEASE_APP_DEF}&branchName={quote(ref, safe='')}"
           f"&resultFilter=succeeded&queryOrder=finishTimeDescending&$top=20&api-version=7.1")
    ok, data, detail = _pp._ado_rest_get(url, timeout)
    if not ok:
        hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
        return (False, None, f"{detail}{hint}")
    builds = (data or {}).get("value") or []
    if not builds:
        return (True, None, f"no succeeded release-app build (def {AUTH_RELEASE_APP_DEF}) on {ref}")
    b = builds[0]                                    # newest succeeded
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

__all__ = ['AUTH_BUILD_DEF', 'AUTH_ORG', 'AUTH_PROJECT', 'AUTH_RELEASE_APP_DEF', 'AUTH_TEST_DEF', 'AUTH_UI_PASS_THRESHOLD', 'AUTH_UI_SUITES', '_AUTH_RC_VERSION', '_AUTH_RELEASE_VERSION', '_ZERO_SHA', '_auth_build_ref', '_auth_test_source_build_id', '_release_ref', 'auth_build_url', 'auth_ui_case_outcomes', 'auth_ui_case_results', 'auth_ui_suite_rates', 'create_lightweight_tag', 'find_auth_ecs_build', 'find_auth_release_build', 'find_auth_ui_test_build', 'merged_release_prs']
