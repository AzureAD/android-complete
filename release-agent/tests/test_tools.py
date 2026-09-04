"""Release-agent tests — tools. Shared harness in tests/_harness.py."""
from tests._harness import *  # noqa: F401,F403




def test_reconcile_retries_pure():
    """reconcile_retries collapses per-attempt results by title: passed if any attempt
    passed; recovered if it also failed; failed only if it never passed; NA ignored."""
    from tools import pipelines as P
    res = [
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Failed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},   # flaky → recovered
        {"testCaseTitle": "testAlwaysGreen", "outcome": "Passed"},
        {"testCaseTitle": "testHardFail", "outcome": "Failed"},
        {"testCaseTitle": "testHardFail", "outcome": "Failed"},          # failed every attempt
        {"testCaseTitle": "testSkipped", "outcome": "NotExecuted"},      # ignored
    ]
    r = P.reconcile_retries(res)
    assert r["passed"] == 2 and r["failed"] == 1
    assert r["recovered"] == ["testNullDrsMetadata"]
    assert r["total"] == 3 and r["na"] == 1
    assert P.reconcile_retries([]) == {"passed": 0, "failed": 0, "recovered": [], "total": 0, "na": 0}




def test_get_test_summary_unit_retry_reconciles():
    """A UNIT run whose only failure is a flaky test that passed on retry reconciles to
    0 failed + a `recovered` warning — even though ADO's run aggregate said 1 failed.
    (The rule is unit-only; UI/instrumented use the aggregate unchanged.)"""
    from tools import pipelines as P
    runs = {"value": [{"id": 700, "name": "broker4j_UnitTests",
                       "totalTests": 5, "passedTests": 4, "notApplicableTests": 0}]}
    results = {"value": [
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Failed"},
        {"testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"testCaseTitle": "t2", "outcome": "Passed"},
        {"testCaseTitle": "t3", "outcome": "Passed"},
        {"testCaseTitle": "t4", "outcome": "Passed"},
    ]}
    orig = P._ado_rest_get
    P._ado_rest_get = lambda url, timeout: (True, runs if "buildUri" in url else results, "")
    try:
        ok, s, _ = P.get_test_summary("O", "P", 700)
        assert ok
        unit = s["categories"]["unit"]
        assert unit["failed"] == 0 and unit["recovered"] == ["testNullDrsMetadata"]
        assert unit["passed"] == 4 and unit["total"] == 4
    finally:
        P._ado_rest_get = orig




def test_classify_test_run_categories():
    """The test-run classifier buckets into exactly three: unit / instrumented / ui;
    anything that isn't unit/instrumented is UI ('the rest are UI', incl. Lab Api Tests)."""
    from tools import pipelines as P
    assert P.classify_test_run("common4j_UnitTests") == "unit"
    assert P.classify_test_run("common_InstrumentedTests") == "instrumented"
    assert P.classify_test_run("PROD MSAL - RC Broker (API 32)") == "ui"
    assert P.classify_test_run("RC MSAL - PROD Broker (API 28) # 123_build.1") == "ui"
    assert P.classify_test_run("Lab Api Tests") == "ui"             # NOT 'other'
    assert P.classify_test_run("") == "ui"




def test_testplans_names_and_query():
    from tools import testplans as T
    assert T.broker_plan_name("2026-08") == "Android Monthly Release - Aug 2026"
    # suite name comes from the CCD date: 'Android release/MM/DD/YYYY' (matches prod)
    assert T.auth_suite_name("2026-08-13") == "Android release/08/13/2026"
    q = T.auth_bugbash_query()
    assert "contains 'Android'" in q and "contains 'ReleaseBugBash'" in q and "Identity Apps" in q




# ---- Phase 3: distribute_tests ----

def test_distribution_even_and_preserves_preference():
    """distribute() lands everyone within ±1 of the target, keeps default assignees where
    possible, and gives the +1 slots to the people with the most eligible defaults."""
    from tools import distribution as D
    elig = ["alice", "bob", "carmine", "dave"]
    # 14 tests: alice-heavy defaults + some on an INELIGIBLE 'owner' (pooled)
    tests = ([{"id": f"a{i}", "assignee": "alice"} for i in range(8)] +
             [{"id": f"b{i}", "assignee": "bob"} for i in range(2)] +
             [{"id": f"o{i}", "assignee": "owner"} for i in range(4)])   # owner not eligible
    r = D.distribute(tests, elig)
    assert sum(r["counts"].values()) == 14
    assert max(r["counts"].values()) - min(r["counts"].values()) <= 1   # even (±1)
    assert r["counts"]["alice"] == 4                                    # 14/4 -> 3 or 4
    # alice keeps 4 of her 8 defaults; bob keeps his 2
    assert r["assignments"]["b0"] == "bob" and r["assignments"]["b1"] == "bob"
    assert r["kept"] >= 6
    # every assignment is an eligible tester (owner's 4 got reassigned)
    assert set(r["assignments"].values()) <= set(elig)




def test_maven_pom_url_shape():
    """The .pom URL matches Maven Central's layout for each artifact."""
    from tools import maven as M
    assert M.pom_url("common", "24.6.0") == \
        "https://repo1.maven.org/maven2/com/microsoft/identity/common/24.6.0/common-24.6.0.pom"
    assert M.pom_url("msal", "8.4.2") == \
        "https://repo1.maven.org/maven2/com/microsoft/identity/client/msal/8.4.2/msal-8.4.2.pom"
    assert M.pom_url("common4j", "24.6.0").endswith("/common4j/24.6.0/common4j-24.6.0.pom")




def test_find_auth_release_build_extracts_version_and_commit(monkeypatch):
    """find_auth_release_build takes the newest succeeded release-app build, reads its
    sourceVersion (commit) and its numeric build-tag (version)."""
    from tools import pipelines as P

    def fake_get(url, t):
        if "/builds/177976153/tags" in url:
            return (True, {"value": ["1ES.PT.Official", "6.2608.5658", "1ES.PT.Build"]}, "")
        if "_apis/build/builds?" in url:
            return (True, {"value": [{"id": 177976153, "sourceVersion": _TA_COMMIT}]}, "")
        return (False, None, "unexpected url")
    monkeypatch.setattr(P, "_ado_rest_get", fake_get)
    ok, info, _ = P.find_auth_release_build("release/2026/08/13")
    assert ok and info == {"build_id": 177976153, "version": "6.2608.5658",
                           "commit": _TA_COMMIT, "build_number": None}




def test_find_auth_release_build_none_when_no_build(monkeypatch):
    """No succeeded release-app build on the branch → (True, None, detail) so the step can block gently."""
    from tools import pipelines as P
    monkeypatch.setattr(P, "_ado_rest_get", lambda url, t: (True, {"value": []}, ""))
    ok, info, detail = P.find_auth_release_build("release/2026/08/13")
    assert ok and info is None and "no succeeded release-app build" in detail




def test_merged_release_prs_merges_working_and_release_dedupes():
    """merged_release_prs windows completed `working` PRs by the previous/current release
    branch dates, adds the release-branch bump PRs, and de-dupes newest-first."""
    from tools import pipelines as P
    calls = {}

    def fake_get(url, timeout=90):
        if "filter=heads/release/20" in url:
            return (True, {"value": [{"name": "refs/heads/release/2026/07/10"},
                                     {"name": "refs/heads/release/2026/08/13"}]}, "")
        if "pullrequests" in url and "working" in url:
            calls["working"] = url
            return (True, {"value": [
                {"pullRequestId": 100, "title": "Feature A", "closedDate": "2026-08-01T00:00:00Z"},
                {"pullRequestId": 101, "title": "Feature B", "closedDate": "2026-08-05T00:00:00Z"}]}, "")
        if "pullrequests" in url:
            calls["release"] = url
            return (True, {"value": [
                {"pullRequestId": 101, "title": "Feature B (dup)", "closedDate": "2026-08-05T00:00:00Z"},
                {"pullRequestId": 200, "title": "RC bump", "closedDate": "2026-08-12T00:00:00Z"}]}, "")
        return (False, None, "unexpected")

    of = P._ado_rest_get
    P._ado_rest_get = fake_get
    try:
        ok, prs, det = P.merged_release_prs("release/2026/08/13")
    finally:
        P._ado_rest_get = of
    assert ok, det
    ids = [p["id"] for p in prs]
    assert ids == [200, 101, 100]                      # newest-first, 101 de-duped
    # the working window used the previous release branch date as the lower bound
    assert "2026-07-10T00:00:00Z" in calls["working"]

