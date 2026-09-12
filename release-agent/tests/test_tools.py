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
    The same policy also applies to UI/instrumented suites."""
    from tools import pipelines as P
    runs = {"value": [{"id": 700, "name": "broker4j_UnitTests",
                       "totalTests": 6, "passedTests": 5, "notApplicableTests": 0}]}
    results = {"value": [
        {"id": 1, "testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"id": 2, "testCaseTitle": "testNullDrsMetadata", "outcome": "Failed"},
        {"id": 3, "testCaseTitle": "testNullDrsMetadata", "outcome": "Passed"},
        {"id": 4, "testCaseTitle": "t2", "outcome": "Passed"},
        {"id": 5, "testCaseTitle": "t3", "outcome": "Passed"},
        {"id": 6, "testCaseTitle": "t4", "outcome": "Passed"},
    ]}
    orig = P._ado_rest_get
    P._ado_rest_get = lambda url, timeout: (True, runs if "buildUri" in url else results, "")
    try:
        ok, s, _ = P.get_test_summary("O", "P", 700)
        assert ok
        unit = s["categories"]["unit"]
        assert unit["failed"] == 0 and unit["recovered"] == [
            {"suite": "broker4j_UnitTests", "title": "testNullDrsMetadata"}]
        assert unit["passed"] == 4 and unit["total"] == 4
    finally:
        P._ado_rest_get = orig


def _paged_test_api(monkeypatch, runs, results):
    from urllib.parse import parse_qs, urlparse
    from tools import pipelines as P
    calls = []

    def get(url, timeout):
        calls.append(url)
        q = parse_qs(urlparse(url).query)
        skip, top = int(q.get("$skip", [0])[0]), int(q.get("$top", [1000])[0])
        if "buildUri" in q:
            rows = runs
        else:
            rid = int(url.split("/Runs/")[1].split("/")[0])
            rows = results[rid]
            if "outcomes" in q:
                rows = [r for r in rows if r["outcome"] == q["outcomes"][0]]
        return True, {"value": rows[skip:skip + top]}, ""

    monkeypatch.setattr(P, "_ado_rest_get", get)
    return calls


def test_get_failed_tests_complete_pass_any_evidence(monkeypatch):
    from tools import pipelines as P
    from steps.build_verify import rc_report
    titles = [f"test_7_parameter[{i:04}]" for i in range(1250)]
    first = [{"id": i, "testCaseTitle": t, "outcome": "Failed"}
             for i, t in enumerate(titles + titles[:1] * 2, 1)]
    first.append({"id": len(first) + 1, "testCaseTitle": titles[0], "outcome": "Passed"})
    results = {1: first}
    for rid in range(2, 22):
        results[rid] = [{"id": 1, "testCaseTitle": titles[0], "outcome": "Failed"}]
    results[22] = [{"id": i, "testCaseTitle": t, "outcome": "Passed"}
                   for i, t in enumerate(titles[:1], 1)]
    runs = [{"id": rid, "name": f"PROD MSAL - RC Broker (API 32) # attempt{rid}",
             "totalTests": len(rows),
             "passedTests": sum(r["outcome"] == "Passed" for r in rows)}
            for rid, rows in results.items()]
    calls = _paged_test_api(monkeypatch, runs, results)
    ok, suites, detail = P.get_failed_tests("O", "P", 1)
    assert ok and not detail and len(suites) == 1
    suite = suites[0]
    assert suite["failed"] == 1249 and suite["total"] == 1250
    assert suite["tests"] == titles[1:] and suite["recovered"] == titles[:1]
    assert suite["test_results"][0]["outcome_counts"] == {"Failed": 23, "Passed": 2}
    assert len(suite["test_results"][0]["attempts"]) == 25
    assert suite["result_entries"] == 1274
    assert suite["run_ids"] == list(range(1, 23))
    assert suite["count_basis"] == P.MRWP_COUNT_BASIS
    assert any("/Runs/1/results" in url and "$skip=1000" in url for url in calls)
    assert any("/Runs/21/results" in url for url in calls)
    assert any("/Runs/22/results" in url for url in calls)  # clean rerun must be read too
    assert not any("outcomes=" in url for url in calls)
    ok, summary, _ = P.get_test_summary("O", "P", 1)
    assert ok and summary["categories"]["ui"]["total"] == 1250
    assert summary["categories"]["ui"]["passed"] == 1
    assert summary["failed_suites"] == suites
    model = {"mrwp": {p: {"tests": summary, "failed_suites": suites} for p in ("ECS", "Local")}}
    gate = rc_report.rc_ui_gate(model)
    assert gate["verdict"] == "attention" and gate["ui_failed"] == 2498
    assert gate["ui_total"] == 2500 and gate["pass_pct"] == 0.1
    model["mrwp"]["ECS"]["failed_suites"] = []
    assert rc_report.rc_ui_gate(model)["pass_pct"] == gate["pass_pct"]


def test_get_failed_tests_and_summary_page_all_runs(monkeypatch):
    from tools import pipelines as P
    runs = [{"id": i, "name": f"UI # {i}", "totalTests": 1, "passedTests": 1}
            for i in range(1, 1002)]
    runs[-1]["passedTests"] = 0
    rows = {i: [{"id": 1, "testCaseTitle": f"test_{i}", "outcome": "Passed"}]
            for i in range(1, 1001)}
    rows[1001] = [{"id": 1, "testCaseTitle": "last_run_failure", "outcome": "Failed"}]
    calls = _paged_test_api(monkeypatch, runs, rows)
    ok, suites, _ = P.get_failed_tests("O", "P", 1)
    assert ok and suites[0]["tests"] == ["last_run_failure"]
    assert suites[0]["total"] == 1001 and suites[0]["failed"] == 1
    assert len(suites[0]["run_ids"]) == 1001
    ok, summary, _ = P.get_test_summary("O", "P", 1)
    assert ok and summary["total"] == 1001 and summary["passed"] == 1000
    assert any("buildUri" in url and "$skip=1000" in url for url in calls)


def test_get_failed_tests_all_categories_apply_same_retry_rule(monkeypatch):
    from tools import pipelines as P
    titles = [f"unit_parameter[{i:03}]" for i in range(45)]
    rows = [{"id": i, "testCaseTitle": t, "outcome": "Failed"} for i, t in enumerate(titles, 1)]
    rows += [{"id": 46, "testCaseTitle": "recovered", "outcome": "Failed"},
             {"id": 47, "testCaseTitle": "recovered", "outcome": "Passed"}]
    runs = [{"id": 1, "name": "sdk_UnitTests # 1", "totalTests": 47, "passedTests": 1},
            {"id": 2, "name": "sdk_UnitTests # 2", "totalTests": 1, "passedTests": 1},
            {"id": 3, "name": "sdk_InstrumentedTests", "totalTests": 2, "passedTests": 1}]
    _paged_test_api(monkeypatch, runs, {1: rows, 2: [
        {"id": 1, "testCaseTitle": titles[0], "outcome": "Passed"}], 3: [
        {"id": 1, "testCaseTitle": "instrumented", "outcome": "Failed"},
        {"id": 2, "testCaseTitle": "instrumented", "outcome": "Passed"}]})
    ok, summary, _ = P.get_test_summary("O", "P", 1)
    assert ok
    unit = next(s for s in summary["suites"] if s["category"] == "unit")
    instrumented = next(s for s in summary["suites"] if s["category"] == "instrumented")
    assert unit["tests"] == titles[1:] and unit["recovered"] == ["recovered", titles[0]]
    assert unit["failed"] == 44 and unit["total"] == 46
    assert unit["count_basis"] == P.MRWP_COUNT_BASIS
    assert instrumented["tests"] == [] and instrumented["failed"] == 0
    assert instrumented["total"] == 1 and instrumented["recovered"] == ["instrumented"]
    assert summary["failed_suites"] == [unit]


def test_run_results_uncapped_or_explicit_limit_error(monkeypatch):
    from tools import pipelines as P
    rows = [{"id": i, "testCaseTitle": f"failure_{i}", "outcome": "Failed"}
            for i in range(1, 10002)]
    _paged_test_api(monkeypatch, [], {1: rows})
    ok, results, detail = P._run_results("O", "P", 1)
    assert not ok and results is None and "limit 10000" in detail
    ok, results, detail = P._run_results("O", "P", 1, cap=None)
    assert ok and len(results) == 10001 and not detail


def test_failed_tests_paging_errors_are_not_complete_lists(monkeypatch):
    from tools import pipelines as P
    from urllib.parse import parse_qs, urlparse
    for fail_runs in (False, True):
        def get(url, timeout):
            q = parse_qs(urlparse(url).query)
            skip, top = int(q["$skip"][0]), int(q["$top"][0])
            if "buildUri" in q:
                if fail_runs and skip:
                    return False, None, "run page offline"
                rows = [{"id": i + 1, "name": "UI", "totalTests": 101, "passedTests": 0}
                        for i in range(top if fail_runs else 1)]
            else:
                if skip:
                    return False, None, "result page offline"
                rows = [{"id": i, "testCaseTitle": f"failure_{i}", "outcome": "Failed"}
                        for i in range(1, top + 1)]
            return True, {"value": rows}, ""
        monkeypatch.setattr(P, "_ado_rest_get", get)
        ok, suites, detail = P.get_failed_tests("O", "P", 1)
        assert not ok and suites is None and "page offline" in detail
        if fail_runs:
            ok, summary, detail = P.get_test_summary("O", "P", 1)
            assert not ok and summary is None and "run page offline" in detail


def test_run_results_repeated_or_malformed_page_is_unavailable(monkeypatch):
    from tools import pipelines as P
    for response in ({"value": [{"id": 1, "testCaseTitle": "x"}]}, {},
                     {"value": [None]}, {"value": [{"id": []}]}):
        monkeypatch.setattr(P, "_ado_rest_get", lambda *a: (True, response, ""))
        ok, results, detail = P._run_results("O", "P", 1, page=1, cap=None)
        assert not ok and results is None and "incomplete" in detail


def test_test_api_ids_are_required_positive_and_unique(monkeypatch):
    from tools import pipelines as P
    missing = object()
    for invalid in (missing, None, "", 0, -1, True, False, 1.0, [], {}, "abc", "-1", " 7", "7.0", "\u0667"):
        row = {"testCaseTitle": "x", "outcome": "Passed"}
        if invalid is not missing:
            row["id"] = invalid
        _paged_test_api(monkeypatch, [{"id": 1, "name": "UI", "totalTests": 1}], {1: [row]})
        ok, summary, detail = P.get_test_summary("O", "P", 1)
        assert not ok and summary is None and "id" in detail
        run = {"name": "UI", "totalTests": 0}
        if invalid is not missing:
            run["id"] = invalid
        _paged_test_api(monkeypatch, [run], {})
        ok, summary, detail = P.get_test_summary("O", "P", 1)
        assert not ok and summary is None and "id" in detail

    for second in (7, "7", "007"):
        rows = [{"id": 7, "testCaseTitle": "x", "outcome": "Failed"},
                {"id": second, "testCaseTitle": "x", "outcome": "Passed"}]
        _paged_test_api(monkeypatch, [], {1: rows})
        ok, results, detail = P._run_results("O", "P", 1, page=1, cap=None)
        assert not ok and results is None and "Repeated" in detail
        _paged_test_api(monkeypatch, [
            {"id": 7, "name": "UI", "totalTests": 0},
            {"id": second, "name": "UI", "totalTests": 0}], {})
        ok, runs, detail = P._test_runs("O", "P", 1)
        assert not ok and runs is None and "Repeated" in detail

    _paged_test_api(monkeypatch, [{"id": "007", "name": "UI", "totalTests": 1}], {
        7: [{"id": "002", "testCaseTitle": "x", "outcome": "Passed"}]})
    ok, summary, detail = P.get_test_summary("O", "P", 1)
    assert ok, detail
    assert summary["suites"][0]["test_results"][0]["attempts"] == [
        {"run_id": 7, "result_id": 2, "outcome": "Passed"}]


def test_retry_evidence_and_reports_are_permutation_invariant(monkeypatch):
    import copy
    import itertools
    import json
    from tools import pipelines as P
    from steps.build_verify import rc_report as R, _rc_report_rendering as rendering
    from orchestrator.commands.rc_report import _format

    runs = [
        {"id": 3, "name": "UI B # retry", "totalTests": 2},
        {"id": 2, "name": "UI A # first", "totalTests": 2},
        {"id": 1, "name": "UI B # first", "totalTests": 2},
    ]
    rows = {
        1: [{"id": 1, "testCaseTitle": "recovered", "outcome": "Failed"},
            {"id": 2, "testCaseTitle": "hard_failure", "outcome": "Failed"}],
        2: [{"id": 2, "testCaseTitle": "skipped", "outcome": None},
            {"id": 1, "testCaseTitle": "hard_failure", "outcome": "Failed"}],
        3: [{"id": 2, "testCaseTitle": "hard_failure", "outcome": "Failed"},
            {"id": 1, "testCaseTitle": "recovered", "outcome": "Passed"}],
    }
    baseline = None
    for order, reverse_rows, string_ids in itertools.product(
            itertools.permutations(runs), (False, True), (False, True)):
        incoming_runs, incoming_rows = copy.deepcopy(list(order)), copy.deepcopy(rows)
        if reverse_rows:
            incoming_rows = {key: list(reversed(value)) for key, value in incoming_rows.items()}
        if string_ids:
            for row in incoming_runs:
                row["id"] = str(row["id"])
            for results in incoming_rows.values():
                for row in results:
                    row["id"] = str(row["id"])
        _paged_test_api(monkeypatch, incoming_runs, incoming_rows)
        ok, summary, detail = P.get_test_summary("O", "P", 99)
        assert ok, detail
        assert summary["passed"] == 1 and summary["failed"] == 2
        assert summary["na"] == 1 and summary["total"] == 3
        model = P.assemble_rc_model(
            "2026-09", {"fired": True, "run_id": 10, "when": "2026-09-10T06:00:00Z"},
            {"found": True, "healthy": True, "run_id": 11, "parked": True},
            {p: {"run_id": bid, "complete": True, "ran": 1, "total": 1,
                 "tests": summary, "failed_suites": summary["failed_suites"]}
             for p, bid in (("ECS", 99), ("Local", 100))}, rc=1)
        gate, auth = R.rc_ui_gate(model), R.auth_report_gate(model)
        action = R.rc_next_action(model)
        rendered = (
            json.dumps(summary), json.dumps(gate),
            rendering.rc_email_html(model, {}, gate, auth, action),
            rendering.rc_email_plain(model, {}, gate, auth, action), _format(model))
        if baseline is None:
            baseline = rendered
        assert rendered == baseline
        for run in model["mrwp"].values():
            run["failed_suites"] = list(reversed(run["failed_suites"]))
        assert R.rc_ui_gate(model) == gate
        assert rendering.rc_email_html(model, {}, gate, auth, action) == baseline[2]
        assert rendering.rc_email_plain(model, {}, gate, auth, action) == baseline[3]
        assert _format(model) == baseline[4]


def test_get_failed_tests_non_failed_outcomes_and_missing_title(monkeypatch):
    from tools import pipelines as P
    runs = [{"id": 1, "name": "UI", "totalTests": 4, "passedTests": 1, "notApplicableTests": 1}]
    rows = [{"id": 1, "testCaseTitle": "failed", "outcome": "Failed"},
            {"id": 2, "testCaseTitle": "aborted", "outcome": "Aborted"},
            {"id": 3, "testCaseTitle": "failed", "outcome": "Passed"},
            {"id": 4, "testCaseTitle": "skipped", "outcome": "NotExecuted"}]
    _paged_test_api(monkeypatch, runs, {1: rows})
    ok, suites, _ = P.get_failed_tests("O", "P", 1)
    assert ok and suites[0]["failed"] == 1 and suites[0]["total"] == 2
    assert suites[0]["tests"] == ["aborted"] and suites[0]["recovered"] == ["failed"]
    assert suites[0]["na"] == 1
    rows[0].pop("testCaseTitle")
    ok, suites, detail = P.get_failed_tests("O", "P", 1)
    assert not ok and suites is None and "title unavailable" in detail


def test_pass_any_across_reruns_gate_and_exact_identity(monkeypatch):
    from tools import pipelines as P
    from steps.build_verify import rc_report
    title = "test_1561125_Joined_DeviceIdClaimWPJ"
    rows = {1: [{"id": 1, "testCaseTitle": title, "outcome": "Failed"}],
            2: [{"id": 1, "testCaseTitle": title, "outcome": "Failed"}],
            3: [{"id": 1, "testCaseTitle": title, "outcome": "Passed"}]}
    runs = [{"id": i, "name": f"PROD MSAL - RC Broker (API32) # {i}",
             "totalTests": 1, "passedTests": int(i == 3)} for i in rows]
    _paged_test_api(monkeypatch, runs, rows)
    ok, s, detail = P.get_test_summary("O", "P", 1690357)
    assert ok, detail
    assert (s["total"], s["passed"], s["failed"]) == (1, 1, 0)
    assert s["failed_suites"] == []
    t = s["suites"][0]["test_results"][0]
    assert t["verdict"] == "Passed" and t["recovered"]
    assert t["outcome_counts"] == {"Failed": 2, "Passed": 1}
    assert t["attempts"] == [{"run_id": i, "result_id": 1, "outcome": rows[i][0]["outcome"]}
                             for i in rows]
    m = {"mrwp": {p: {"tests": s, "failed_suites": []} for p in ("ECS", "Local")}}
    gate = rc_report.rc_ui_gate(m)
    assert gate["verdict"] == "clean" and gate["ui_total"] == 2 and gate["ui_failed"] == 0
    # Same case ID does not merge parameterizations, case variants, or other suites/APIs.
    rows[4] = [{"id": 1, "testCaseTitle": title, "outcome": "Failed"},
               {"id": 2, "testCaseTitle": title + "[param]", "outcome": "Failed"},
               {"id": 3, "testCaseTitle": title.lower(), "outcome": "Failed"},
               {"id": 4, "testCaseTitle": "na_only", "outcome": "NotExecuted"}]
    runs.append({"id": 4, "name": "PROD MSAL - RC Broker (API28)", "totalTests": 4})
    ok, s, detail = P.get_test_summary("O", "P", 1690357)
    assert ok, detail
    assert (s["total"], s["passed"], s["failed"], s["na"]) == (4, 1, 3, 1)
    assert len(s["suites"]) == 2
    assert next(v for v in s["suites"] if "API28" in v["name"])["recovered"] == []


def test_reconciliation_scoped_to_build_and_provider(monkeypatch):
    from tools import pipelines as P
    from steps.build_verify import rc_report
    title = "same_title"
    def summary(bid, outcome):
        runs = [{"id": bid, "name": "UI # rerun", "totalTests": 1}]
        _paged_test_api(monkeypatch, runs, {
            bid: [{"id": 1, "testCaseTitle": title, "outcome": outcome}]})
        ok, data, detail = P.get_test_summary("O", "P", bid)
        assert ok, detail
        assert data["build_id"] == bid and data["suites"][0]["run_ids"] == [bid]
        return data
    ecs = summary(1, "Passed")
    local = summary(2, "Failed")
    next_rc = summary(3, "Failed")
    assert ecs["passed"] == 1 and local["failed"] == next_rc["failed"] == 1
    gate = rc_report.rc_ui_gate({"mrwp": {"ECS": {"tests": ecs}, "Local": {"tests": local}}})
    assert gate["ui_total"] == 2 and gate["ui_failed"] == 1 and gate["pass_pct"] == 50


def test_summary_rejects_missing_invalid_or_incomplete_evidence(monkeypatch):
    from tools import pipelines as P
    runs = [{"id": 1, "name": "UI", "totalTests": 1}]
    for rows in ([], [{"id": 1, "testCaseTitle": "x"}],
                 [{"id": 1, "testCaseTitle": "x", "outcome": "Bogus"}],
                 [{"id": 1, "testCaseTitle": "x", "outcome": []}],
                 [{"id": 1, "outcome": "Passed"}]):
        _paged_test_api(monkeypatch, runs, {1: rows})
        for fetch in (P.get_test_summary, P.get_failed_tests):
            ok, data, detail = fetch("O", "P", 1)
            assert not ok and data is None and detail


def test_na_only_and_pass_before_failure():
    from tools import pipelines as P
    rows = [{"testCaseTitle": "na", "outcome": o} for o in P._NA_OUTCOMES]
    rows += [{"testCaseTitle": "pass_first", "outcome": o} for o in ("Passed", "Failed")]
    rows += [{"testCaseTitle": "never_passed", "outcome": o} for o in ("Failed", "Failed")]
    assert P.reconcile_retries(rows) == {
        "passed": 1, "failed": 1, "total": 2, "na": 1, "recovered": ["pass_first"]}
    audit = P.reconcile_retries(rows, include_tests=True)["test_results"][0]
    assert audit["title"] == "na"
    assert sum(audit["outcome_counts"].values()) == len(P._NA_OUTCOMES)
    assert audit["outcome_counts"]["null"] == audit["outcome_counts"]["None"] == 1


def test_auth_firebase_keeps_separate_aggregate_policy(monkeypatch):
    from tools import pipelines as P
    def no_mrwp(*a, **k):
        raise AssertionError("Authenticator must not use MRWP reconciliation")
    monkeypatch.setattr(P, "get_test_summary", no_mrwp)
    name = P.AUTH_UI_SUITES[0]
    calls = _paged_test_api(monkeypatch, [
        {"id": 1, "name": name, "totalTests": 3, "passedTests": 1},
        {"id": 2, "name": "unrelated suite", "totalTests": 100, "passedTests": 100}], {})
    ok, suites, detail = P.auth_ui_suite_rates(123)
    assert ok, detail
    assert suites[name] == {"present": True, "passed": 1, "failed": 2, "total": 3, "pct": 33.3}
    assert not suites[P.AUTH_UI_SUITES[1]]["present"]
    assert len(calls) == 1 and "Build/123" in calls[0]


def test_unit_result_fetch_error_propagates_to_summary_and_failures(monkeypatch):
    from tools import pipelines as P
    monkeypatch.setattr(P, "_ado_rest_get", lambda url, timeout: (
        (True, {"value": [{"id": 1, "name": "sdk_UnitTests", "totalTests": 2, "passedTests": 1}]}, "")
        if "buildUri" in url else (False, None, "unit page offline")))
    for fetch in (P.get_test_summary, P.get_failed_tests):
        ok, data, detail = fetch("O", "P", 1)
        assert not ok and data is None and "unit page offline" in detail
    _paged_test_api(monkeypatch, [
        {"id": 1, "name": "sdk_UnitTests", "totalTests": 1, "passedTests": 0}],
        {1: [{"id": 1, "outcome": "Failed"}]})
    for fetch in (P.get_test_summary, P.get_failed_tests):
        ok, data, detail = fetch("O", "P", 1)
        assert not ok and data is None and "title unavailable" in detail




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
