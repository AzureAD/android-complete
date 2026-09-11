"""Synthetic Authenticator fixtures, captured through the real acquisition boundary."""
from unittest.mock import patch

from tools import pipelines as P


def capture(suites=None, *, rows=None, rc=1, apk=900010, build=900011):
    if rows is None:
        suites = suites or {n: {"present": True, "total": 1, "passed": 1, "failed": 0}
                            for n in P.AUTH_UI_SUITES}
        rows = {}
        for name, s in suites.items():
            if not s["present"]:
                continue
            rows[name] = [(f"test_{1000 + i}_Scenario" if name != P.MONTHLY_REPORT_ONLY
                           else f"monthly_scenario_{i}",
                           "Passed" if i < s["passed"] else
                           "Failed" if i < s["passed"] + s["failed"] else "NotApplicable")
                          for i in range(s["total"])]
    runs, results = [], {}
    for rid, (name, tests) in enumerate(rows.items(), 1):
        runs.append({"id": rid, "name": name, "state": "Completed", "incompleteTests": 0,
                     "build": {"id": build}, "totalTests": len(tests),
                     "passedTests": sum(t[1] == "Passed" for t in tests),
                     "notApplicableTests": sum(t[1] == "NotApplicable" for t in tests)})
        results[rid] = [{"id": i, "testCaseTitle": title, "outcome": outcome}
                        for i, (title, outcome) in enumerate(tests, 1)]
    with patch.object(P, "_auth_test_source_build_id", return_value=apk), patch.object(
            P, "_test_runs", return_value=(True, runs, "")), patch.object(
            P, "_ado_rest_get", side_effect=lambda url, *a:
            (True, next(r for r in runs if str(r["id"]) == url.split("/runs/")[1].split("?")[0]), "")), patch.object(
            P, "_run_results", side_effect=lambda org, project, rid, *a, **k:
            (True, results[rid], "")):
        ok, result, detail = P.collect_auth_ui_evidence(build, apk, rc)
    assert ok, detail
    return result


def auth_test(suites=None, *, rows=None, rc=1, apk=900010, build=900011):
    return {"run_id": str(build), "complete": True,
            **capture(suites, rows=rows, rc=rc, apk=apk, build=build)}


def auth_snapshot(suites=None, *, rows=None, rc=1, apk=900010, build=900011):
    return {"build": {"run_id": str(apk), "rc": rc, "complete": True, "result": "succeeded"},
            "test": auth_test(suites, rows=rows, rc=rc, apk=apk, build=build), "verdict": "clean"}
